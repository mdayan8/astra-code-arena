"""AI question jobs shared by AI Studio, the competition assistant and competition question pools.

Pool: a competition gives each student their own draw of questions. While students register, the AI writes variants of
the competition's questions in the background. A variant joins the pool only if the server proved its answer by running
code (coding: both reference solutions agree on every test; predict-the-output: the program really prints the marked
option). Concept and logic questions cannot be proved, so they are never added automatically."""
import json
import random
import re
import threading

from . import ai, db

_busy, _lock = set(), threading.Lock()
MAX_ADDED = 15         # AI questions added to one competition at most
PER_STUDENTS = 4       # one new variant for every 4 registrations


def new_draft(c, uid, request, title=""):
    return c.execute("INSERT INTO generated_question_drafts(source,status,payload,created_by,created_at) VALUES('ai','generating',?,?,?)",
                     (json.dumps({"request": request, **({"title": title} if title else {})}), uid, db.now())).lastrowid


def spawn(c, uid, m, reference=""):
    """Start writing m = {count, type, difficulty, topic} questions, optionally based on a reference document.
    Returns how many were started (0 = this type cannot be written)."""
    typ, n = m.get("type"), max(1, min(int(m.get("count") or 1), 40))
    topic = (m.get("topic") or "").strip() or ("the topics in the reference material" if reference else "")
    p = {"difficulty": m.get("difficulty") or "Easy", **({"comp_tag": m["comp_tag"]} if m.get("comp_tag") else {})}
    if reference:
        p["reference"] = ai.chunk(reference, 0, 4000)
    if typ in ("mcq", "output", "trace"):
        logic = typ == "mcq" and re.search(r"logic|reason|aptitude|puzzle", topic)
        style = "output" if typ != "mcq" or re.search(r"output|predict|trac|code|program|pointer|loop|operator", topic) else "concept"
        if logic:
            topic = f"logical reasoning puzzles ({topic}): number series, ratios, time and work, directions, seating, coding-decoding"
        p.update(style=style, count=n, topic=topic or ("logical reasoning: series, ratios, directions" if logic else "C programming basics" if style == "output" else "data structures and algorithms basics"))
        # sets of up to 5 (small models lose track beyond that); with a document, each set reads a different part of it
        for k in range(0, n, 5):
            pk = {**p, "count": min(5, n - k), **({"reference": ai.chunk(reference, k // 5, 4000)} if reference else {})}
            gid = new_draft(c, uid, pk, f"{pk['count']} multiple-choice: {p['topic']}" + (f" (part {k // 5 + 1})" if n > 5 else ""))
            threading.Thread(target=ai.run_mcq, args=(gid, pk, uid), daemon=True).start()
        return n
    if typ in (None, "standard", "debug"):
        p.update(topic=topic or "arrays, strings and loops", language="C and Python", samples=2, debug=typ == "debug")
        if m.get("tests") is not None:
            p["tests"] = bool(m["tests"])
        for i in range(min(n, 5)):   # each coding problem takes minutes on a small model
            pi = {**p, "reference": ai.chunk(reference, i)} if reference else p   # each one reads a different part of a long document
            threading.Thread(target=ai.run_draft, args=(new_draft(c, uid, pi, "Fix the bug: " + topic if typ == "debug" else ""), "generate", pi, uid), daemon=True).start()
        return min(n, 5)
    return 0


def _added(c, cid):
    return db.jl(db.setting(c, f"pool:{cid}", "[]"))


def provable(q):
    """Templates whose variants the server can check by running code."""
    return q["qtype"] in ("standard", "debug", "complete") or (q["qtype"] in ("mcq", "output", "trace") and "```c" in (q["statement"] or ""))


def maybe_grow(c, cid, force=0):
    """Called when a student registers (force=0) or from the admin page (force=N extra variants)."""
    comp = db.one(c, "SELECT * FROM competitions WHERE id=?", cid)
    if not comp or comp["status"] not in ("draft", "published") or not ai.enabled():
        return 0
    if not force:
        if not comp["ai_pool"] or not comp["draw"]:
            return 0
        n = db.one(c, "SELECT COUNT(*) AS n FROM competition_participants WHERE competition_id=?", cid)["n"]
        want = min(MAX_ADDED, n // PER_STUDENTS) - len(_added(c, cid))
        if want <= 0:
            return 0
        force = 1
    with _lock:
        if cid in _busy:
            return 0
        _busy.add(cid)
    threading.Thread(target=_grow, args=(cid, min(force, MAX_ADDED)), daemon=True).start()
    return force


def _grow(cid, n):
    c = db.connect()
    try:
        row = db.one(c, "SELECT created_by, ai_prompt FROM competitions WHERE id=?", cid)
        adm, ask = row["created_by"], (row["ai_prompt"] or "").strip()
        for _ in range(n):
            if len(_added(c, cid)) >= MAX_ADDED:
                break
            temps = [q for q in db.all_(c, "SELECT cq.question_id, cq.marks, q.category, q.difficulty, qv.title, qv.statement, qv.input_format, qv.output_format, "
                                              "qv.constraints, qv.qtype FROM competition_questions cq JOIN questions q ON q.id=cq.question_id "
                                              "JOIN question_versions qv ON qv.id=cq.version_id WHERE cq.competition_id=?", cid)
                     if provable(q) and q["question_id"] not in _added(c, cid)]
            if not temps:
                db.log_error(c, "pool", f"competition {cid}: no question the AI can vary and check (needs coding or predict-the-output questions)")
                break
            t = random.choice(temps)
            if t["qtype"] in ("standard", "debug", "complete"):
                p = {k: t[k] for k in ("title", "statement", "input_format", "output_format", "constraints")}
                p.update(auto_publish=True, difficulty=t["difficulty"])
                if ask:   # the organiser's stored instructions shape the new story (same skill, same input/output style)
                    p["statement"] = f"{t['statement']}\n\nOrganiser's instructions for the new version: {ask}"
                gid = new_draft(c, adm, {"variation_of": t["question_id"], "pool": cid})
                ai.run_draft(gid, "variation", p, adm)
            else:
                p = {"topic": ask or f"{t['category']}: {t['title']}", "difficulty": t["difficulty"], "style": "output", "count": 1}
                gid = new_draft(c, adm, {**p, "pool": cid}, f"Pool variant: {t['title']}")
                ai.run_mcq(gid, p, adm)
            g = db.one(c, "SELECT question_id, warnings FROM generated_question_drafts WHERE id=?", gid)
            ok = g and g["question_id"] and any(w.startswith(("Sandbox check passed", "Answer proved", "The model marked a wrong answer", "Published")) for w in db.jl(g["warnings"]))
            q = ok and db.one(c, "SELECT latest_version_id AS v, (SELECT validated_at FROM question_versions WHERE id=latest_version_id) AS ok FROM questions WHERE id=?", g["question_id"])
            if not (q and q["ok"]):
                continue
            with db.tx(c):
                comp = db.one(c, "SELECT status FROM competitions WHERE id=?", cid)
                if comp["status"] not in ("draft", "published"):
                    break   # the round started: the pool is final
                c.execute("UPDATE questions SET status='approved' WHERE id=?", (g["question_id"],))
                c.execute("INSERT OR IGNORE INTO competition_questions(competition_id,question_id,version_id,ordinal,marks) "
                          "VALUES(?,?,?,(SELECT COALESCE(MAX(ordinal),0)+1 FROM competition_questions WHERE competition_id=?),?)",
                          (cid, g["question_id"], q["v"], cid, t["marks"]))
                db.set_setting(c, f"pool:{cid}", json.dumps(_added(c, cid) + [g["question_id"]]))
    except Exception as e:   # a background helper must never take the server down
        db.log_error(c, "pool", e)
    finally:
        with _lock:
            _busy.discard(cid)
        c.close()


def number_variants(c, cid, per, uid):
    """Instant variants of the competition's predict-the-output questions (changed numbers, answers proved by running).
    Returns how many questions can be varied; the work runs in the background."""
    from . import variants
    comp = db.one(c, "SELECT status FROM competitions WHERE id=?", cid)
    if not comp or comp["status"] not in ("draft", "published"):
        return -1
    temps = [q for q in db.all_(c, "SELECT cq.question_id, cq.marks, qv.qtype, qv.statement FROM competition_questions cq JOIN question_versions qv ON qv.id=cq.version_id "
                                   "WHERE cq.competition_id=?", cid) if q["qtype"] in ("mcq", "output", "trace") and "```c" in (q["statement"] or "")]
    if not temps:
        return 0
    with _lock:
        if cid in _busy:
            return 0
        _busy.add(cid)

    def work():
        c2 = db.connect()
        try:
            for t in temps:
                vid = db.one(c2, "SELECT latest_version_id AS v FROM questions WHERE id=?", t["question_id"])["v"]
                for _ in range(per):
                    nid = variants.make(c2, t["question_id"], uid, lambda code: ai._run(c2, t["question_id"], vid, uid, "c", code))
                    if not nid:
                        continue
                    with db.tx(c2):
                        if db.one(c2, "SELECT status FROM competitions WHERE id=?", cid)["status"] not in ("draft", "published"):
                            return
                        c2.execute("INSERT OR IGNORE INTO competition_questions(competition_id,question_id,version_id,ordinal,marks) VALUES(?,?,"
                                   "(SELECT latest_version_id FROM questions WHERE id=?),(SELECT COALESCE(MAX(ordinal),0)+1 FROM competition_questions WHERE competition_id=?),?)",
                                   (cid, nid, nid, cid, t["marks"]))
        except Exception as e:
            db.log_error(c2, "variants", e)
        finally:
            with _lock:
                _busy.discard(cid)
            c2.close()
    threading.Thread(target=work, daemon=True).start()
    return len(temps)


def add_to_comp(c, cid, qid):
    """Approve a checked question and add it to a draft competition with the same marks as the others there."""
    comp = db.one(c, "SELECT status FROM competitions WHERE id=?", cid)
    if not comp or comp["status"] != "draft":
        return False
    marks = (db.one(c, "SELECT marks FROM competition_questions WHERE competition_id=? ORDER BY ordinal LIMIT 1", cid) or {}).get("marks") or 10
    with db.tx(c):
        c.execute("UPDATE questions SET status='approved' WHERE id=?", (qid,))
        c.execute("INSERT OR IGNORE INTO competition_questions(competition_id,question_id,version_id,ordinal,marks) VALUES(?,?,"
                  "(SELECT latest_version_id FROM questions WHERE id=?),(SELECT COALESCE(MAX(ordinal),0)+1 FROM competition_questions WHERE competition_id=?),?)",
                  (cid, qid, qid, cid, marks))
    return True


def info(c, cid):
    return {"added": len(_added(c, cid)), "max": MAX_ADDED, "working": cid in _busy, "per_students": PER_STUDENTS}
