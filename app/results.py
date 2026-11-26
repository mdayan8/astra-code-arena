"""Results of a competition: one row per student (rank, marks out of their paper's total, attempted/solved, time used)
and the per-question detail; a printable PDF report (summary, top 5, full ranking) built with pymupdf (already installed)."""
import html
import io
import statistics
from datetime import datetime
from zoneinfo import ZoneInfo

from . import contest, db

TZ = ZoneInfo("Asia/Kolkata")
TYPE = {"standard": "Coding", "complete": "Complete the code", "debug": "Fix the bug", "mcq": "Multiple choice",
        "output": "Predict the output", "trace": "Code tracing", "prompt": "Prompt"}


def clock(t):
    return datetime.fromtimestamp(t, TZ).strftime("%d %b %Y, %I:%M %p") if t else ""


def _n(x):
    x = round(float(x or 0), 2)
    return int(x) if x.is_integer() else x


def collect(c, comp):
    lb = contest.leaderboard(c, comp, fresh=True)
    qinfo = {q["question_id"]: q for q in db.all_(c, "SELECT cq.question_id, cq.marks, qv.title, qv.qtype FROM competition_questions cq "
                                                    "JOIN question_versions qv ON qv.id=cq.version_id WHERE cq.competition_id=? ORDER BY cq.ordinal", comp["id"])}
    cols = {r[1] for r in c.execute("PRAGMA table_info(competition_participants)")}
    parts = {r["user_id"]: r for r in db.all_(c, "SELECT user_id, questions" + (", started_at" if "started_at" in cols else "") +
                                              " FROM competition_participants WHERE competition_id=?", comp["id"])}
    last_sub = {r["user_id"]: r["t"] for r in db.all_(c, "SELECT user_id, MAX(created_at) AS t FROM submissions WHERE competition_id=? AND kind='submit' GROUP BY user_id", comp["id"])}
    verdicts = {}
    for r in db.all_(c, "SELECT user_id, question_id, verdict FROM submissions WHERE competition_id=? AND kind='submit' AND status='done' ORDER BY id", comp["id"]):
        verdicts[(r["user_id"], r["question_id"])] = r["verdict"]
    fixed = contest.fixed_total(c, comp["id"])
    start = comp["started_at"] or comp["start_at"] or 0
    out = []
    for r in lb["rows"]:
        p = parts.get(r["user_id"]) or {}
        paper = [q for q in (db.jl(p.get("questions")) or list(qinfo)) if q in qinfo]
        weights = [qinfo[q]["marks"] for q in paper]
        marks = contest.shares(weights, fixed, 2) if fixed and paper else weights   # 2 decimals: matches the scores
        qs = []
        for i, (qid, m) in enumerate(zip(paper, marks)):
            st = r["per"].get(str(qid)) or {}
            qs.append({"label": contest.label(i), "question_id": qid, "title": qinfo[qid]["title"], "type": TYPE.get(qinfo[qid]["qtype"] or "standard", "Coding"),
                       "marks": _n(m), "scored": _n(st.get("score") or 0), "tries": st.get("tries") or 0, "solved": bool(st.get("ac")),
                       "verdict": verdicts.get((r["user_id"], qid)) or ""})
        total = fixed or sum(weights)
        began = max(p.get("started_at") or start, start) if start else None
        ended = r.get("finished_at") or last_sub.get(r["user_id"])
        attempted = sum(1 for q in qs if q["tries"])
        out.append({"rank": r["rank"], "participant_id": r.get("participant_id") or "", "name": r["name"], "department": r.get("department") or "",
                    "year": r.get("year") or "", "score": _n(r["score"]), "total": _n(total), "pct": round(100 * float(r["score"]) / total, 1) if total else 0,
                    "questions": len(qs), "attempted": attempted, "solved": sum(1 for q in qs if q["solved"]), "warnings": r.get("warnings") or 0,
                    "deducted": _n(r.get("deducted") or 0), "minutes": max(0, (ended - began) // 60) if ended and began else None,
                    "finished": clock(r.get("finished_at")), "status": "Did not attempt" if not attempted else "Finished" if r.get("finished_at") else "Attempted",
                    "detail": qs})
    return out


def summary_rows(rows):
    return (["Rank", "Participant ID", "Name", "Department", "Year", "Score", "Out of", "Percent", "Questions", "Attempted", "Solved",
             "Warnings", "Marks lost to warnings", "Time used (min)", "Finished at", "Status"],
            [[r["rank"], r["participant_id"], r["name"], r["department"], r["year"], r["score"], r["total"], r["pct"], r["questions"], r["attempted"],
              r["solved"], r["warnings"], r["deducted"], r["minutes"] if r["minutes"] is not None else "", r["finished"], r["status"]] for r in rows])


def detail_rows(rows):
    return (["Rank", "Participant ID", "Name", "Department", "Year", "Total score", "Out of", "Q", "Question", "Type", "Marks", "Scored",
             "Solved", "Attempts", "Last verdict"],
            [[r["rank"], r["participant_id"], r["name"], r["department"], r["year"], r["score"], r["total"], q["label"], q["title"], q["type"],
              q["marks"], q["scored"], "yes" if q["solved"] else "no", q["tries"], q["verdict"]] for r in rows for q in r["detail"]])


ALT = ("", " class='alt'")
CSS = """
body { font-family: sans-serif; font-size: 9pt; color: #1f2430; }
h2 { font-size: 11.5pt; color: #4c35c9; margin: 14pt 0 5pt 0; }
table { border-collapse: collapse; width: 100%; }
th { background-color: #4c35c9; color: #ffffff; text-align: left; font-size: 8pt; padding: 4pt 5pt; }
td { padding: 3.5pt 5pt; border-bottom: 1px solid #eceef3; font-size: 8.5pt; }
.num { text-align: right; }
.band td { background-color: #4c35c9; color: #ffffff; border: none; padding: 10pt 12pt; }
.ttl { font-size: 19pt; font-weight: bold; color: #ffffff; }
.bsub { font-size: 8.5pt; color: #ded8ff; }
.tiles td { border: 2px solid #ffffff; padding: 7pt 8pt; }
.k { font-size: 15pt; font-weight: bold; }
.kl { font-size: 7.5pt; color: #4b5563; }
.t1 { background-color: #ede9fe; } .t2 { background-color: #dbeafe; } .t3 { background-color: #dcfce7; }
.t4 { background-color: #fef3c7; } .t5 { background-color: #fce7f3; } .t6 { background-color: #e0f2fe; }
.gold td { background-color: #fef3c7; font-size: 10pt; } .silver td { background-color: #f1f5f9; font-size: 10pt; }
.bronze td { background-color: #ffedd5; font-size: 10pt; } .plain td { background-color: #faf9ff; font-size: 10pt; }
.alt td { background-color: #f8f9fc; }
.hi { color: #15803d; font-weight: bold; } .mid { color: #b45309; font-weight: bold; } .lo { color: #b91c1c; font-weight: bold; }
.bar { color: #7c6cf0; font-size: 8pt; letter-spacing: -1pt; }
.note { color: #6b7280; font-size: 7.5pt; margin-top: 10pt; }
"""


def pdf(c, comp):
    return render(comp, collect(c, comp), contest.fixed_total(c, comp["id"]) or comp["max_marks"])


def render(comp, rows, fallback_total=100):
    """The report from collected rows (kept separate so it can be tested with any data)."""
    import pymupdf
    e = lambda x: html.escape(str(x if x is not None else ""))
    import re
    cut = lambda x, n: re.sub(r"(\S{16})(?=\S)", r"\1 ", (lambda t: t if len(t) <= n else t[:n - 1] + "...")(" ".join(str(x or "").split())))   # long words wrap
    rows = [{**r, "name": cut(r["name"], 34), "department": cut(r["department"], 14), "participant_id": cut(r["participant_id"], 18)} for r in rows]
    comp = {**comp, "title": cut(comp["title"], 90)}
    done = [r for r in rows if r["attempted"]]
    scores = [float(r["score"]) for r in done]
    total = rows[0]["total"] if rows else fallback_total
    per = rows[0]["questions"] if rows else 0
    ended = comp.get("ended_at") or comp.get("finalized_at")
    tf = float(total or 0) or 1.0
    same = len({str(r["total"]) for r in rows}) <= 1   # one total for everyone: say it once in the header
    of = (lambda r: "") if same else (lambda r: f" / {e(r['total'])}")
    sh = f"Score (of {e(total)})" if same else "Score"
    cls = lambda sc: "hi" if float(sc) >= 0.7 * tf else "mid" if float(sc) >= 0.4 * tf else "lo"
    tile = lambda v, l, k: f"<td class='{k}'><span class='k'>{e(v)}</span><br/><span class='kl'>{e(l)}</span></td>"
    mins = lambda r: f"{r['minutes']} min" if r["minutes"] is not None else ""
    body = [f"<table class='band'><tr><td><span class='ttl'>{e(comp['title'])}</span><br/><span class='bsub'>Results report &#183; {e(comp['code'])} &#183; "
            f"{e(clock(comp['start_at']))} &#183; {e(comp['duration_min'])} minutes &#183; {e(per)} questions per student &#183; {e(total)} marks"
            f"{' &#183; ended ' + e(clock(ended)) if ended else ''}</span></td></tr></table><p></p>",
            "<table class='tiles'><tr>" + tile(len(rows), "registered", "t1") + tile(len(done), "attempted", "t2") +
            tile(_n(max(scores)) if scores else 0, "highest", "t3") + tile(_n(statistics.mean(scores)) if scores else 0, "average", "t4") +
            tile(_n(statistics.median(scores)) if scores else 0, "median", "t5") +
            tile(sum(1 for x in scores if x >= 0.4 * tf), "scored 40% or more", "t6") + "</tr></table>"]
    # top 5
    body.append("<h2>Top 5</h2><table><tr><th>Rank</th><th>Name</th><th>Participant ID</th><th>Department</th>"
                f"<th class='num'>{sh}</th><th class='num'>Solved</th><th class='num'>Time</th></tr>")
    style = {1: ("gold", "1st"), 2: ("silver", "2nd"), 3: ("bronze", "3rd")}
    for r in done[:5]:
        k, lab = style.get(r["rank"], ("plain", f"{r['rank']}th"))
        body.append(f"<tr class='{k}'><td><b>{lab}</b></td><td><b>{e(r['name'])}</b></td><td>{e(r['participant_id'])}</td><td>{e(r['department'])}</td>"
                    f"<td class='num'><span class='{cls(r['score'])}'>{e(r['score'])}</span>{of(r)}</td>"
                    f"<td class='num'>{e(r['solved'])} / {e(r['questions'])}</td><td class='num'>{mins(r)}</td></tr>")
    if not done:
        body.append("<tr><td colspan='7'>No one has attempted yet.</td></tr>")
    body.append("</table>")
    # how the scores spread
    if scores:
        bins = [0] * 5
        for x in scores:
            bins[min(4, int(5 * x / tf))] += 1
        top_n = max(bins) or 1
        body.append("<h2>Score spread</h2><table>")
        for i, n in enumerate(bins):
            lo_, hi_ = _n(tf * i / 5), _n(tf * (i + 1) / 5)
            body.append(f"<tr><td style='width:22%'>{lo_} to {hi_}{'' if i < 4 else ''}</td><td class='bar'>{'&#9608;' * max(0, round(40 * n / top_n))}</td>"
                        f"<td class='num' style='width:16%'>{n} student{'s' if n != 1 else ''}</td></tr>")
        body.append("</table>")
    # by department
    depts = {}
    for r in rows:
        depts.setdefault(r["department"] or "-", []).append(r)
    if len(depts) > 1 or rows:
        body.append("<h2>By department</h2><table><tr><th>Department</th><th class='num'>Registered</th><th class='num'>Attempted</th>"
                    "<th class='num'>Average</th><th class='num'>Best</th><th>Top student</th></tr>")
        for j, (d, rs) in enumerate(sorted(depts.items(), key=lambda kv: -len(kv[1]))):
            sc = [float(r["score"]) for r in rs if r["attempted"]]
            best = min((r for r in rs if r["attempted"]), key=lambda r: r["rank"], default=None)
            body.append(f"<tr{ALT[j % 2]}><td><b>{e(d)}</b></td><td class='num'>{len(rs)}</td><td class='num'>{len(sc)}</td>"
                        f"<td class='num'>{_n(statistics.mean(sc)) if sc else '-'}</td><td class='num'>{_n(max(sc)) if sc else '-'}</td>"
                        f"<td>{e(best['name']) if best else '-'}</td></tr>")
        body.append("</table>")
    # everyone
    body.append("<h2>Full results</h2><table><tr><th>Rank</th><th>Participant ID</th><th>Name</th><th>Dept</th><th>Year</th>"
                f"<th class='num'>{sh}</th><th class='num'>%</th><th class='num'>Tried</th><th class='num'>Solved</th><th class='num'>Warn.</th><th class='num'>Time</th></tr>")
    for j, r in enumerate(rows):
        body.append(f"<tr{ALT[j % 2]}><td>{e(r['rank'])}</td><td>{e(r['participant_id'])}</td><td>{e(r['name'])}</td><td>{e(r['department'])}</td>"
                    f"<td>{e(r['year'])}</td><td class='num'><span class='{cls(r['score']) if r['attempted'] else 'lo'}'>{e(r['score'])}</span>{of(r)}</td>"
                    f"<td class='num'>{e(r['pct'])}</td><td class='num'>{e(r['attempted'])}/{e(r['questions'])}</td><td class='num'>{e(r['solved'])}</td>"
                    f"<td class='num'>{e(r['warnings'] or '')}</td><td class='num'>{mins(r) if r['attempted'] else e(r['status'])}</td></tr>")
    if not rows:
        body.append("<tr><td colspan='11'>No one registered.</td></tr>")
    body.append("</table><p class='note'>Scores: <span class='hi'>70% or more</span> &#183; <span class='mid'>40 to 70%</span> &#183; <span class='lo'>below 40%</span>. "
                "Ranked by score; equal scores by time taken (plus penalty minutes for wrong attempts), then who reached the score first. "
                "Every student's paper is worth the same total; harder questions carry more of it. Marks lost to fair-play warnings are already taken off. "
                f"Generated {e(clock(db.now()))} by ASTRA Code Arena.</p>")
    story = pymupdf.Story(html="".join(body), user_css=CSS)
    buf = io.BytesIO()
    w = pymupdf.DocumentWriter(buf)
    page, where = pymupdf.paper_rect("a4"), pymupdf.paper_rect("a4") + (32, 32, -32, -36)
    more, guard = True, 0
    while more and guard < 500:   # ponytail: 500 pages max (~20,000 students)
        dev = w.begin_page(page)
        more, _ = story.place(where)
        story.draw(dev)
        w.end_page()
        guard += 1
    w.close()
    doc = pymupdf.open("pdf", buf.getvalue())   # page numbers
    for i, pg in enumerate(doc, 1):
        pg.insert_text((32, page.height - 18), f"{comp['title']}  -  page {i} of {doc.page_count}", fontsize=7, color=(0.45, 0.45, 0.5))
    return doc.tobytes(garbage=3, deflate=True)


if __name__ == "__main__":   # robustness self-check of the PDF (no database needed)
    import random
    import pymupdf
    comp = {"title": "Code Arena <Session> & 1", "code": "CA-1", "start_at": 1795000000, "duration_min": 120, "ended_at": 1795007200}
    names = ["Asha Rao", "<b>Bold</b> & 'Quote'", "A" * 120, "ಕನ್ನಡ ವಿದ್ಯಾರ್ಥಿ", "हिंदी छात्र", "", "Ravi K"]
    def row(i, total=100):
        att = i % 9 != 0
        sc = round(random.uniform(0, total), 2) if att else 0
        return {"rank": i + 1, "participant_id": f"AST26-{i:04d}", "name": names[i % len(names)], "department": ["CSE", "ISE", "", "ECE"][i % 4],
                "year": i % 4 + 1, "score": sc, "total": total, "pct": round(100 * sc / total, 1) if total else 0, "questions": 10, "attempted": 7 if att else 0,
                "solved": 5 if att else 0, "warnings": i % 3, "deducted": 0, "minutes": (i * 7) % 130 if att else None, "finished": "",
                "status": "Finished" if att else "Did not attempt", "detail": []}
    for n, total in ((0, 100), (1, 100), (5, 50), (250, 100), (30, 0)):
        rows = sorted((row(i, total) for i in range(n)), key=lambda r: -r["score"])
        for k, r in enumerate(rows):
            r["rank"] = k + 1
        b = render(comp, rows)
        d = pymupdf.open("pdf", b)
        txt = "".join(p.get_text() for p in d)
        assert b[:5] == b"%PDF-" and "Full results" in txt and f"page 1 of {d.page_count}" in txt, n
        assert n < 100 or (d.page_count >= 4 and "AST26-0249" in txt), (n, d.page_count)
        assert n < 2 or "<b>Bold</b> & 'Quote'" in txt, "names with HTML characters are printed as typed, never run as markup"
        if n == 250:
            d[0].get_pixmap(dpi=80).save("/private/tmp/claude-501/-Users-mdayan/985df9dc-8bc1-47ce-8658-d5bf5fa55057/scratchpad/p1.png")
    print("results pdf self-check ok")
