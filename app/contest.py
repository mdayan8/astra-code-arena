"""Competition timing/state and leaderboard scoring (pure functions over stored rows => reproducible)."""
import json

from . import db

DEFAULT_SCORING = {
    "mode": "partial",          # partial (by test weight) | all_or_nothing
    "negative_marks": 0,        # deducted per wrong submission on a question (never below 0 for that question)
    "first_blood_bonus": 0,     # extra marks for the first full solve of each question
    "penalty_minutes": 10,      # tie-break penalty per wrong attempt before the best submission
    "tie_break": "penalty",     # penalty (time + wrong attempts) | time (time of last score improvement)
}


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
        for q in qs:
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
        rows.append({"user_id": p["user_id"], "name": p["name"], "avatar": avatar_url(p["user_id"], p["avatar"]), "participant_id": p["participant_id"], "department": p["department"],
                     "year": p["year"], "score": round(total, 2), "solved": solved, "penalty": int(penalty), "last": last, "per": per})
    if cfg["tie_break"] == "time":
        rows.sort(key=lambda r: (-r["score"], r["last"] or 10**12, r["name"]))
    else:
        rows.sort(key=lambda r: (-r["score"], r["penalty"], r["last"] or 10**12, r["name"]))
    prev, rank = None, 0
    for i, r in enumerate(rows):
        key = (r["score"], r["penalty"]) if cfg["tie_break"] != "time" else (r["score"], r["last"])
        if key != prev:
            rank, prev = i + 1, key
        r["rank"] = rank
    return {"questions": [{"id": q["question_id"], "label": chr(65 + i), "title": q["title"], "marks": q["marks"]} for i, q in enumerate(qs)],
            "rows": rows, "tie_break": cfg["tie_break"]}


def leaderboard(c, comp):
    """Finalized competitions read the frozen snapshot; everything else is computed live."""
    if comp["status"] in ("finalized", "archived") and db.one(c, "SELECT 1 AS x FROM leaderboard_entries WHERE competition_id=? LIMIT 1", comp["id"]):
        lb = compute_leaderboard(c, comp)  # for question headers
        snap = db.all_(c, "SELECT le.*, u.name, u.avatar, s.participant_id, s.department, s.year FROM leaderboard_entries le JOIN users u ON u.id=le.user_id "
                          "LEFT JOIN students s ON s.user_id=u.id WHERE le.competition_id=? ORDER BY le.rank, u.name", comp["id"])
        lb["rows"] = [{**r, "avatar": avatar_url(r["user_id"], r["avatar"]), "per": db.jl(r["detail"], {}), "last": None} for r in snap]
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


def avatar_url(uid, av):
    return f"/api/avatar/{uid}?v={av.split(':')[-1]}" if av else None


def display_name(name, pid, mode):
    if mode == "participant_id":
        return pid or "—"
    if mode == "initials":
        parts = (name or "").split()
        return (" ".join(p[0] + "." for p in parts[:-1]) + " " + parts[-1]).strip() if len(parts) > 1 else (name or "")
    return name
