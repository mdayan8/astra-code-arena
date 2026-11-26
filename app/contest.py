"""Competition timing/state and leaderboard scoring (pure functions over stored rows => reproducible)."""
import json
import random
import secrets
import threading
import time

from . import db

DEFAULT_SCORING = {
    "mode": "partial",          # partial (by test weight) | all_or_nothing
    "negative_marks": 0,        # deducted per wrong submission on a question (never below 0 for that question)
    "first_blood_bonus": 0,     # extra marks for the first full solve of each question
    "penalty_minutes": 10,      # tie-break penalty per wrong attempt before the best submission
    "tie_break": "penalty",     # penalty (time + wrong attempts) | time (time of last score improvement)
    "violation_pct": 5,         # % of the student's maximum marks deducted per fair-play warning (left the test, internet, ...)
}
ANSWER_TYPES = ("output", "trace", "mcq")


def _split(n, sizes):
    """Largest-remainder split of n slots over groups of the given sizes (everyone gets the same counts per group)."""
    tot = sum(sizes)
    raw = [n * s / tot for s in sizes]
    got = [min(int(r), s) for r, s in zip(raw, sizes)]
    for k in sorted(range(len(sizes)), key=lambda k: -(raw[k] - int(raw[k]))):
        if sum(got) >= n:
            break
        if got[k] < sizes[k]:
            got[k] += 1
    return got


def rotated(ids, k):
    """Order for the k-th student when everyone gets the same questions: a rotation, reversed every other round,
    so students who start one after another never begin with the same question (and orders repeat as rarely as possible)."""
    if not ids:
        return ids
    n = len(ids)
    r = ids[k % n:] + ids[:k % n]
    return list(reversed(r)) if (k // n) % 2 else r


def variety(pool, n, shuffle):
    """How many different papers the list can make: sets of questions (same mix for everyone) x orders."""
    from math import comb, factorial
    if not pool:
        return {"sets": 0, "orders": 0, "per_student": 0}
    if not n or n >= len(pool):
        return {"sets": 1, "orders": (2 * len(pool) if len(pool) > 2 else len(pool)) if shuffle else 1, "per_student": len(pool)}
    groups = {}
    for q in pool:
        groups.setdefault(("answer" if q["qtype"] in ANSWER_TYPES else "code", q["difficulty"]), []).append(q)
    keys = sorted(groups)
    sets = 1
    for k, m in zip(keys, _split(n, [len(groups[k]) for k in keys])):
        sets *= comb(len(groups[k]), m)
    return {"sets": sets, "orders": factorial(n) if shuffle else 1, "per_student": n}


def draw_for(pool, n, shuffle, rng):
    """pool: [{question_id, difficulty, qtype}] in organiser order. Every student gets the same mix of question kind and difficulty."""
    if n and n < len(pool):
        groups = {}
        for q in pool:
            groups.setdefault(("answer" if q["qtype"] in ANSWER_TYPES else "code", q["difficulty"]), []).append(q)
        keys = sorted(groups)
        pick = []
        for k, m in zip(keys, _split(n, [len(groups[k]) for k in keys])):
            pick += rng.sample(groups[k], m)
        chosen = {q["question_id"] for q in pick}
        pool = [q for q in pool if q["question_id"] in chosen]
    ids = [q["question_id"] for q in pool]
    if shuffle:
        rng.shuffle(ids)
    return ids


def assigned(c, comp, uid, create=True):
    """The questions this student sees, in their order. Fixed at first look and stored, so a reload never changes it."""
    p = db.one(c, "SELECT questions FROM competition_participants WHERE competition_id=? AND user_id=?", comp["id"], uid)
    if p and p["questions"]:
        return db.jl(p["questions"])
    pool = db.all_(c, "SELECT cq.question_id, q.difficulty, qv.qtype FROM competition_questions cq JOIN questions q ON q.id=cq.question_id "
                      "JOIN question_versions qv ON qv.id=cq.version_id WHERE cq.competition_id=? ORDER BY cq.ordinal", comp["id"])
    if not comp["draw"] or comp["draw"] >= len(pool):
        base = [q["question_id"] for q in pool]
        if comp["shuffle"]:
            random.Random(comp["id"]).shuffle(base)   # one mixed base order per competition, then rotated per student
            k = db.one(c, "SELECT COUNT(*) AS n FROM competition_participants WHERE competition_id=? AND questions IS NOT NULL", comp["id"])["n"]
            base = rotated(base, k)
        ids = base
    else:   # a draw from a bigger list: own questions, same mix, random order
        ids = draw_for(pool, comp["draw"], bool(comp["shuffle"]), random.Random(secrets.randbits(64)))
    if create and p:
        c.execute("UPDATE competition_participants SET questions=? WHERE competition_id=? AND user_id=? AND questions IS NULL", (json.dumps(ids), comp["id"], uid))
        return db.jl(db.one(c, "SELECT questions FROM competition_participants WHERE competition_id=? AND user_id=?", comp["id"], uid)["questions"])
    return ids


def shuffled_options(opts, uid, qid):
    o = list(opts)
    random.Random(f"{uid}:{qid}").shuffle(o)
    return o


DEFAULT_LOCKDOWN = {"internet": "flag", "fullscreen": "required", "leave": "warn", "max_violations": 0, "no_copy": False, "no_paste": False}
_LD_CHOICES = {"internet": ("flag", "block", "allow"), "fullscreen": ("required", "off"), "leave": ("warn", "allow")}


def lockdown_cfg(raw):
    """Exam lockdown rules for a competition (stored JSON or a dict from the admin form), always complete and valid."""
    d = db.jl(raw, {}) if isinstance(raw, str) else (raw or {})
    cfg = dict(DEFAULT_LOCKDOWN)
    for k, ok in _LD_CHOICES.items():
        if d.get(k) in ok:
            cfg[k] = d[k]
    try:
        cfg["max_violations"] = max(0, min(50, int(d.get("max_violations") or 0)))
    except (TypeError, ValueError):
        pass
    cfg["no_copy"], cfg["no_paste"] = bool(d.get("no_copy")), bool(d.get("no_paste"))
    return cfg


def scoring_cfg(comp):
    cfg = dict(DEFAULT_SCORING)
    cfg.update(db.jl(comp["scoring"], {}) if isinstance(comp["scoring"], str) else (comp["scoring"] or {}))
    return cfg


def end_time(comp):
    if not comp["start_at"]:
        return None
    return comp["start_at"] + (comp["duration_min"] + comp["extra_min"]) * 60 + comp["paused_total_sec"] + (
        (db.now() - comp["paused_at"]) if comp["status"] == "paused" and comp["paused_at"] else 0)


def display_status(comp, t=None):
    t = t or db.now()
    s = comp["status"]
    if s == "draft":
        return "Draft"
    if s == "cancelled":
        return "Cancelled"
    if s == "archived":
        return "Archived"
    if s in ("ended", "finalized"):
        return "Completed"
    if s == "paused":
        return "Paused"
    if s == "live":
        return "Live"
    if comp["reg_start"] and t < comp["reg_start"]:
        return "Upcoming"
    if (not comp["reg_end"] or t <= comp["reg_end"]) and (not comp["start_at"] or t < end_time(comp)):
        return "Registration Open"
    return "Registration Closed"


def tick(c, comp):
    """Lazy state transitions: auto-start scheduled competitions, auto-end when time is up. Returns fresh row."""
    t = db.now()
    changed = False
    if comp["status"] == "published" and comp["auto_start"] and comp["start_at"] and t >= comp["start_at"]:
        c.execute("UPDATE competitions SET status='live', started_at=COALESCE(started_at,?), updated_at=? WHERE id=? AND status='published'",
                  (t, t, comp["id"]))
        changed = True
    elif comp["status"] == "live" and comp["start_at"] and t >= end_time(comp):
        c.execute("UPDATE competitions SET status='ended', ended_at=?, updated_at=? WHERE id=? AND status='live'", (t, t, comp["id"]))
        c.execute("INSERT INTO audit_logs(user_id,action,entity,entity_id,competition_id,detail,created_at) VALUES(NULL,'competition.auto_end','competition',?,?,NULL,?)",
                  (comp["id"], comp["id"], t))
        changed = True
    elif (comp["status"] in ("ended", "finalized", "archived") and comp.get("results_at") and not comp.get("results_published_at") and t >= comp["results_at"]
          and not db.one(c, "SELECT 1 AS x FROM submissions WHERE competition_id=? AND status IN ('queued','running') LIMIT 1", comp["id"])):
        c.execute("UPDATE competitions SET results_published_at=?, updated_at=? WHERE id=? AND results_published_at IS NULL", (t, t, comp["id"]))
        c.execute("INSERT INTO announcements(competition_id,title,body,created_by,created_at) VALUES(?,?,?,NULL,?)",
                  (comp["id"], "Results are out!", "Open your profile or the leaderboard to see your rank.", t))
        changed = True
    return db.one(c, "SELECT * FROM competitions WHERE id=?", comp["id"]) if changed else comp


def get_comp(c, comp_id):
    comp = db.one(c, "SELECT * FROM competitions WHERE id=?", comp_id)
    return tick(c, comp) if comp else None


def remaining(comp):
    e = end_time(comp)
    return max(0, e - db.now()) if e else None


def results_visible(comp):
    """Whether students may see scores/ranks of this competition yet."""
    if comp.get("results_published_at"):
        return True
    vis, st = comp["leaderboard_visibility"], comp["status"]
    if st in ("draft", "cancelled"):
        return False
    if vis == "live":
        return True
    if vis == "after_end":
        return st in ("ended", "finalized", "archived")
    return False  # 'hidden' = organisers publish manually


def compute_leaderboard(c, comp):
    """Score = sum over questions of best submission score (+bonus - negative marks). Reproducible from submissions."""
    cfg = scoring_cfg(comp)
    qs = db.all_(c, "SELECT cq.question_id, cq.marks, cq.ordinal, qv.title FROM competition_questions cq "
                    "JOIN question_versions qv ON qv.id=cq.version_id WHERE cq.competition_id=? ORDER BY cq.ordinal", comp["id"])
    marks = {q["question_id"]: q["marks"] for q in qs}
    parts = db.all_(c, "SELECT cp.user_id, cp.status, u.name, u.avatar, s.participant_id, s.department, s.year FROM competition_participants cp "
                       "JOIN users u ON u.id=cp.user_id LEFT JOIN students s ON s.user_id=u.id WHERE cp.competition_id=?", comp["id"])
    mine = {r["user_id"]: db.jl(r["questions"]) for r in db.all_(c, "SELECT user_id, questions FROM competition_participants WHERE competition_id=? AND questions IS NOT NULL", comp["id"])}
    warns = {r["user_id"]: r["n"] for r in db.all_(c, "SELECT a.user_id, COUNT(*) AS n FROM activity_events a WHERE a.competition_id=? AND a.type='violation' AND a.created_at >= "
                                                     "COALESCE((SELECT MAX(r.created_at) FROM activity_events r WHERE r.competition_id=a.competition_id AND r.user_id=a.user_id AND r.type='reopened'), 0) "
                                                     "GROUP BY a.user_id", comp["id"])}
    fin = {r["user_id"]: r["finished_at"] for r in db.all_(c, "SELECT user_id, finished_at FROM participant_finish WHERE competition_id=?", comp["id"])}
    subs = db.all_(c, "SELECT id, user_id, question_id, verdict, score, created_at FROM submissions WHERE competition_id=? AND kind='submit' "
                      "AND status='done' AND verdict NOT IN ('IE','CE') ORDER BY created_at, id", comp["id"])
    start = comp["started_at"] or comp["start_at"] or 0
    first_ac = {}
    for s in subs:
        if s["verdict"] == "AC" and s["question_id"] not in first_ac:
            first_ac[s["question_id"]] = s["user_id"]
    state = {}
    for s in subs:
        if s["question_id"] not in marks:
            continue
        st = state.setdefault(s["user_id"], {}).setdefault(s["question_id"], {"best": 0.0, "best_at": None, "wrong": 0, "wrong_before_best": 0, "ac": False, "tries": 0})
        st["tries"] += 1
        sc = s["score"] if cfg["mode"] == "partial" else (marks[s["question_id"]] if s["verdict"] == "AC" else 0)
        if sc > st["best"]:
            st["best"], st["best_at"], st["wrong_before_best"] = sc, s["created_at"], st["wrong"]
        if s["verdict"] == "AC":
            st["ac"] = True
        elif not st["ac"]:
            st["wrong"] += 1
    rows = []
    for p in parts:
        if p["status"] == "disqualified":
            continue
        per, total, solved, penalty, last = {}, 0.0, 0, 0, 0
        own = set(mine.get(p["user_id"]) or marks)
        for q in qs:
            if q["question_id"] not in own:
                continue
            st = state.get(p["user_id"], {}).get(q["question_id"])
            if not st:
                continue
            sc = st["best"]
            if st["ac"] and first_ac.get(q["question_id"]) == p["user_id"]:
                sc += cfg["first_blood_bonus"]
            sc = max(0.0, sc - cfg["negative_marks"] * st["wrong"])
            total += sc
            if st["ac"]:
                solved += 1
            if st["best_at"]:
                mins = max(0, (st["best_at"] - start) // 60)
                penalty += mins + st["wrong_before_best"] * cfg["penalty_minutes"]
                last = max(last, st["best_at"])
            per[str(q["question_id"])] = {"score": round(sc, 2), "ac": st["ac"], "tries": st["tries"]}
        n_warn = warns.get(p["user_id"], 0)
        cut = round(min(total, n_warn * float(cfg.get("violation_pct") or 0) / 100 * sum(marks[i] for i in own)), 2) if n_warn else 0
        total -= cut
        rows.append({"user_id": p["user_id"], "name": p["name"], "avatar": avatar_url(p["user_id"], p["avatar"]), "participant_id": p["participant_id"], "department": p["department"],
                     "year": p["year"], "score": round(total, 2), "solved": solved, "penalty": int(penalty), "last": last, "per": per,
                     "warnings": n_warn, "deducted": cut, "finished_at": fin.get(p["user_id"])})
    # Same rule for everyone: higher score, then less penalty time (time taken + wrong attempts), then whoever reached
    # that score first, then whoever finished first. Names never decide a rank: equal on all of these = shared rank.
    late = 10**12
    if cfg["tie_break"] == "time":
        tk = lambda r: (-r["score"], r["last"] or late, r["finished_at"] or late)
    else:
        tk = lambda r: (-r["score"], r["penalty"], r["last"] or late, r["finished_at"] or late)
    rows.sort(key=lambda r: (tk(r), r["name"]))
    groups = {}
    for r in rows:
        groups.setdefault(tk(r), []).append(r)
    tied = [g for g in groups.values() if len(g) > 1 and g[0]["score"] > 0]
    if tied:   # still exactly equal: average rule-based quality of their accepted code decides (never changes marks)
        from . import feedback
        for g in tied:
            for r in g:
                codes = db.all_(c, "SELECT code, language FROM submissions WHERE id IN (SELECT MAX(id) FROM submissions WHERE competition_id=? AND user_id=? "
                                   "AND kind='submit' AND verdict='AC' AND language NOT IN ('answer','prompt') GROUP BY question_id)", comp["id"], r["user_id"])
                r["quality"] = round(sum(feedback.quality(x["code"], x["language"])["score"] for x in codes) / len(codes)) if codes else None
        tk0 = tk
        tk = lambda r: (*tk0(r), -(r.get("quality") or 0) if r.get("quality") is not None else 0)
        rows.sort(key=lambda r: (tk(r), r["name"]))
    prev, rank = None, 0
    for i, r in enumerate(rows):
        key = tk(r)
        if key != prev:
            rank, prev = i + 1, key
        r["rank"] = rank
    return {"questions": [{"id": q["question_id"], "label": chr(65 + i), "title": q["title"], "marks": q["marks"]} for i, q in enumerate(qs)],
            "rows": rows, "tie_break": cfg["tie_break"]}


_LB, _LB_LOCKS, _LB_GUARD = {}, {}, threading.Lock()


def _lb_version(c, comp):
    """Cheap fingerprint of everything the ranking depends on (all indexed lookups)."""
    s = db.one(c, "SELECT COUNT(*) AS n, MAX(judged_at) AS t FROM submissions WHERE competition_id=? AND kind='submit' AND status='done'", comp["id"])
    v = db.one(c, "SELECT COUNT(*) AS n FROM activity_events WHERE competition_id=? AND type IN ('violation','reopened')", comp["id"])
    o = db.one(c, "SELECT (SELECT COUNT(*) FROM participant_finish WHERE competition_id=?) AS f, (SELECT COUNT(*) FROM competition_participants WHERE competition_id=?) AS p,"
                  " (SELECT COUNT(*) FROM competition_participants WHERE competition_id=? AND questions IS NOT NULL) AS q", comp["id"], comp["id"], comp["id"])
    return (comp["status"], comp.get("results_published_at"), comp["scoring"], s["n"], s["t"], v["n"], o["f"], o["p"], o["q"])


def leaderboard(c, comp, fresh=False):
    """Shared, cached ranking. During a live round 500 students poll it and press Finish together; computing it once per
    change (and at most every 2 s while live) instead of once per request keeps every request fast."""
    with _LB_GUARD:
        lock = _LB_LOCKS.setdefault(comp["id"], threading.Lock())
    with lock:   # concurrent callers wait for the one computation and reuse it
        hit = _LB.get(comp["id"])
        if hit and not fresh and comp["status"] in ("live", "paused") and time.time() - hit[0] < 2:
            return hit[2]
        ver = _lb_version(c, comp)
        if hit and hit[1] == ver:
            return hit[2]
        lb = _leaderboard(c, comp)
        _LB[comp["id"]] = (time.time(), ver, lb)
        return lb


def _leaderboard(c, comp):
    """Finalized competitions read the frozen snapshot; everything else is computed live."""
    if comp["status"] in ("finalized", "archived") and db.one(c, "SELECT 1 AS x FROM leaderboard_entries WHERE competition_id=? LIMIT 1", comp["id"]):
        lb = compute_leaderboard(c, comp)  # for question headers
        snap = db.all_(c, "SELECT le.*, u.name, u.avatar, s.participant_id, s.department, s.year FROM leaderboard_entries le JOIN users u ON u.id=le.user_id "
                          "LEFT JOIN students s ON s.user_id=u.id WHERE le.competition_id=? ORDER BY le.rank, u.name", comp["id"])
        live = {r["user_id"]: r for r in lb["rows"]}   # warnings are not in the snapshot; they never change after the end
        lb["rows"] = [{**r, "avatar": avatar_url(r["user_id"], r["avatar"]), "per": db.jl(r["detail"], {}), "last": None,
                       "warnings": live.get(r["user_id"], {}).get("warnings", 0), "deducted": live.get(r["user_id"], {}).get("deducted", 0)} for r in snap]
        lb["frozen"] = True
        return lb
    return compute_leaderboard(c, comp)


def finalize(c, comp):
    lb = compute_leaderboard(c, comp)
    c.execute("DELETE FROM leaderboard_entries WHERE competition_id=?", (comp["id"],))
    for r in lb["rows"]:
        c.execute("INSERT INTO leaderboard_entries(competition_id,user_id,rank,score,solved,penalty,detail) VALUES(?,?,?,?,?,?,?)",
                  (comp["id"], r["user_id"], r["rank"], r["score"], r["solved"], r["penalty"], json.dumps(r["per"])))
    return lb


def semester_leaderboard(c, sem_id):
    comps = db.all_(c, "SELECT sc.points, c.* FROM semester_competitions sc JOIN competitions c ON c.id=sc.competition_id "
                       "WHERE sc.semester_id=? AND c.status NOT IN ('draft','cancelled') ORDER BY c.start_at", sem_id)
    totals = {}
    cols = []
    for comp in comps:
        if not results_visible(comp):
            continue  # unpublished results don't count yet
        lb = leaderboard(c, comp)
        mx = sum(q["marks"] for q in lb["questions"]) or comp["max_marks"] or 1
        cols.append({"id": comp["id"], "code": comp["code"], "title": comp["title"], "round_no": comp["round_no"], "points": comp["points"],
                     "status": display_status(comp)})
        for r in lb["rows"]:
            pts = round(r["score"] / mx * comp["points"], 2)
            t = totals.setdefault(r["user_id"], {"user_id": r["user_id"], "name": r["name"], "avatar": r.get("avatar"), "participant_id": r["participant_id"],
                                                  "department": r["department"], "year": r["year"], "points": 0.0, "solved": 0, "rounds": {}, "played": 0})
            t["points"] = round(t["points"] + pts, 2)
            t["solved"] += r["solved"]
            t["played"] += 1
            t["rounds"][str(comp["id"])] = pts
    rows = sorted(totals.values(), key=lambda r: (-r["points"], -r["solved"], r["name"]))
    prev, rank = None, 0
    for i, r in enumerate(rows):
        if r["points"] != prev:
            rank, prev = i + 1, r["points"]
        r["rank"] = rank
    return {"competitions": cols, "rows": rows}


def explain_ranks(lb, cfg, start=0):
    """Plain reason for each place: what put this student above the next one. Same rule for everyone."""
    rows, tb = lb["rows"], cfg.get("tie_break", "penalty")
    hm = lambda t: f"{(t - start) // 60} min" if t and start else "—"
    for i, r in enumerate(rows):
        why = []
        if r.get("deducted"):
            why.append(f"−{r['deducted']} marks for {r['warnings']} fair-play warning(s)")
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        if not nxt:
            pass
        elif r["score"] > nxt["score"]:
            why.insert(0, f"Higher score than the next place ({r['score']} vs {nxt['score']})")
        elif tb == "penalty" and r["penalty"] < nxt["penalty"]:
            why.insert(0, f"Same score as the next place; less penalty time ({r['penalty']} vs {nxt['penalty']} min: time taken plus wrong attempts)")
        elif r.get("last") and nxt.get("last") and r["last"] < nxt["last"]:
            why.insert(0, f"Same score; reached it earlier ({hm(r['last'])} vs {hm(nxt['last'])} after the start)")
        elif r.get("finished_at") and (not nxt.get("finished_at") or r["finished_at"] < nxt["finished_at"]):
            why.insert(0, "Same score and time; pressed Finish earlier")
        elif r.get("quality") is not None and nxt.get("quality") is not None and r["quality"] > nxt["quality"]:
            why.insert(0, f"Equal on everything else; cleaner code ({r['quality']} vs {nxt['quality']} on the code quality check)")
        elif r["rank"] == nxt["rank"]:
            why.insert(0, "Exactly equal on every rule: shares the rank")
        r["why"] = why
    return {"order": ["Higher score (sum of the best mark on each question, minus fair-play deductions)"] +
                     (["Less penalty time (minutes to each scoring submission + %s min per earlier wrong attempt)" % cfg.get("penalty_minutes", 10)] if tb == "penalty" else []) +
                     ["Reached that score earlier", "Pressed Finish earlier", "Cleaner code (rule-based quality check of accepted programs; never changes marks)",
                      "Still equal: same rank (names never decide)"],
            "deduction": f"Each fair-play warning costs {cfg.get('violation_pct', 0)}% of the student's maximum marks." if cfg.get("violation_pct") else ""}


def avatar_url(uid, av):
    return f"/api/avatar/{uid}?v={av.split(':')[-1]}" if av else None


def display_name(name, pid, mode):
    if mode == "participant_id":
        return pid or "—"
    if mode == "initials":
        parts = (name or "").split()
        return (" ".join(p[0] + "." for p in parts[:-1]) + " " + parts[-1]).strip() if len(parts) > 1 else (name or "")
    return name
