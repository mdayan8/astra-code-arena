"""Document -> question drafts. Accepts PDF (OCR for scans), Word, OpenDocument, PowerPoint, RTF, HTML, text/Markdown/code,
structured JSON/CSV/Excel question sets, images (OCR) and ZIP archives of these.
Nothing in an uploaded file is ever executed: we only read its text."""
import csv
import html as html_lib
import io
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import config, db

HEAD = r"(?:^|\n)[ \t]*(?:question|problem|q|prob\.?|task|exercise)[ \t]*[-#:.]?[ \t]*(\d{1,3})\b[^\n]*"
SECTIONS = {
    "statement": r"(?:problem\s+statement|description|statement)",
    "input_format": r"(?:input[ \t]+format|input[ \t]+specification|input)(?=[ \t]*(?::|-|$))",
    "output_format": r"(?:output[ \t]+format|output[ \t]+specification|output)(?=[ \t]*(?::|-|$))",
    "constraints": r"(?:constraints?|limits?)",
    "sample_input": r"(?:sample[ \t]+input|example[ \t]+input|input[ \t]+example)[ \t]*\d*",
    "sample_output": r"(?:sample[ \t]+output|example[ \t]+output|output[ \t]+example)[ \t]*\d*",
    "explanation": r"(?:explanation|note)",
}
SEC_RE = re.compile(r"(?im)^[ \t]*(" + "|".join(f"(?P<{k}>{v})" for k, v in SECTIONS.items()) + r")[ \t]*[:\-]?[ \t]*(?:\n|$)?")


KINDS = {".pdf": "pdf", ".docx": "docx", ".odt": "odt", ".pptx": "pptx", ".rtf": "rtf", ".html": "html", ".htm": "html",
         ".txt": "text", ".md": "text", ".markdown": "text", ".c": "text", ".cpp": "text", ".py": "text", ".java": "text", ".js": "text",
         ".json": "json", ".csv": "csv", ".tsv": "csv", ".xlsx": "xlsx", ".zip": "zip",
         ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image", ".tif": "image", ".tiff": "image", ".bmp": "image", ".gif": "image"}
MEDIA = {"pdf": "application/pdf", "text": "text/plain; charset=utf-8", "json": "text/plain; charset=utf-8", "csv": "text/plain; charset=utf-8",
         "html": "text/plain; charset=utf-8", "rtf": "text/plain; charset=utf-8"}
STRUCTURED = {"json", "csv", "xlsx"}
ZIP_KINDS = {"docx", "odt", "pptx", "xlsx", "zip"}
ACCEPT = ", ".join(sorted(KINDS))


def _is_text(data):
    try:
        data.decode("utf-8-sig")
        return b"\x00" not in data
    except UnicodeDecodeError:
        return False


def validate_upload(data: bytes, filename: str) -> str:
    """Returns the document kind; checks size and the real file signature (not just the extension)."""
    if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise ValueError(f"File is larger than {config.MAX_UPLOAD_MB} MB.")
    ext = Path(filename or "").suffix.lower()
    kind = KINDS.get(ext)
    if not kind:
        raise ValueError("Unsupported file type. Use PDF, Word, OpenDocument, PowerPoint, RTF, HTML, text/Markdown, code, JSON, CSV, Excel, images or a ZIP of these.")
    if kind == "pdf":
        ok = data.startswith(b"%PDF-")
    elif kind in ZIP_KINDS:
        ok = data.startswith(b"PK\x03\x04")
    elif kind == "image":
        ok = any(data.startswith(m) for m in (b"\x89PNG", b"\xff\xd8", b"GIF8", b"BM", b"II*\x00", b"MM\x00*")) or data[8:12] == b"WEBP"
    elif kind == "rtf":
        ok = data.lstrip().startswith(b"{\\rtf")
    elif kind == "json":
        ok = _is_text(data)
        if ok:
            try:
                json.loads(data.decode("utf-8-sig"))
            except ValueError as e:
                raise ValueError(f"The JSON file is not valid: {e}")
    else:
        ok = _is_text(data)
    if not ok:
        raise ValueError(f"The file content does not look like a valid {ext} file.")
    return kind


def validate_pdf(data: bytes):
    validate_upload(data, "x.pdf")


def extract_docx(path: Path):
    import zipfile
    with zipfile.ZipFile(path) as z:
        info = z.getinfo("word/document.xml")
        if info.file_size > 30 * 1024 * 1024:
            raise ValueError("Word document is too large once uncompressed.")
        xml = z.read(info).decode("utf-8", "replace")
    paras = []
    for p in re.findall(r"<w:p[ >].*?</w:p>", xml, re.S):
        p = re.sub(r"<w:tab/>", "\t", p)
        p = re.sub(r"<w:br[^>]*/>", "\n", p)
        txt = "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, re.S))
        paras.append(txt.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'").replace("&amp;", "&"))
    return "\n".join(paras)


def _xml_text(xml, para_tag, text_tag):
    out = []
    for p in re.findall(rf"<{para_tag}[ >].*?</{para_tag}>", xml, re.S):
        p = re.sub(r"<(text:tab|w:tab|a:tab)\s*/>", "\t", p)
        p = re.sub(r"<(text:line-break|w:br|a:br)[^>]*/>", "\n", p)
        txt = "".join(re.findall(rf"<{text_tag}[^>]*>(.*?)</{text_tag}>", p, re.S)) if text_tag else re.sub(r"<[^>]+>", "", p)
        out.append(html_lib.unescape(txt))
    return "\n".join(out)


def _zip_read(z, name, limit=30 * 1024 * 1024):
    info = z.getinfo(name)
    if info.file_size > limit:
        raise ValueError(f"{name} is too large once uncompressed.")
    return z.read(info).decode("utf-8", "replace")


def extract_odt(path):
    import zipfile
    with zipfile.ZipFile(path) as z:
        xml = _zip_read(z, "content.xml")
    return re.sub(r"<[^>]+>", "", _xml_text(xml, "text:(?:p|h)", None))


def extract_pptx(path):
    import zipfile
    with zipfile.ZipFile(path) as z:
        slides = sorted((n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)), key=lambda n: int(re.findall(r"\d+", n)[-1]))
        return "\n\n".join(_xml_text(_zip_read(z, n), "a:p", "a:t") for n in slides[:300])


def extract_rtf(data: str):
    data = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: bytes.fromhex(m.group(1)).decode("latin-1"), data)
    data = re.sub(r"\\(par|line)\b ?", "\n", data)
    data = re.sub(r"\\tab\b ?", "\t", data)
    data = re.sub(r"\{\\\*[^{}]*\}", "", data)
    data = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", data)
    return re.sub(r"[{}]", "", data).replace("\\\\", "\\")


def extract_html(data: str):
    data = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", data)
    data = re.sub(r"(?i)<br\s*/?>|</(p|div|h\d|li|tr|pre)>", "\n", data)
    return html_lib.unescape(re.sub(r"<[^>]+>", "", data))


def extract_any(path: Path, kind: str):
    """-> (pages[list[str]], warnings, page_count) for free-text documents."""
    if kind == "pdf":
        return extract_text(path)
    if kind == "docx":
        return [extract_docx(path)], [], 1
    if kind == "odt":
        return [extract_odt(path)], [], 1
    if kind == "pptx":
        return [extract_pptx(path)], ["Slides were read in order; check that questions spanning several slides were joined correctly."], 1
    raw = None if kind == "image" else path.read_bytes().decode("utf-8-sig", "replace")
    if kind == "rtf":
        return [extract_rtf(raw)], [], 1
    if kind == "html":
        return [extract_html(raw)], [], 1
    if kind in ("text", "json", "csv"):
        return [raw], [], 1
    if not shutil.which("tesseract"):
        return [""], ["OCR (tesseract) is not installed on the server, so images cannot be read."], 1
    r = subprocess.run(["tesseract", str(path), "stdout", "--psm", "4"], capture_output=True, text=True, timeout=180)
    txt = r.stdout
    w = ["Image read with OCR. Check every number and symbol carefully."]
    if len(txt.strip()) < 25:
        w.append("OCR found almost no text (blurry or low-resolution image?).")
    return [txt], w, 1


# ---- structured question sets (JSON / CSV / XLSX) ----
FIELD_ALIASES = {"title": ("title", "name", "question", "problem"), "statement": ("statement", "description", "problem_statement", "body", "text"),
                 "input_format": ("input_format", "input", "inputformat"), "output_format": ("output_format", "output", "outputformat"),
                 "constraints": ("constraints", "constraint", "limits"), "difficulty": ("difficulty", "level"), "category": ("category", "topic"),
                 "tags": ("tags", "tag"), "sample_input": ("sample_input", "example_input"), "sample_output": ("sample_output", "example_output"),
                 "explanation": ("explanation", "note"), "time_limit_ms": ("time_limit_ms", "time_limit"), "memory_mb": ("memory_mb", "memory_limit"),
                 "solution_c": ("solution_c", "reference_c", "c"), "solution_python": ("solution_python", "reference_python", "python"),
                 "qtype": ("qtype", "type", "question_type")}


def _pick(row, key):
    low = {str(k).strip().lower().replace(" ", "_"): v for k, v in row.items()}
    for a in FIELD_ALIASES[key]:
        if a in low and low[a] not in (None, ""):
            return low[a]
    return None


def _nl(x):
    x = "" if x is None else str(x)
    return x if not x or x.endswith("\n") else x + "\n"


def structured_draft(row: dict):
    """One record from JSON/CSV/XLSX -> (payload, warnings)."""
    diff = str(_pick(row, "difficulty") or "Easy").strip().capitalize()
    diff = {"Medium": "Intermediate", "Hard": "Challenging"}.get(diff, diff)
    samples = row.get("samples") if isinstance(row.get("samples"), list) else []
    samples = [{"input": _nl(x.get("input")), "output": _nl(x.get("output", x.get("expected"))), "explanation": str(x.get("explanation") or "")} for x in samples if isinstance(x, dict)]
    if not samples and _pick(row, "sample_input") is not None:
        samples = [{"input": _nl(_pick(row, "sample_input")), "output": _nl(_pick(row, "sample_output")), "explanation": str(_pick(row, "explanation") or "")}]
    tests = row.get("tests") if isinstance(row.get("tests"), list) else []
    tests = [{"input": _nl(t.get("input")), "expected": _nl(t.get("output", t.get("expected"))) if (t.get("output", t.get("expected")) not in (None, "")) else "",
              "kind": t.get("kind") if t.get("kind") in ("normal", "boundary", "edge", "stress") else "normal"} for t in tests if isinstance(t, dict) and t.get("input") is not None]
    ref = row.get("reference") if isinstance(row.get("reference"), dict) else {}
    ref = {k: str(v) for k, v in ref.items() if k in ("c", "cpp", "python", "java", "javascript") and v}
    if _pick(row, "solution_c"):
        ref["c"] = str(_pick(row, "solution_c"))
    if _pick(row, "solution_python"):
        ref["python"] = str(_pick(row, "solution_python"))
    tags = _pick(row, "tags") or []
    tags = [t.strip() for t in (tags if isinstance(tags, list) else str(tags).replace(";", ",").split(",")) if str(t).strip()]
    starter = row.get("starter") if isinstance(row.get("starter"), dict) else {}
    d = {"title": str(_pick(row, "title") or "Untitled")[:150], "statement": str(_pick(row, "statement") or ""), "input_format": str(_pick(row, "input_format") or ""),
         "output_format": str(_pick(row, "output_format") or ""), "constraints": str(_pick(row, "constraints") or ""),
         "difficulty": diff if diff in ("Easy", "Intermediate", "Challenging") else "Easy", "category": str(_pick(row, "category") or "Problem Solving")[:60],
         "tags": tags[:12], "samples": samples, "tests": tests, "reference": ref, "starter": {k: str(v) for k, v in starter.items()},
         "qtype": str(_pick(row, "qtype") or "standard").lower()}
    w = [f"Missing {k.replace('_', ' ')}." for k in ("statement", "input_format", "output_format") if not d[k].strip()]
    if not samples:
        w.append("No sample cases.")
    if not ref:
        w.append("No reference solution: add one so hidden outputs can be computed.")
    return d, w


def read_xlsx_rows(path):
    import zipfile
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in re.findall(r"<si>(.*?)</si>", _zip_read(z, "xl/sharedStrings.xml"), re.S):
                shared.append(html_lib.unescape("".join(re.findall(r"<t[^>]*>(.*?)</t>", si, re.S))))
        sheet = next((n for n in sorted(z.namelist()) if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)), None)
        if not sheet:
            return []
        xml = _zip_read(z, sheet)
    grid = []
    for row in re.findall(r"<row[^>]*>(.*?)</row>", xml, re.S):
        cells = {}
        for ref, attrs, body in re.findall(r'<c r="([A-Z]+)\d+"([^>]*)>(.*?)</c>', row, re.S):
            v = re.search(r"<v>(.*?)</v>", body, re.S)
            isv = re.search(r"<t[^>]*>(.*?)</t>", body, re.S)
            val = shared[int(v.group(1))] if v and 't="s"' in attrs else html_lib.unescape(v.group(1)) if v else html_lib.unescape(isv.group(1)) if isv else ""
            col = 0
            for ch in ref:
                col = col * 26 + ord(ch) - 64
            cells[col - 1] = val
        if cells:
            grid.append([cells.get(i, "") for i in range(max(cells) + 1)])
    if not grid:
        return []
    head = [str(h).strip() for h in grid[0]]
    return [dict(zip(head, r)) for r in grid[1:] if any(str(x).strip() for x in r)]


def structured_rows(path: Path, kind: str):
    if kind == "json":
        data = json.loads(path.read_bytes().decode("utf-8-sig"))
        if isinstance(data, dict):
            data = data.get("questions") or data.get("problems") or [data]
        return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []
    if kind == "csv":
        txt = path.read_bytes().decode("utf-8-sig", "replace")
        dialect = "excel-tab" if path.suffix.lower() == ".tsv" or txt.split("\n", 1)[0].count("\t") > txt.split("\n", 1)[0].count(",") else "excel"
        return list(csv.DictReader(io.StringIO(txt), dialect=dialect))
    return read_xlsx_rows(path)


def expand_zip(path: Path, workdir: Path):
    """Safely unpack a ZIP (no path traversal, size + count limits). Returns [(Path, kind)]."""
    import zipfile
    out, total = [], 0
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            name = Path(info.filename).name
            kind = KINDS.get(Path(name).suffix.lower())
            if info.is_dir() or not name or name.startswith((".", "__")) or not kind or kind == "zip":
                continue
            total += info.file_size
            if total > 80 * 1024 * 1024 or len(out) >= 60:
                break
            safe = re.sub(r"[^\w.\-]", "_", name)
            dest = workdir / f"{len(out):03d}-{safe}"
            dest.write_bytes(z.read(info))
            out.append((dest, kind))
    return out


def extract_text(path: Path):
    import pymupdf as fitz
    warnings, pages = [], []
    doc = fitz.open(path)
    if doc.needs_pass:
        raise ValueError("The PDF is password protected.")
    has_ocr = bool(shutil.which("tesseract"))
    for i, page in enumerate(doc):
        txt = page.get_text("text", sort=True) or ""
        if len(txt.strip()) < 25:
            if has_ocr:
                txt = ocr_page(page)
                if len(txt.strip()) < 25:
                    warnings.append(f"Page {i + 1}: OCR found almost no text (poor scan quality?).")
                else:
                    warnings.append(f"Page {i + 1}: scanned page, text recovered with OCR. Check it carefully.")
            else:
                warnings.append(f"Page {i + 1}: no text layer and OCR (tesseract) is not installed on the server.")
        if page.find_tables and _has_table(page):
            warnings.append(f"Page {i + 1}: contains a table; check row/column layout in the extracted text.")
        if re.search(r"[∑∏√≤≥≠∈∀∃^]|\\frac|\\sum", txt):
            warnings.append(f"Page {i + 1}: mathematical notation detected; verify symbols survived extraction.")
        pages.append(txt)
    n = len(doc)
    doc.close()
    return pages, warnings, n


def _has_table(page):
    try:
        return len(page.find_tables().tables) > 0
    except Exception:
        return False


def ocr_page(page):
    with tempfile.TemporaryDirectory() as d:
        img = Path(d) / "p.png"
        page.get_pixmap(dpi=300).save(img)
        try:
            r = subprocess.run(["tesseract", str(img), "stdout", "--psm", "4"], capture_output=True, text=True, timeout=120)
            return r.stdout
        except (subprocess.SubprocessError, OSError):
            return ""


NUMBERED = re.compile(r"(?im)^[ \t]*(?:(?:question|problem|prob\.?|task|exercise|q)[ \t]*[-#:.]?[ \t]*)?\(?(\d{1,3})[ \t]*[.):\]-][ \t]*(?=\S)")


def numbered_starts(text):
    """Start offsets of questions numbered 1, 2, 3 ... (with or without 'Q'/'Question'). Only the longest run that counts up
    by one is used, so numbered steps or options inside a question do not split it."""
    cands = [(int(m.group(1)), m.start()) for m in NUMBERED.finditer(text)]
    best = []
    for i, (n, pos) in enumerate(cands):
        run, want = [pos], n + 1
        for n2, pos2 in cands[i + 1:]:
            if n2 == want:
                run.append(pos2)
                want += 1
        if len(run) > len(best):
            best = run
    return best if len(best) >= 2 else []


def split_questions(text: str):
    """Find question boundaries: headings like 'Question 1', 'Problem 2:', 'Q3.' or plain numbering '1.', '2)', ..."""
    marks = [m for m in re.finditer(HEAD, text, re.I)]
    starts = numbered_starts(text)
    if len(marks) < 2 and len(starts) > len(marks):   # no 'Question N' headings: use plain numbering 1. 2) 3. ...
        bounds = [(a, starts[i + 1] if i + 1 < len(starts) else len(text)) for i, a in enumerate(starts)]
        return [(a, b) for a, b in bounds if len(text[a:b].strip()) > 10] or [(0, len(text))]
    if not marks:
        return [(0, len(text))]
    bounds = []
    for i, m in enumerate(marks):
        start = m.start() if text[m.start()] != "\n" else m.start() + 1
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        bounds.append((start, end))
    return [(a, b) for a, b in bounds if len(text[a:b].strip()) > 40] or [(0, len(text))]


OPT_LINE = re.compile(r"(?im)^[ \t]*\(?([A-Da-d])[\).:\]][ \t]+(.+?)[ \t]*$")
OPT_INLINE = re.compile(r"\(([A-Da-d])\)\s*(.+?)(?=\s*\([A-Da-d]\)|$)", re.S)
ANSWER = re.compile(r"(?im)^[ \t]*(?:correct[ \t]+)?(?:answer|ans|key)[ \t]*[:.\-=)]*[ \t]*\(?([A-Da-d])?\)?[ \t]*[).:\-]?[ \t]*(.*)$")


def find_mcq(block: str):
    """A multiple-choice question as written in a paper: stem, options (A-D, on lines or inline) and the answer if given.
    -> dict or None when the block has no options."""
    text = block.strip()
    ans = ANSWER.search(text)
    body = text[:ans.start()] if ans else text
    opts = list(OPT_LINE.finditer(body))
    if len(opts) >= 2:
        stem = body[:opts[0].start()]
        options = [m.group(2).strip() for m in opts]
        letters = [m.group(1).upper() for m in opts]
    else:
        first = re.search(r"\([Aa]\)", body)
        inl = list(OPT_INLINE.finditer(body[first.start():])) if first else []
        if len(inl) < 2:
            return None
        stem = body[:first.start()]
        options = [m.group(2).strip().rstrip(",;") for m in inl]
        letters = [m.group(1).upper() for m in inl]
    stem = re.sub(r"^\s*(?:(?:question|q)\s*[-#:.]?\s*)?\d{1,3}\s*[.):\]]\s*", "", stem.strip(), flags=re.I).strip()
    answer = None
    if ans:
        letter, rest = (ans.group(1) or "").upper(), (ans.group(2) or "").strip()
        if letter in letters:
            answer = options[letters.index(letter)]
        elif rest:
            answer = next((o for o in options if o.strip().lower() == rest.lower()), None)
    if not stem or len(set(options)) < 2:
        return None
    return {"statement": stem, "options": options[:6], "answer": answer}


FILE_MARK = "===== FILE:"


def find_questions(text: str):
    """Every question in a document (or several joined with FILE_MARK lines), in order. Multiple choice is recognised with
    its options and answer; anything else is kept as written for review."""
    if FILE_MARK in text:   # several files: numbering restarts in each, so each file is split on its own
        return [q for part in re.split(r"(?m)^" + re.escape(FILE_MARK) + r".*$", text) if part.strip() for q in find_questions(part)]
    out = []
    for a, b in split_questions(text):
        block = text[a:b].strip()
        if not block:
            continue
        mcq = find_mcq(block)
        if mcq:
            out.append({"kind": "mcq", **mcq, "raw": block[:6000]})
        else:
            out.append({"kind": "other", "raw": block[:6000]})
    return out


def parse_block(block: str):
    lines = block.strip().split("\n")
    head = lines[0].strip()
    title = re.sub(r"(?i)^(question|problem|q|prob\.?|task|exercise)\s*[-#:.]?\s*\d+\s*[:.)\-]?\s*", "", head)
    title = re.sub(r"^\d+\s*[.)]\s*", "", title).strip(" :-") or head
    body = "\n".join(lines[1:])
    parts, last, key = {}, 0, "statement"
    for m in SEC_RE.finditer(body):
        parts.setdefault(key, []).append(body[last:m.start()])
        key = next(k for k, v in m.groupdict().items() if v)
        last = m.end()
    parts.setdefault(key, []).append(body[last:])
    j = {k: [x.strip() for x in v if x.strip()] for k, v in parts.items()}
    s_in, s_out = j.get("sample_input", []), j.get("sample_output", [])
    samples = [{"input": a + "\n", "output": b + "\n", "explanation": ""} for a, b in zip(s_in, s_out)]
    if samples and j.get("explanation"):
        samples[0]["explanation"] = "\n".join(j["explanation"])
    d = {"title": title[:150], "statement": "\n\n".join(j.get("statement", [])), "input_format": "\n".join(j.get("input_format", [])),
         "output_format": "\n".join(j.get("output_format", [])), "constraints": "\n".join(j.get("constraints", [])),
         "samples": samples, "tests": [], "reference": {}, "difficulty": "Easy", "category": "Problem Solving", "tags": []}
    warn = []
    for k in ("statement", "input_format", "output_format"):
        if not d[k]:
            warn.append(f"Missing {k.replace('_', ' ')}.")
    if not samples:
        warn.append("No sample input/output pair detected.")
    if len(s_in) != len(s_out):
        warn.append(f"{len(s_in)} sample inputs but {len(s_out)} sample outputs.")
    if re.search(r"�|[^\x00-\x7F]{6,}", block):
        warn.append("Unusual characters found; possible poor OCR or font encoding.")
    return d, warn


def _ingest_one(c, doc, path, kind, label=""):
    """Returns (text_for_preview, warnings, page_count, drafts_created)."""
    tag = f"{label}: " if label else ""
    if kind in STRUCTURED:
        rows = structured_rows(path, kind)
        n = 0
        for r in rows[:500]:
            d, w = structured_draft(r)
            c.execute("INSERT INTO generated_question_drafts(source,document_id,status,payload,warnings,created_by,created_at) VALUES('pdf',?,'pending',?,?,?,?)",
                      (doc["id"], json.dumps(d), json.dumps(w), doc["uploaded_by"], db.now()))
            n += 1
        return f"[{label or path.name}] {n} structured question record(s)\n", [f"{tag}{n} question(s) read from {kind.upper()} fields."] if n else [f"{tag}no question records found."], 1, n
    pages, warnings, npages = extract_any(path, kind)
    text = "\n".join(pages)
    clean = re.sub(r"\n\[\[page \d+\]\]\n", "\n", text)
    n = 0
    if clean.strip():
        for a, b in split_questions(clean):
            d, w = parse_block(clean[a:b])
            d["raw_text"] = clean[a:b][:8000]
            c.execute("INSERT INTO generated_question_drafts(source,document_id,status,payload,warnings,created_by,created_at) VALUES('pdf',?,'pending',?,?,?,?)",
                      (doc["id"], json.dumps(d), json.dumps(w), doc["uploaded_by"], db.now()))
            n += 1
    return clean, [tag + w for w in warnings], npages, n


def process(doc_id: int):
    """Runs in a background thread. Creates one draft per detected question (or per structured record)."""
    c = db.connect()
    try:
        doc = db.one(c, "SELECT * FROM uploaded_documents WHERE id=?", doc_id)
        path = config.UPLOAD_DIR / doc["stored_name"]
        kind = KINDS.get(path.suffix.lower(), "pdf")
        texts, warnings, pages, drafts = [], [], 0, 0
        with tempfile.TemporaryDirectory() as tmp:
            items = expand_zip(path, Path(tmp)) if kind == "zip" else [(path, kind)]
            if kind == "zip":
                warnings.append(f"ZIP archive: {len(items)} supported file(s) inside.")
            with db.tx(c):
                for p, k in items:
                    try:
                        t, w, n, d = _ingest_one(c, doc, p, k, p.name.split("-", 1)[-1] if kind == "zip" else "")
                    except Exception as e:  # one bad file inside a ZIP must not stop the rest
                        t, w, n, d = "", [f"{p.name}: could not be read ({e})."], 0, 0
                    texts.append(t)
                    warnings += w
                    pages += n
                    drafts += d
                warnings.append(f"Detected {drafts} question(s).")
                c.execute("UPDATE uploaded_documents SET status='done', pages=?, extracted_text=?, warnings=? WHERE id=?",
                          (pages, "\n".join(texts)[:2_000_000], json.dumps(warnings), doc_id))
    except Exception as e:
        c.execute("UPDATE uploaded_documents SET status='failed', warnings=? WHERE id=?", (json.dumps([f"Extraction failed: {e}"]), doc_id))
        db.log_error(c, "import", e)
    finally:
        c.close()


if __name__ == "__main__":
    paper = """C Programming Quiz - Unit 2
1. What is the size of int on a 32-bit compiler?
A) 2 bytes
B) 4 bytes
C) 8 bytes
D) 1 byte
Answer: B
2) Which loop runs at least once?
(a) for (b) while (c) do-while (d) none
Ans: c
3. Which header declares printf?
a. stdlib.h
b. stdio.h
c. string.h
d. math.h
4. Write a program to reverse a number.
Input: 123  Output: 321
5. What does 7 % 3 give?
A) 1
B) 2
C) 3
D) 0
Answer: 1
"""
    qs = find_questions(paper)
    assert len(qs) == 5, [q["raw"][:30] for q in qs]
    assert qs[0]["kind"] == "mcq" and qs[0]["answer"] == "4 bytes" and qs[0]["statement"].startswith("What is the size"), qs[0]
    assert qs[1]["kind"] == "mcq" and qs[1]["options"] == ["for", "while", "do-while", "none"] and qs[1]["answer"] == "do-while", qs[1]
    assert qs[2]["kind"] == "mcq" and qs[2]["answer"] is None and len(qs[2]["options"]) == 4, qs[2]
    assert qs[3]["kind"] == "other" and "reverse" in qs[3]["raw"]
    assert qs[4]["answer"] == "1", qs[4]   # 'Answer: 1' is the option text, not a letter
    steps = "1. Read n.\n2. Loop i from 1 to n.\n3. Print the sum.\nSample: 3 -> 6"
    assert len(find_questions("Problem 1: Sum\n" + steps + "\n\nProblem 2: Max\nRead two numbers and print the larger one.")) == 2
    print("pdfimport self-check ok")
