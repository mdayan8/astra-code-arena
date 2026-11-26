"""Optional AI assistant. Any OpenAI-compatible /chat/completions endpoint (Ollama, LM Studio, vLLM, OpenAI...).
Keys live only in server env. Everything the model returns becomes a *draft*; expected outputs are always
recomputed by running the reference solution in the sandbox, never trusted from the model."""
import json
import random
import re
import time
import urllib.error
import urllib.request

from . import config, db

SYSTEM = ("You are an expert competitive-programming problem setter for first and second year engineering students. "
          "You always reply with a single valid JSON object and nothing else.")

QUESTION_SCHEMA = """{
 "title": str, "category": str, "difficulty": "Easy"|"Intermediate"|"Challenging", "tags": [str],
 "statement": str, "input_format": str, "output_format": str, "constraints": str,
 "samples": [{"input": str, "output": str, "explanation": str}],
 "reference": {"c": str, "python": str},
 "tests": [{"input": str, "kind": "normal"|"boundary"|"edge"|"stress", "description": str}]
}"""


OLLAMA = "http://127.0.0.1:11434/v1"   # used when nothing is configured: any model installed in Ollama just works
_state = {"at": 0.0, "v": None}


def base():
    return (config.AI_BASE_URL or OLLAMA).rstrip("/")


def choose(models, want):
    """The organiser's choice if it is installed, else the closest installed model. Never a model that is not there."""
    models = [m for m in models if "embed" not in m.lower()]
    if not models:
        return want   # endpoints that do not list models (some hosted providers): trust the configured name
    if want in models:
        return want
    near = [m for m in models if want and m.split(":")[0] == want.split(":")[0]]
    coder = [m for m in models if "coder" in m.lower() or "code" in m.lower()]
    return (near or coder or models)[0]


def status(fresh=True):
    """Looks at what is really installed, at most every 20 s, so swapping or adding an Ollama model needs no restart."""
    if not fresh and _state["v"] and time.time() - _state["at"] < 20:
        return _state["v"]
    want = ""
    try:
        c = db.connect()
        try:
            want = db.setting(c, "ai_model", "")
        finally:
            c.close()
    except Exception:
        pass
    want = want or config.AI_MODEL
    try:
        req = urllib.request.Request(base() + "/models", headers=_headers())
        with urllib.request.urlopen(req, timeout=3) as r:
            models = sorted(str(m.get("id")) for m in (json.loads(r.read()).get("data") or []) if isinstance(m, dict) and m.get("id"))
        model = choose(models, want)
        v = ({"enabled": True, "reachable": True, "model": model, "models": [m for m in models if "embed" not in m.lower()][:60], "auto": model != want}
             if model else {"enabled": False, "models": [], "reason": "The AI service is running but no model is installed yet. Install one from the ASTRA Code Arena app (Install AI model)."})
    except Exception as e:
        v = ({"enabled": True, "reachable": False, "model": want, "models": [], "reason": f"AI endpoint not reachable: {e}"} if config.AI_BASE_URL else
             {"enabled": False, "models": [], "reason": "No AI model is installed on this computer. Everything else works without it. To add one, open the ASTRA Code Arena app and press Install AI model."})
    _state.update(at=time.time(), v=v)
    return v


def enabled():
    v = status(False)
    return bool(v["enabled"] and v.get("reachable") is not False)   # never start a job the model cannot answer


def model():
    return status(False).get("model") or config.AI_MODEL


def _headers():
    h = {"Content-Type": "application/json"}
    if config.AI_API_KEY:
        h["Authorization"] = f"Bearer {config.AI_API_KEY}"
    return h


import threading as _th

_SLOTS = _th.BoundedSemaphore(max(1, config.AI_CONCURRENCY))


class AIBusy(Exception):
    pass


def chat_text(system: str, user: str, max_tokens: int = 700, temperature: float = 0.3) -> str:
    """Free-text answer (markdown) for student-facing tools. Concurrency-limited so a small server never overloads."""
    if not _SLOTS.acquire(timeout=45):
        raise AIBusy("The AI assistant is busy. Try again in a minute.")
    try:
        body = {"model": model(), "temperature": temperature, "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user + ("\n/no_think" if "qwen3" in model() else "")}]}
        req = urllib.request.Request(base() + "/chat/completions", data=json.dumps(body).encode(), headers=_headers())
        with urllib.request.urlopen(req, timeout=min(config.AI_TIMEOUT, 120)) as r:
            data = json.loads(r.read())
        return re.sub(r"<think>.*?</think>", "", data["choices"][0]["message"]["content"] or "", flags=re.S).strip()
    finally:
        _SLOTS.release()


def chat(prompt: str) -> dict:
    if not _SLOTS.acquire(timeout=600):
        raise AIBusy("AI busy")
    try:
        return _chat(prompt)
    finally:
        _SLOTS.release()


def _chat(prompt: str) -> dict:
    body = {"model": model(), "temperature": 0.4, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt + ("\n/no_think" if "qwen3" in model() else "")}]}
    req = urllib.request.Request(base() + "/chat/completions", data=json.dumps(body).encode(), headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=config.AI_TIMEOUT) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"AI provider returned {e.code}: {e.read()[:300].decode('utf-8', 'replace')}")
    text = re.sub(r"<think>.*?</think>", "", data["choices"][0]["message"]["content"] or "", flags=re.S)
    return parse_json(text)


def parse_json(text: str) -> dict:
    """Small models often emit almost-JSON: code fences, trailing commas, raw newlines in strings. Repair before giving up."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", text, re.S)
    candidates = [text] + ([m.group(0)] if m else [])
    for cand in candidates:
        for fix in (lambda t: t, lambda t: re.sub(r",\s*([}\]])", r"\1", t)):
            try:
                return json.loads(fix(cand), strict=False)
            except ValueError:
                continue
    raise RuntimeError("The AI reply was not valid JSON.")


def fix_code(lang: str, code: str) -> str:
    """Small models put real newlines inside string literals when they meant "\\n" (a JSON escaping slip). Repair that."""
    out, q, i = [], None, 0
    code = code.replace("\r\n", "\n")
    while i < len(code):
        ch = code[i]
        if q:
            if ch == "\\" and i + 1 < len(code):
                out.append(code[i:i + 2]); i += 2; continue
            if ch == q:
                q = None
            elif ch == "\n":
                out.append("\\n"); i += 1; continue
        elif ch in ('"', "'") and not (lang == "python" and code[i:i + 3] in ('"""', "'''")):
            q = ch
        elif lang == "python" and code[i:i + 3] in ('"""', "'''"):
            j = code.find(code[i:i + 3], i + 3)
            j = len(code) if j < 0 else j + 3
            out.append(code[i:j]); i = j; continue
        elif ch == "/" and code[i:i + 2] == "//":
            j = code.find("\n", i); j = len(code) if j < 0 else j
            out.append(code[i:j]); i = j; continue
        elif ch == "#" and lang == "python":
            j = code.find("\n", i); j = len(code) if j < 0 else j
            out.append(code[i:j]); i = j; continue
        out.append(ch); i += 1
    return "".join(out)


def looks_like_code(lang: str, code: str) -> bool:
    c = code or ""
    if lang in ("c", "cpp"):
        return "main" in c and ("#include" in c or "scanf" in c or "printf" in c)
    if lang == "python":
        return ("input" in c or "stdin" in c) and "print" in c
    return len(c.strip()) > 10


def real_input(x):
    """Drops placeholder tests like '2 4 6 ... (1000 even numbers)': a program can only read real values."""
    t = str(x)
    return bool(t.strip()) and not re.search(r"\.\.\.|…|\(\s*\d+\s+\w+.*\)|\b(?:etc|repeated|times|numbers|elements|values)\b", t, re.I)


def normalize_question(d: dict) -> tuple[dict, list]:
    warn = []
    q = {k: str(d.get(k) or "") for k in ("title", "statement", "input_format", "output_format", "constraints")}
    q["category"] = str(d.get("category") or "Problem Solving")
    q["difficulty"] = d.get("difficulty") if d.get("difficulty") in ("Easy", "Intermediate", "Challenging") else "Easy"
    q["tags"] = [str(t) for t in (d.get("tags") or [])][:8]
    q["samples"] = [{"input": _nl(s.get("input")), "output": _nl(s.get("output")), "explanation": str(s.get("explanation") or "")}
                    for s in (d.get("samples") or []) if isinstance(s, dict)]
    ref = d.get("reference") or {}
    q["reference"] = {k: fix_code(k, str(v)) for k, v in ref.items() if k in ("c", "python", "cpp", "java") and v and looks_like_code(k, str(v))}
    q["tests"] = [{"input": _nl(t.get("input")), "kind": t.get("kind") if t.get("kind") in ("normal", "boundary", "edge", "stress") else "normal",
                   "description": str(t.get("description") or "")} for t in (d.get("tests") or []) if isinstance(t, dict) and t.get("input") is not None and real_input(t.get("input"))]
    for k in ("title", "statement", "input_format", "output_format"):
        if not q[k].strip():
            warn.append(f"AI left the {k.replace('_', ' ')} empty.")
    if not q["reference"]:
        warn.append("No reference solution generated; write one before validating.")
    if not q["samples"]:
        warn.append("No sample cases generated.")
    warn.append("AI draft: sample outputs will be re-checked and all hidden expected outputs recomputed by the reference solution in the sandbox.")
    return q, warn


def _nl(s):
    s = "" if s is None else str(s)
    return s if s.endswith("\n") or not s else s + "\n"


def ref_block(p):
    """The organiser's reference document (notes, a syllabus, an old question paper): questions follow its topics and style."""
    ref = (p.get("reference") or "").strip()
    if not ref:
        return ""
    return ("\nReference material from the organiser (base the questions on the topics, concepts and style in it; if it contains questions, "
            "write NEW questions of the same kind and level instead of copying them):\n<<<\n" + ref[:4000] + "\n>>>")


def chunk(text, i, size=3500):
    """Different part of a long reference for each question, so a set covers the whole document."""
    text = (text or "").strip()
    if len(text) <= size:
        return text
    parts = [text[k:k + size] for k in range(0, len(text), size)]
    return parts[i % len(parts)]


MUTATIONS = [(r"<=", "<"), (r"(?<![<>=!])<(?![<=])", "<="), (r">=", ">"), (r"(?<![<>=!-])>(?![>=])", ">="), (r"==", "!="),
             (r"\+\+", "--"), (r"\+ *1\b", "+ 2"), (r"- *1\b", ""), (r"\b0\b", "1"), (r"\+=", "-="), (r"&&", "||"), (r"%", "/"), (r"= *1\b(?!\.)", "= 0"),
             (r"(?<![+])\+(?![+=])", "-"), (r"(?<![-])-(?![-=>])", "+"), (r"(?<![*/])\*(?![=/])", "+"), (r"\[\s*i\s*\]", "[i + 1]"),
             (r"(?<![<>=!-])>(?![>=])", "<"), (r"(?<![<>=!])<(?![<=])", ">"), (r"!=", "==")]


def mutants(code):
    """Small one-place bugs a student can find by reading carefully (programmatic, so they always exist)."""
    lines = code.split("\n")
    out = []
    for i, line in enumerate(lines):
        s = line.strip()
        if not s or s.startswith(("#", "//", "/*", "*")):
            continue
        quoted = [m.span() for m in re.finditer(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', line)]   # never inside "..." or '.'
        for rx, rep in MUTATIONS:
            for m in re.finditer(rx, line):
                if any(a <= m.start() < b for a, b in quoted) or re.search(r"return\s*$", line[:m.start()]):
                    continue   # inside a string, or the exit code (that would only crash, not teach)
                new = line[:m.start()] + rep + line[m.end():]
                if new != line:
                    out.append("\n".join(lines[:i] + [new] + lines[i + 1:]))
    return out


def judge_all(c, qid, vid, user_id, code, lang="c", timeout=120):
    """Runs code against every test of the question (admin run). -> verdict or None."""
    sid = c.execute("INSERT INTO submissions(competition_id,user_id,question_id,version_id,kind,language,code,created_at) VALUES(NULL,?,?,?,'run',?,?,?)",
                    (user_id, qid, vid, lang, code, db.now())).lastrowid
    t = time.time()
    try:
        while time.time() - t < timeout:
            r = db.one(c, "SELECT status, verdict FROM submissions WHERE id=?", sid)
            if r and r["status"] == "done":
                return r["verdict"]
            time.sleep(1)
        return None
    finally:
        c.execute("DELETE FROM submissions WHERE id=?", (sid,))


def make_debug(c, qid, user_id, log):
    """Turns a checked coding problem into 'Fix the bug': the editor starts with the reference C program carrying one small bug.
    The bug is accepted only if the buggy program compiles and the server sees it fail at least one test."""
    from .api_admin import editable_version
    v = db.one(c, "SELECT q.latest_version_id AS vid, r.code FROM questions q JOIN reference_solutions r ON r.version_id=q.latest_version_id AND r.language='c' WHERE q.id=?", qid)
    if not v:
        log.append("Fix the bug: there is no C solution to put a bug into, so it stays a normal coding problem.")
        return False
    ref = v["code"]
    cands = []
    try:   # first ask the model for a natural-looking bug, then fall back to small automatic changes
        d = chat(f"""Here is a correct C program:\n{ref}\nIntroduce exactly ONE small logical bug (for example a wrong loop bound, a wrong comparison or a wrong initial value) that a student can find by reading the code.
The program must still compile. Do not add comments that reveal the bug. Return JSON: {{"code": str}}""")
        b = fix_code("c", str(d.get("code") or ""))
        if looks_like_code("c", b) and b.strip() != ref.strip():
            cands.append(b)
    except Exception:
        pass
    muts = mutants(ref)
    random.shuffle(muts)
    cands += muts[:12]
    for b in cands:
        verdict = judge_all(c, qid, v["vid"], user_id, b)
        if verdict in ("WA", "RE"):
            with db.tx(c):
                vid, _ = editable_version(c, qid, {"id": user_id})
                c.execute("UPDATE question_versions SET qtype='debug', starter=?, languages=? WHERE id=?", (json.dumps({"c": b}), json.dumps(["c"]), vid))
            log.append(f"Fix the bug: the starter program has one bug; the server checked that it compiles and fails a test ({verdict}).")
            return True
    log.append("Fix the bug: no bug could be checked automatically, so it stays a normal coding problem.")
    return False


LEVEL_GUIDE = {
    "Easy": "Easy = a first-year student solves it in 10-15 minutes with one loop or a few conditions (for example: count, sum, reverse, maximum, check even). No advanced algorithms.",
    "Intermediate": "Moderate = needs two ideas together, such as nested loops, sorting then scanning, a frequency count or a running sum. Solvable in 20-30 minutes.",
    "Challenging": "Hard = needs a clear algorithm (two pointers, binary search, hashing, recursion or simple dynamic programming), still solvable in 40 minutes.",
}


def generate_question(p: dict) -> dict:
    prompt = f"""Create one ORIGINAL programming problem.
Topic: {p.get('topic')}
Difficulty: {p.get('difficulty')}. {LEVEL_GUIDE.get(p.get('difficulty') or 'Easy', '')} Respect the difficulty strictly.{ref_block(p)}
Target language for reference solution: {p.get('language') or 'C and Python'}
Style: {p.get('style') or 'short real-world story, clear and unambiguous'}
Constraints guidance: {p.get('constraints') or ('n up to 1000, values fit in int' if (p.get('difficulty') or 'Easy') == 'Easy' else 'small enough for O(n log n)')}
Number of sample cases: {p.get('samples') or (3 if p.get('tests') is False else 2)}
{'Also give 8-12 hidden test inputs covering normal, boundary (minimum and maximum), edge and duplicate-value cases.' if p.get('tests') is not False else 'The organiser wants SIMPLE input/output only: give the sample cases (each a small, realistic input with its output) and leave "tests" empty.'}
Rules for reference solutions (they are executed automatically and must agree with each other):
- Each one is a COMPLETE program: it reads the whole input from standard input exactly as the input format says and prints only the answer to standard output.
- The Python one must be a full script that reads input (e.g. sys.stdin) and prints; never just a function.
- Sample outputs must be exactly what the reference prints for the sample inputs.
- Test inputs must follow the input format exactly (if the first line is n, include it).
Return JSON exactly in this shape:
{QUESTION_SCHEMA}"""
    return chat(prompt)


HEADERS = "#include <stdio.h>\n#include <string.h>\n#include <stdlib.h>\n"
_LABEL = re.compile(r"^\s*(?:\(?[A-Da-d]\)\s*|[A-Da-d][.:]\s+|option\s+[A-Da-d]\s*[:.)-]?\s*)", re.I)


def clean_option(o):
    """'B) 1 2 3' -> '1 2 3' (models often label their options; the page labels them already)."""
    return _LABEL.sub("", str(o)).strip()[:300]


def complete_c(code):
    """Small models forget the #include lines; without them the program does not compile and nothing can be checked."""
    code = code.strip()
    if "#include" not in code:
        code = HEADERS + code
    if "main" not in code:
        code += "\nint main(void){return 0;}"
    return code + "\n"


def generate_mcq(p: dict) -> list:
    """Multiple-choice questions. style 'output': each shows a short C program and asks what it prints; the server runs it to get
    the true answer. Otherwise concept / logic questions; for anything computable the model also writes a tiny Python check that
    the server runs, so the marked answer is proved instead of trusted."""
    n = max(1, min(int(p.get("count") or 5), 10))
    output = p.get("style") == "output"
    example = ('{"title": "Loop with a skip", "question": "What does this program print?", "code": "#include <stdio.h>\\nint main(void) {\\n    for (int i = 1; i <= 5; i++) {\\n        if (i == 3) continue;\\n        printf(\\"%d \\", i);\\n    }\\n    return 0;\\n}", '
               '"options": ["1 2 4 5", "1 2 3 4 5", "1 2", "3"], "answer": 0, "check": "", "explanation": "continue skips 3."}') if output else (
               '{"title": "Number series", "question": "What comes next: 2, 6, 12, 20, 30, ?", "code": "", "options": ["42", "40", "36", "44"], "answer": 0, '
               '"check": "s=[2,6,12,20,30]\\nprint(s[-1]+(s[-1]-s[-2])+2)", "explanation": "Differences grow by 2: 4, 6, 8, 10, 12."}')
    prompt = f"""Write {n} multiple-choice questions for engineering students preparing for campus placement tests.
Topic: {p.get('topic')}
Difficulty: {p.get('difficulty') or 'Easy'}. Short to read, but the student must think. All {n} questions must be different from each other.{ref_block(p)}
{'Each question shows one short, complete C program in "code" (with #include <stdio.h> and int main, no input, no undefined behaviour) and asks what it prints. The options are possible outputs.' if output else 'Each is a short concept, logic or aptitude question. If the answer can be calculated, put in "check" a tiny Python 3 program that prints the correct answer exactly as it is written in the options; otherwise leave "check" empty.'}
Rules: exactly 4 options, exactly one correct, all different, plain text without letters like "A)" in front, no "all of the above". "answer" is the position 0-3 of the correct option.
Example of the FORMAT only (do not reuse its content or numbers; every question must be new and different from the others):
{example}
Return JSON: {{"questions": [ ...{n} items like the example... ]}}"""
    out, seen = [], set()
    for k, q in enumerate(chat(prompt).get("questions") or []):
        if not isinstance(q, dict) or not str(q.get("question") or "").strip():
            continue
        raw = [clean_option(o) for o in (q.get("options") or []) if clean_option(o)]
        try:
            ans = int(q.get("answer"))
            marked = raw[ans]
        except (TypeError, ValueError, IndexError):
            continue
        opts = list(dict.fromkeys(raw))
        raw_code = str(q.get("code") or "").strip()
        if raw_code and not output and not looks_like_code("c", raw_code):   # a Python calculation put in the wrong field
            q["check"], raw_code = q.get("check") or raw_code, ""
        code = complete_c(fix_code("c", raw_code)) if raw_code else ""
        if output and (not code or "Loop with a skip" in str(q.get("title"))):   # missing program, or the example copied back
            continue
        if len(opts) < (3 if code else 4):   # a program is run to get the truth, so one duplicate option can be replaced; otherwise drop it
            continue
        if "2, 6, 12, 20, 30" in q["question"] or "Loop with a skip" in str(q.get("title")):
            continue   # the format example copied back
        key = (q["question"].strip().lower(), re.sub(r"\s+", "", code))
        if key in seen:
            continue
        seen.add(key)
        title = str(q.get("title") or "").strip()
        if not title or len(title) > 60 or title.lower().startswith(("what", "which", "find", "question")):
            title = f"{str(p.get('topic') or 'Quiz').split(':')[0].strip().title()[:40]} #{len(out) + 1}"
        qtext = re.sub(r"\s*\(?options?\s*:\s*\[.*?\]\)?\s*$", "", str(q["question"]).strip(), flags=re.I | re.S)   # options shown separately
        out.append({"title": title[:120], "question": qtext or str(q["question"]).strip(), "code": code, "options": opts[:4], "answer": marked,
                    "check": str(q.get("check") or "").strip()[:3000], "explanation": str(q.get("explanation") or "")[:1500]})
    return out[:n]


def _run(c, qid, vid, user_id, lang, code, timeout=90):
    """Runs a program in the judge sandbox (through the normal queue) and returns what it printed, or None."""
    sid = c.execute("INSERT INTO submissions(competition_id,user_id,question_id,version_id,kind,language,code,custom_input,created_at) VALUES(NULL,?,?,?,'run',?,?,' ',?)",
                    (user_id, qid, vid, lang, code, db.now())).lastrowid
    t = time.time()
    try:
        while time.time() - t < timeout:
            r = db.one(c, "SELECT status, verdict FROM submissions WHERE id=?", sid)
            if r and r["status"] == "done":
                o = db.one(c, "SELECT output FROM judging_results WHERE submission_id=? ORDER BY ordinal LIMIT 1", sid)
                return o["output"] if o and r["verdict"] == "OK" else None
            time.sleep(1)
        return None
    finally:
        c.execute("DELETE FROM submissions WHERE id=?", (sid,))


def _run_c(c, qid, vid, user_id, code, timeout=90):
    return _run(c, qid, vid, user_id, "c", code, timeout)


def run_mcq(draft_id: int, params: dict, user_id: int):
    """Background: generate, save each as an unapproved question, and for output questions replace the model's claimed answer by the real one."""
    from .api_admin import QuestionIn, create_question, set_answer
    c = db.connect()
    first = None
    try:
        want, qs, seen = max(1, min(int(params.get("count") or 5), 10)), [], set()
        for attempt in range(4):   # small models drop or repeat items: ask again for the missing ones (and survive a bad reply)
            try:
                got = generate_mcq({**params, "count": want - len(qs)})
            except Exception:
                continue
            for q in got:
                k = re.sub(r"\W+", "", q["question"].lower() + q["code"])
                if k not in seen:
                    seen.add(k)
                    qs.append(q)
            if len(qs) >= want:
                break
        qs = qs[:want]
        if not qs:
            raise RuntimeError("The model did not return usable multiple-choice questions. Try again, a simpler topic, or a larger model.")
        for n, q in enumerate(qs):
            log = []
            st = q["question"] + (f"\n\n```c\n{q['code'].strip()}\n```" if q["code"] else "")
            if len(q["options"]) < 4:   # placeholder for the duplicate that was removed; fixed below once the program has run
                q["options"] = q["options"] + ["None of these outputs"][: 4 - len(q["options"])]
            with db.tx(c):
                qid = create_question(c, QuestionIn(title=q["title"][:150], category=str(params.get("topic") or "Problem Solving")[:60], difficulty=params.get("difficulty") or "Easy",
                                                    tags=["mcq", "ai"], statement=st, qtype="mcq", options=q["options"], answer=q["answer"], practice=True), {"id": user_id}, "ai")
            vid = db.one(c, "SELECT latest_version_id AS v FROM questions WHERE id=?", qid)["v"]
            def norm(x):   # same text, or the same number (2.4 == 2.4000000000000004 == 2.40)
                t = "\n".join(l.rstrip() for l in str(x).strip().splitlines())
                try:
                    return repr(round(float(t), 6))
                except ValueError:
                    return t
            prog = ("c", q["code"]) if q["code"] else ("python", q["check"]) if q["check"] else None
            real = _run(c, qid, vid, user_id, *prog) if prog else None
            if prog and real is not None and real.strip():
                match = next((o for o in q["options"] if norm(o) == norm(real)), None)
                if match and norm(match) == norm(q["answer"]):
                    log.append("Answer proved: the server ran the program and it prints the marked option." if q["code"] else
                               "Answer checked: a calculation the AI wrote agrees with the marked option. Read it once before approving.")
                elif match or q["code"]:
                    # a program's output is the truth; a calculation is trusted only when it lands exactly on one of the options
                    opts = q["options"] if match else [real.strip()[:300] if o == q["answer"] else o for o in q["options"]]
                    while len(opts) < 4 and q["code"]:
                        opts.append(f"{real.strip()[:280]} (and a newline)" if len(opts) % 2 else "Compilation error")
                    with db.tx(c):
                        c.execute("UPDATE question_versions SET options=? WHERE id=?", (json.dumps(list(dict.fromkeys(opts))[:4]), vid))
                        set_answer(c, vid, match or real.strip()[:300])
                    log.append("The model marked a wrong answer. Corrected to what the program really prints when the server runs it." if q["code"] else
                               "The marked answer was changed to what the AI's own calculation gives. Check it before approving.")
                else:
                    log.append("The model's calculation does not match any option, so the answer is unproved. Check it before approving.")
            elif prog:
                log.append("The " + ("program" if q["code"] else "calculation") + " could not be run, so the marked answer is the model's guess. Check it before approving.")
            else:
                log.append("Concept question: read it and confirm the marked answer before approving.")
            if q["explanation"]:
                log.append("Model's explanation: " + q["explanation"])
            pay = json.dumps({"title": q["title"], "qtype": "mcq", "harness": "done"})
            if n == 0:   # the first row stays "writing" until the whole set is done, so the page keeps refreshing
                first = (qid, pay, json.dumps(log), draft_id)
            else:
                c.execute("INSERT INTO generated_question_drafts(source,status,payload,warnings,question_id,created_by,created_at) VALUES('ai','pending',?,?,?,?,?)",
                          (pay, json.dumps(log), qid, user_id, db.now()))
        c.execute("UPDATE generated_question_drafts SET status='pending', question_id=?, payload=?, warnings=? WHERE id=?", first)
    except Exception as e:
        if first:   # some questions were already saved: keep them reviewable
            c.execute("UPDATE generated_question_drafts SET status='pending', question_id=?, payload=?, warnings=? WHERE id=?", first)
        else:
            c.execute("UPDATE generated_question_drafts SET status='failed', warnings=? WHERE id=?", (json.dumps([f"AI generation failed: {e}"]), draft_id))
        db.log_error(c, "ai", e)
    finally:
        c.close()


def suggest_tests(q: dict, kinds: list, count: int) -> list:
    prompt = f"""Problem: {q['title']}
Statement: {q['statement']}
Input format: {q['input_format']}
Constraints: {q['constraints']}
Sample input: {q.get('sample_input', '')}
Write {count} NEW test inputs. Cover these kinds: {', '.join(kinds)}.
(normal, boundary = minimum/maximum values, edge = tricky/duplicate/special values, stress = largest size that is still under 2000 numbers.)
Every input must be valid according to the input format and constraints. Do NOT include outputs.
Return JSON: {{"tests": [{{"input": str, "kind": "normal"|"boundary"|"edge"|"stress", "description": str}}]}}"""
    d = chat(prompt)
    return [{"input": _nl(t.get("input")), "kind": t.get("kind") if t.get("kind") in ("normal", "boundary", "edge", "stress") else "normal",
             "description": str(t.get("description") or "")[:200]} for t in (d.get("tests") or []) if isinstance(t, dict) and t.get("input") is not None and real_input(t.get("input"))][:count]


def variation(q: dict) -> dict:
    prompt = f"""Rewrite this problem into a NEW problem with the same learning objective but a different scenario, wording and numbers.
Original title: {q['title']}
Original statement: {q['statement']}
Input format: {q['input_format']}
Output format: {q['output_format']}
Constraints: {q['constraints']}
Return JSON exactly in this shape:
{QUESTION_SCHEMA}"""
    return chat(prompt)


REPAIR_ROUNDS = 3


def repair(q: dict, problems: list[str]) -> dict:
    prompt = f"""Your reference solutions for this problem were executed automatically and FAILED these checks:
{chr(10).join('- ' + p for p in problems)}

Problem: {q['title']}
Statement: {q['statement']}
Input format: {q['input_format']}
Output format: {q['output_format']}
Constraints: {q['constraints']}
Current C solution:
{q['reference'].get('c', '(missing)')}
Current Python solution:
{q['reference'].get('python', '(missing)')}

Fix the solutions so that BOTH are complete programs that read standard input exactly as the input format says,
print only the answer, and produce identical output for every valid input. Keep the problem itself unchanged.
Return ONLY the corrected programs (real code, not explanations) as JSON: {{"reference": {{"c": str, "python": str}}}}
Inside JSON strings write newlines in the code as \\n and a C newline escape as \\\\n."""
    d = chat(prompt)
    ref = d.get("reference") or {k: d[k] for k in ("c", "python") if k in d}   # small models also answer {"c": ...} or a bare string
    if isinstance(ref, str):
        ref = {("python" if looks_like_code("python", ref) and "#include" not in ref else "c"): ref}
    if not isinstance(ref, dict):
        return {}
    return {k: fix_code(k, str(v)) for k, v in ref.items() if k in ("c", "python") and v and looks_like_code(k, str(v))}


def _wait_validation(c, vid, timeout=300):
    import time
    t = time.time()
    while time.time() - t < timeout:
        if not db.one(c, "SELECT 1 AS x FROM submissions WHERE version_id=? AND kind='validate' AND status IN ('queued','running')", vid):
            time.sleep(1.5)  # chained cross-check jobs may be inserted right after the fill job
            if not db.one(c, "SELECT 1 AS x FROM submissions WHERE version_id=? AND kind='validate' AND status IN ('queued','running')", vid):
                return True
        time.sleep(1)
    return False


def _problems(c, qid, claimed_samples):
    """Turn sandbox validation results into concrete feedback for the model."""
    q = db.one(c, "SELECT latest_version_id FROM questions WHERE id=?", qid)
    v = db.one(c, "SELECT * FROM question_versions WHERE id=?", q["latest_version_id"])
    val = db.jl(v["validation"], {}) or {}
    refs = [r["language"] for r in db.all_(c, "SELECT language FROM reference_solutions WHERE version_id=?", v["id"])]
    tests = {t["ordinal"]: t for t in db.all_(c, "SELECT * FROM test_cases WHERE version_id=?", v["id"])}
    out, notes = [], []
    for lang in ("c", "python"):
        if lang not in refs:
            out.append(f"The {lang} solution is missing.")
    for lang, x in val.items():
        if x.get("compile_output"):
            out.append(f"The {lang} solution does not compile: {x['compile_output'][:400]}")
        elif x.get("verdict") not in ("AC", "OK"):
            sub = db.one(c, "SELECT id FROM submissions WHERE version_id=? AND kind='validate' AND language=? ORDER BY id DESC LIMIT 1", v["id"], lang)
            for r in db.all_(c, "SELECT ordinal, verdict, output, stderr FROM judging_results WHERE submission_id=? AND verdict NOT IN ('AC','OK') ORDER BY ordinal LIMIT 3", sub["id"]):
                t = tests.get(r["ordinal"], {})
                out.append(f"{lang} gave {r['verdict']} on input {t.get('input', '')[:200]!r}: printed {r['output'][:120]!r}, expected {t.get('expected', '')[:120]!r}"
                           + (f" (error: {r['stderr'][-200:]})" if r["stderr"] else ""))
    if not out and v["validated_at"]:
        for t, cl in zip([t for t in tests.values() if t["visibility"] == "sample"], claimed_samples):
            if cl.strip() and t["expected"].strip() != cl.strip():
                notes.append(f"For sample input {t['input'].strip()[:80]!r} the AI's statement claimed {cl.strip()[:60]!r} but both references print "
                             f"{t['expected'].strip()[:60]!r}. Samples now show the reference output; check the statement wording.")
    return out, notes, v


def convert(d: dict) -> dict:
    """Turn a question extracted from an uploaded document (possibly messy/partial) into a complete problem."""
    text = d.get("raw_text") or "\n".join(f"{k}: {d.get(k, '')}" for k in ("title", "statement", "input_format", "output_format", "constraints"))
    samples = "\n".join(f"Sample input:\n{x.get('input', '')}\nSample output:\n{x.get('output', '')}" for x in d.get("samples", []))
    prompt = f"""Below is a programming question copied from a question paper (it may be messy or incomplete).
Rewrite it as ONE complete, unambiguous competitive-programming problem that keeps the original task.
Fill in missing input/output formats and constraints sensibly. Include 2 samples and 8-12 hidden test inputs.
--- QUESTION TEXT ---
{text[:6000]}
{samples[:2000]}
--- END ---
Rules for reference solutions (they are executed automatically and must agree with each other):
- Each one is a COMPLETE program reading standard input exactly as the input format says and printing only the answer.
- The Python one must be a full script that reads input; never just a function.
- Test inputs must follow the input format exactly.
Return JSON exactly in this shape:
{QUESTION_SCHEMA}"""
    return chat(prompt)


TOOL_SCHEMA = """{
 "name": str (2-4 words), "button_label": str, "icon": one of ["bulb","bot","sparkles","book","target","code","alert","chart","wand"],
 "description": str (one sentence shown to students),
 "system_prompt": str (detailed instructions for the assistant model),
 "inputs": [{"key": str, "label": str, "type": "textarea"|"text"|"select", "options": [str], "placeholder": str}],
 "include": {"problem": bool, "code": bool, "last_result": bool},
 "allow_full_solutions": bool, "max_uses_per_problem": int (1-20)
}"""


def design_tool(request: str) -> dict:
    """Agent step: turn an organiser's plain-language idea into a safe, declarative tool spec (no code is generated)."""
    d = chat(f"""An organiser of a college coding platform wants a new AI tool for students. Their request:
"{request[:1500]}"
Design the tool. It will appear as a button in the coding workspace; when pressed it sends the chosen context
(problem statement, the student's current code, their last verdict) plus the form inputs to an assistant model
with your system_prompt, and shows the reply as markdown. Unless the request clearly asks otherwise, the tool must
guide learning and must not hand out complete solutions. Return JSON exactly in this shape:
{TOOL_SCHEMA}""")
    return normalize_tool(d)


def normalize_tool(d: dict) -> dict:
    icons = {"bulb", "bot", "sparkles", "book", "target", "code", "alert", "chart", "wand"}
    inputs = []
    for x in (d.get("inputs") or [])[:4]:
        if not isinstance(x, dict) or not x.get("key"):
            continue
        t = x.get("type") if x.get("type") in ("textarea", "text", "select") else "text"
        inputs.append({"key": re.sub(r"\W", "_", str(x["key"]))[:30], "label": str(x.get("label") or x["key"])[:60], "type": t,
                       "options": [str(o)[:60] for o in (x.get("options") or [])][:10] if t == "select" else [], "placeholder": str(x.get("placeholder") or "")[:120]})
    inc = d.get("include") or {}
    return {"name": str(d.get("name") or "AI Tool")[:40], "button_label": str(d.get("button_label") or d.get("name") or "Ask")[:30],
            "icon": d.get("icon") if d.get("icon") in icons else "sparkles", "description": str(d.get("description") or "")[:200],
            "system_prompt": str(d.get("system_prompt") or "You are a helpful programming tutor.")[:4000], "inputs": inputs,
            "include": {k: bool(inc.get(k, k != "last_result")) for k in ("problem", "code", "last_result")},
            "allow_full_solutions": bool(d.get("allow_full_solutions", False)),
            "max_uses_per_problem": max(1, min(20, int(d.get("max_uses_per_problem") or 3)))}


def run_draft(draft_id: int, kind: str, params: dict, user_id: int = None):
    """Background harness. A small local model gets things wrong often, so every draft goes through:
    generate -> save as an unapproved question -> run C + Python references on all tests in the sandbox ->
    feed concrete failures back to the model -> repeat (max REPAIR_ROUNDS). A human still approves the result."""
    from .api_admin import QuestionIn, TestIn, create_question, editable_version, invalidate, queue_validation
    c = db.connect()
    log = []
    try:
        fn = {"generate": generate_question, "variation": variation, "convert": convert}[kind]
        for attempt in range(3):  # small models occasionally produce broken JSON: just ask again
            try:
                raw = fn(params)
                break
            except RuntimeError as e:
                log.append(f"Attempt {attempt + 1}: {e} Retrying.")
                if attempt == 2:
                    raise
        q, warn = normalize_question(raw)
        log += [w for w in warn if not w.startswith("AI draft")]
        if not q["title"].strip() or not q["statement"].strip():
            raise RuntimeError("The model returned an empty problem. Try again or use a larger model.")
        simple = params.get("tests") is False   # organiser asked for simple input/output only: the examples are also what is judged
        if simple:
            q["tests"] = []
        elif len([t for t in q["tests"] if t["input"].strip()]) < 3:   # small models often skip hidden tests; without them the question cannot be approved
            try:
                q["tests"] += suggest_tests({**q, "sample_input": q["samples"][0]["input"] if q["samples"] else ""}, ["normal", "boundary", "edge"], 6)
                log.append(f"The model gave too few hidden tests; asked for more ({len(q['tests'])} now).")
            except Exception as e:
                log.append(f"Could not get extra hidden tests: {str(e)[:80]}")
        claimed = [s["output"] for s in q["samples"]]
        user = {"id": user_id}
        tests = [TestIn(input=s["input"], expected="", visibility="sample", explanation=s["explanation"][:5000], description="AI sample") for s in q["samples"] if s["input"].strip()]
        tests += [TestIn(input=t["input"], expected="", visibility="hidden", kind=t["kind"], description=("AI: " + t["description"])[:500]) for t in q["tests"] if t["input"].strip()]
        if simple:   # judged on the same examples the student sees
            tests += [TestIn(input=s["input"], expected="", visibility="hidden", description="Same as the example") for s in q["samples"] if s["input"].strip()]
        if not tests:
            raise RuntimeError("The model produced no test inputs.")
        with db.tx(c):
            qid = create_question(c, QuestionIn(title=q["title"][:150], category=q["category"][:60], difficulty=q["difficulty"], tags=q["tags"],
                                                statement=q["statement"], input_format=q["input_format"], output_format=q["output_format"],
                                                constraints=q["constraints"], references=q["reference"], tests=tests), user, "ai")
            c.execute("UPDATE generated_question_drafts SET question_id=?, payload=? WHERE id=?", (qid, json.dumps({**q, "harness": "running"}), draft_id))
        for rnd in range(REPAIR_ROUNDS + 1):
            if db.one(c, "SELECT 1 AS x FROM reference_solutions r JOIN questions q ON q.latest_version_id=r.version_id WHERE q.id=?", qid):
                with db.tx(c):
                    queue_validation(c, qid, user, True)
                vid = db.one(c, "SELECT latest_version_id FROM questions WHERE id=?", qid)["latest_version_id"]
                if not _wait_validation(c, vid):
                    log.append("Validation timed out (is the judge worker running?).")
                    break
            problems, notes, v = _problems(c, qid, claimed)
            if not problems:
                log.append(f"Sandbox check passed{' after ' + str(rnd) + ' repair round(s)' if rnd else ''}: C and Python agree on all {len(tests)} tests.")
                log += notes
                break
            if rnd == REPAIR_ROUNDS:
                # Fallback: if exactly one reference language is broken, keep the one that works and let a human add the other.
                refs_now = {r["language"]: r["code"] for r in db.all_(c, "SELECT language, code FROM reference_solutions WHERE version_id=?", v["id"])}
                bad = {lang for lang in refs_now if any(p.startswith(f"The {lang} solution") or p.startswith(f"{lang} gave") for p in problems)}
                if len(refs_now) == 2 and len(bad) == 1:
                    drop = bad.pop()
                    with db.tx(c):
                        vid2, _ = editable_version(c, qid, user)
                        c.execute("DELETE FROM reference_solutions WHERE version_id=? AND language=?", (vid2, drop))
                        c.execute("UPDATE test_cases SET expected='', validated=0 WHERE version_id=?", (vid2,))
                        invalidate(c, vid2)
                        queue_validation(c, qid, user, True)
                    _wait_validation(c, vid2)
                    ok_now = db.one(c, "SELECT validated_at FROM question_versions WHERE id=?", vid2)["validated_at"]
                    log.append(f"The {drop} solution could not be repaired, so it was removed; outputs now come from the remaining reference"
                               + (" (validated). Add a second reference by hand to cross-check." if ok_now else ", which also fails. Fix by hand."))
                else:
                    log.append(f"Still failing after {REPAIR_ROUNDS} repair rounds; fix the reference solutions by hand: " + " | ".join(problems[:3]))
                break
            log.append(f"Round {rnd + 1}: {len(problems)} problem(s) found, asking the model to repair.")
            try:   # a broken reply from the model costs one round, never the whole draft
                fixed = repair({**q, "reference": {r["language"]: r["code"] for r in db.all_(c, "SELECT language, code FROM reference_solutions WHERE version_id=?", v["id"])}}, problems)
            except Exception as e:
                log.append(f"Round {rnd + 1}: the model's fix was unreadable ({str(e)[:80]}).")
                fixed = None
            if not fixed:
                log.append("The model returned no fix.")
                continue
            with db.tx(c):
                vid2, _ = editable_version(c, qid, user)
                for lang, code in fixed.items():
                    c.execute("INSERT INTO reference_solutions(version_id,language,code) VALUES(?,?,?) ON CONFLICT(version_id,language) DO UPDATE SET code=excluded.code", (vid2, lang, code))
                c.execute("UPDATE test_cases SET expected='', validated=0 WHERE version_id=?", (vid2,))  # recompute from the fixed reference
                invalidate(c, vid2)
        if params.get("debug") and db.one(c, "SELECT qv.validated_at AS v FROM questions q JOIN question_versions qv ON qv.id=q.latest_version_id WHERE q.id=?", qid)["v"]:
            make_debug(c, qid, user_id, log)
        st = "pending"
        if params.get("auto_publish"):   # practice auto-grow: published only when both reference solutions agree on every test
            ok = any(l.startswith("Sandbox check passed") for l in log) and db.one(c, "SELECT validated_at AS v FROM question_versions qv JOIN questions q ON q.latest_version_id=qv.id WHERE q.id=?", qid)["v"]
            if ok:
                c.execute("UPDATE questions SET status='approved', practice=1, tags=? WHERE id=?", (json.dumps((q["tags"] + ["auto"])[:12]), qid))
                st = "approved"
                log.append("Published to Practice automatically.")
            else:
                log.append("Not published: it did not pass the sandbox check. Review it by hand or reject it.")
        c.execute("UPDATE generated_question_drafts SET status=?, payload=?, warnings=? WHERE id=?", (st, json.dumps({**q, "harness": "done"}), json.dumps(log), draft_id))
    except Exception as e:
        c.execute("UPDATE generated_question_drafts SET status='failed', warnings=? WHERE id=?", (json.dumps(log + [f"AI generation failed: {e}"]), draft_id))
        db.log_error(c, "ai", e)
    finally:
        c.close()
