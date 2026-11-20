"""Public + student APIs. Nothing here ever returns hidden test data, reference solutions or other students' code."""
import json
import re

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import config, contest, db, judge
from .security import (STUDENT_COOKIE, attempt_login, check_password_strength, client_ip, current_user, hash_password,
                       login, logout, rate_limit, verify_password)

router = APIRouter()


def conn():
    c = db.connect()
    try:
        yield c
    finally:
        c.close()


def student(req: Request, c=Depends(conn)):
    return current_user(c, req, STUDENT_COOKIE, "student")


def maybe_student(req: Request, c):
    try:
        return current_user(c, req, STUDENT_COOKIE, "student")
    except HTTPException:
        return None


PUBLIC_COMP_FIELDS = ("id", "code", "title", "description", "instructions", "academic_year", "semester_label", "round_no", "category",
                      "registration_mode", "reg_start", "reg_end", "start_at", "duration_min", "extra_min", "max_marks",
                      "leaderboard_visibility", "allow_late_join", "show_test_counts", "paste_policy", "status")


def public_comp(c, comp, me=None):
    d = {k: comp[k] for k in PUBLIC_COMP_FIELDS}
    d["languages"] = db.jl(comp["languages"])
    d["eligible_years"] = db.jl(comp["eligible_years"])
    d["eligible_departments"] = db.jl(comp["eligible_departments"])
    d["scoring"] = contest.scoring_cfg(comp)
    d["lockdown"] = contest.lockdown_cfg(comp["lockdown"])
    d["display_status"] = contest.display_status(comp)
    d["end_at"] = contest.end_time(comp)
    d["remaining_sec"] = contest.remaining(comp) if comp["status"] in ("live", "paused") else None
    d["question_count"] = db.one(c, "SELECT COUNT(*) AS n FROM competition_questions WHERE competition_id=?", comp["id"])["n"]
    d["participant_count"] = db.one(c, "SELECT COUNT(*) AS n FROM competition_participants WHERE competition_id=?", comp["id"])["n"]
    if me:
        p = db.one(c, "SELECT status, joined_at FROM competition_participants WHERE competition_id=? AND user_id=?", comp["id"], me["id"])
        d["joined"] = bool(p)
        d["disqualified"] = bool(p and p["status"] == "disqualified")
        d["can_join"], d["join_reason"] = can_join(c, comp, me)
    return d


def can_join(c, comp, me):
    t = db.now()
    if db.one(c, "SELECT 1 AS x FROM competition_participants WHERE competition_id=? AND user_id=?", comp["id"], me["id"]):
        return False, "Already registered"
    if comp["registration_mode"] == "invite_only":
        return False, "Registration is by organiser invitation only"
    if comp["status"] not in ("published", "live", "paused"):
        return False, "Registration is not open"
    if comp["status"] in ("live", "paused") and not comp["allow_late_join"]:
        return False, "Registration closed when the competition started"
    if comp["reg_start"] and t < comp["reg_start"]:
        return False, "Registration has not opened yet"
    if comp["reg_end"] and t > comp["reg_end"] and not comp["allow_late_join"]:
        return False, "Registration deadline has passed"
    s = db.one(c, "SELECT * FROM students WHERE user_id=?", me["id"]) or {}
    yrs, deps = db.jl(comp["eligible_years"]), db.jl(comp["eligible_departments"])
    if yrs and s.get("year") not in yrs:
        return False, "Not open to your academic year"
    if deps and (s.get("department") or "").upper() not in [d.upper() for d in deps]:
        return False, "Not open to your department"
    return True, ""


def visible_comps(c):
    rows = db.all_(c, "SELECT * FROM competitions WHERE status NOT IN ('draft','cancelled') ORDER BY COALESCE(start_at, created_at) DESC")
    return [contest.tick(c, r) for r in rows]


def md_ok(s, n=20000):
    return (s or "")[:n]


# ---------------- public ----------------

@router.get("/api/public/info")
def info(c=Depends(conn)):
    return {"name": "ASTRA Code Arena", "org": "ASTRA Club · PESITM Shivamogga", "timezone": config.APP_TIMEZONE,
            "registration_open": db.setting(c, "registration_open") == "true", "banner": db.setting(c, "site_banner"),
            "highlights_enabled": db.setting(c, "highlights_enabled") == "true", "departments": departments(c),
            "languages": {k: {"name": v["name"], "monaco": v["monaco"], "template": judge.TEMPLATES.get(k, "")} for k, v in judge.LANGS.items()},
            "server_time": db.now()}


@router.get("/api/public/competitions")
def list_comps(req: Request, c=Depends(conn)):
    me = maybe_student(req, c)
    return [public_comp(c, x, me) for x in visible_comps(c) if x["status"] != "archived"]


@router.get("/api/public/competitions/{code}")
def comp_detail(code: str, req: Request, c=Depends(conn)):
    comp = db.one(c, "SELECT * FROM competitions WHERE (code=? OR CAST(id AS TEXT)=?) AND status NOT IN ('draft','cancelled')", code, code)
    if not comp:
        raise HTTPException(404, "Competition not found.")
    comp = contest.tick(c, comp)
    d = public_comp(c, comp, maybe_student(req, c))
    d["announcements"] = db.all_(c, "SELECT id, title, body, created_at FROM announcements WHERE competition_id=? ORDER BY id DESC LIMIT 20", comp["id"])
    return d


@router.get("/api/public/announcements")
def announcements(c=Depends(conn)):
    return db.all_(c, "SELECT a.id, a.title, a.body, a.created_at, a.competition_id, co.title AS competition FROM announcements a "
                      "LEFT JOIN competitions co ON co.id=a.competition_id WHERE a.competition_id IS NULL OR co.status IN ('published','live','paused') "
                      "ORDER BY a.id DESC LIMIT 10")


@router.get("/api/public/highlights")
def highlights(c=Depends(conn)):
    if db.setting(c, "highlights_enabled") != "true":
        return []
    mode = db.setting(c, "name_display", "full")
    out = []
    for comp in [x for x in db.all_(c, "SELECT * FROM competitions WHERE status IN ('ended','finalized','archived') ORDER BY COALESCE(finalized_at, ended_at) DESC LIMIT 8") if contest.results_visible(x)][:4]:
        lb = contest.leaderboard(c, comp)
        out.append({"id": comp["id"], "code": comp["code"], "title": comp["title"], "round_no": comp["round_no"],
                    "participants": len(lb["rows"]),
                    "top": [{"rank": r["rank"], "name": contest.display_name(r["name"], r["participant_id"], mode), "score": r["score"]} for r in lb["rows"][:3]]})
    return out


@router.get("/api/competitions/{comp_id}/leaderboard")
def comp_leaderboard(comp_id: int, req: Request, c=Depends(conn)):
    comp = contest.get_comp(c, comp_id)
    if not comp or comp["status"] in ("draft", "cancelled"):
        raise HTTPException(404, "Competition not found.")
    vis = comp["leaderboard_visibility"]
    if not contest.results_visible(comp):
        return {"hidden": True, "reason": "Results will be published by the organisers soon." if vis == "hidden" else "The leaderboard is revealed when the competition ends.",
                "competition": {"id": comp["id"], "title": comp["title"], "code": comp["code"], "display_status": contest.display_status(comp)}}
    me = maybe_student(req, c)
    mode = db.setting(c, "name_display", "full")
    lb = contest.leaderboard(c, comp)
    rows = [{"rank": r["rank"], "name": contest.display_name(r["name"], r["participant_id"], mode), "score": r["score"], "solved": r["solved"],
             "penalty": r["penalty"], "per": r["per"], "me": bool(me and me["id"] == r["user_id"]), "department": r["department"],
             "avatar": r.get("avatar") if mode == "full" else None} for r in lb["rows"]]
    return {"competition": {"id": comp["id"], "title": comp["title"], "code": comp["code"], "round_no": comp["round_no"],
                            "display_status": contest.display_status(comp), "remaining_sec": contest.remaining(comp) if comp["status"] in ("live", "paused") else None},
            "questions": lb["questions"], "rows": rows, "tie_break": lb["tie_break"], "frozen": lb.get("frozen", False),
            "penalty_minutes": contest.scoring_cfg(comp)["penalty_minutes"]}


def semester_board(c, sem):
    if sem["status"] in ("frozen", "finalized", "archived"):
        snap = db.setting(c, f"semester_snapshot:{sem['id']}", "")
        if snap:
            return json.loads(snap)
    return contest.semester_leaderboard(c, sem["id"])


@router.get("/api/semesters")
def semesters(c=Depends(conn)):
    return db.all_(c, "SELECT id, name, academic_year, term, status FROM semesters ORDER BY id DESC")


@router.get("/api/semesters/{sid}/leaderboard")
def sem_lb(sid: int, req: Request, c=Depends(conn)):
    sem = db.one(c, "SELECT * FROM semesters WHERE id=?", sid)
    if not sem:
        raise HTTPException(404, "Semester not found.")
    me = maybe_student(req, c)
    mode = db.setting(c, "name_display", "full")
    lb = semester_board(c, sem)
    rows = [{"rank": r["rank"], "name": contest.display_name(r["name"], r["participant_id"], mode), "points": r["points"], "solved": r["solved"],
             "played": r["played"], "rounds": r["rounds"], "department": r["department"], "me": bool(me and me["id"] == r["user_id"])} for r in lb["rows"]]
    return {"semester": sem, "competitions": lb["competitions"], "rows": rows}


@router.get("/api/academic-years/{year}/leaderboard")
def year_lb(year: str, req: Request, c=Depends(conn)):
    me = maybe_student(req, c)
    mode = db.setting(c, "name_display", "full")
    totals = {}
    for sem in db.all_(c, "SELECT * FROM semesters WHERE academic_year=?", year):
        for r in semester_board(c, sem)["rows"]:
            t = totals.setdefault(r["user_id"], {"user_id": r["user_id"], "name": r["name"], "participant_id": r["participant_id"],
                                                  "department": r["department"], "points": 0, "solved": 0, "played": 0})
            t["points"] = round(t["points"] + r["points"], 2)
            t["solved"] += r["solved"]
            t["played"] += r["played"]
    rows = sorted(totals.values(), key=lambda r: (-r["points"], -r["solved"]))
    for i, r in enumerate(rows):
        r["rank"] = i + 1 if i == 0 or r["points"] != rows[i - 1]["points"] else rows[i - 1]["rank"]
    return {"academic_year": year, "rows": [{"rank": r["rank"], "name": contest.display_name(r["name"], r["participant_id"], mode),
                                             "points": r["points"], "solved": r["solved"], "played": r["played"], "department": r["department"],
                                             "me": bool(me and me["id"] == r["user_id"])} for r in rows]}


# ---------------- auth ----------------

class LoginIn(BaseModel):
    username: str = Field(max_length=120)
    password: str = Field(max_length=200)


class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    usn: str = Field(min_length=3, max_length=20)
    department: str = Field(min_length=1, max_length=40)
    year: int = Field(ge=1, le=5)
    semester: int | None = Field(default=None, ge=1, le=10)
    section: str = Field(default="", max_length=10)
    email: str = Field(default="", max_length=120)
    dob: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


def check_dob(dob: str) -> str:
    import datetime
    try:
        d = datetime.date.fromisoformat(dob)
    except ValueError:
        raise HTTPException(400, "Enter a valid date of birth.")
    if not (1950 <= d.year <= datetime.date.today().year - 10):
        raise HTTPException(400, "Enter a valid date of birth.")
    return d.isoformat()


def dob_password(dob: str) -> str:
    """Default student password = date of birth as DDMMYYYY."""
    y, m, d = dob.split("-")
    return f"{d}{m}{y}"


def departments(c):
    return db.jl(db.setting(c, "departments", "[]"))


def check_department(c, dep: str) -> str:
    dep = dep.strip().upper()
    allowed = [d.upper() for d in departments(c)]
    if allowed and dep not in allowed:
        raise HTTPException(400, "Choose your department from the list.")
    return dep


def norm_name(n):
    return " ".join((n or "").lower().split())


def new_participant_id(c):
    yy = __import__("time").strftime("%y")
    n = db.one(c, "SELECT COUNT(*) AS n FROM students")["n"] + 1
    while True:
        pid = f"AST{yy}-{n:04d}"
        if not db.one(c, "SELECT 1 AS x FROM students WHERE participant_id=?", pid):
            return pid
        n += 1


def me_payload(c, u, csrf=None):
    s = db.one(c, "SELECT participant_id, usn, department, year, section, semester FROM students WHERE user_id=?", u["id"]) or {}
    av = db.one(c, "SELECT avatar FROM users WHERE id=?", u["id"]) or {}
    s["avatar"] = contest.avatar_url(u["id"], av.get("avatar"))
    out = {"id": u["id"], "name": u["name"], "username": u["username"], "email": u["email"], "must_change_pw": bool(u["must_change_pw"]), **s}
    if csrf:
        out["csrf"] = csrf
    return out


@router.post("/api/auth/login")
def student_login(body: LoginIn, req: Request, resp: Response, c=Depends(conn)):
    u = attempt_login(c, body.username, body.password, "student", req)
    csrf = login(c, u, req, resp, STUDENT_COOKIE)
    return me_payload(c, u, csrf)


@router.post("/api/auth/register")
def register(body: RegisterIn, req: Request, resp: Response, c=Depends(conn)):
    if db.setting(c, "registration_open") != "true":
        raise HTTPException(403, "Self-registration is closed. Ask the organisers for your participant account.")
    rate_limit(f"register:{client_ip(req)}", 300, 600)  # lab PCs are shared by several students
    usn = body.usn.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{3,20}", usn):
        raise HTTPException(400, "USN may contain only letters and digits.")
    if db.one(c, "SELECT 1 AS x FROM students WHERE usn=?", usn) or db.one(c, "SELECT 1 AS x FROM users WHERE username=?", usn):
        raise HTTPException(409, "An account with this USN already exists. Sign in instead.")
    t = db.now()
    dob = check_dob(body.dob)
    dep = check_department(c, body.department)
    with db.tx(c):
        uid = c.execute("INSERT INTO users(username,email,name,password_hash,kind,created_at) VALUES(?,?,?,?, 'student', ?)",
                        (usn, body.email.strip() or None, " ".join(body.name.split()), hash_password(dob_password(dob)), t)).lastrowid
        c.execute("INSERT INTO students(user_id,participant_id,usn,department,year,section,dob,semester) VALUES(?,?,?,?,?,?,?,?)",
                  (uid, new_participant_id(c), usn, dep, body.year, body.section.strip().upper(), dob, body.semester))
    u = db.one(c, "SELECT * FROM users WHERE id=?", uid)
    csrf = login(c, u, req, resp, STUDENT_COOKIE)
    return me_payload(c, u, csrf)


@router.post("/api/auth/logout")
def student_logout(req: Request, resp: Response, c=Depends(conn)):
    logout(c, req, resp, STUDENT_COOKIE)
    return {"ok": True}


class ForgotIn(BaseModel):
    usn: str = Field(max_length=40)
    name: str = Field(max_length=100)
    dob: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


@router.post("/api/auth/forgot")
def forgot(body: ForgotIn, req: Request, c=Depends(conn)):
    """Self-service reset: USN + full name + date of birth must all match; password goes back to the DOB (DDMMYYYY)."""
    rate_limit(f"forgot:{client_ip(req)}", 20, 600)
    rate_limit(f"forgot-usn:{body.usn.strip().upper()}", 5, 600)
    s = db.one(c, "SELECT s.user_id, s.dob, u.name, u.status FROM students s JOIN users u ON u.id=s.user_id WHERE s.usn=? OR s.participant_id=? COLLATE NOCASE",
               body.usn.strip().upper(), body.usn.strip())
    if not s or not s["dob"] or s["dob"] != body.dob or norm_name(s["name"]) != norm_name(body.name):
        __import__("time").sleep(0.4)
        raise HTTPException(400, "Those details don't match our records. Check the spelling of your name as registered, or ask an organiser.")
    if s["status"] != "active":
        raise HTTPException(403, "This account is suspended. Contact the organisers.")
    c.execute("UPDATE users SET password_hash=?, failed_logins=0, locked_until=0, must_change_pw=0 WHERE id=?", (hash_password(dob_password(s["dob"])), s["user_id"]))
    c.execute("DELETE FROM sessions WHERE user_id=?", (s["user_id"],))
    return {"ok": True, "message": "Done. Your password is now your date of birth as DDMMYYYY."}


class ProfileIn(BaseModel):
    semester: int | None = Field(default=None, ge=1, le=10)
    email: str = Field(default="", max_length=120)
    section: str = Field(default="", max_length=10)


@router.put("/api/me/profile")
def update_profile(body: ProfileIn, u=Depends(student), c=Depends(conn)):
    with db.tx(c):
        c.execute("UPDATE users SET email=? WHERE id=?", (body.email.strip() or None, u["id"]))
        c.execute("UPDATE students SET semester=?, section=? WHERE user_id=?", (body.semester, body.section.strip().upper(), u["id"]))
    return {"ok": True}


@router.get("/api/me")
def me(req: Request, c=Depends(conn)):
    u = maybe_student(req, c)
    if not u:
        return {"user": None}
    return {"user": me_payload(c, u, u["csrf"])}


class PwIn(BaseModel):
    current: str = Field(max_length=200)
    new: str = Field(max_length=200)


@router.post("/api/me/password")
def change_pw(body: PwIn, u=Depends(student), c=Depends(conn)):
    full = db.one(c, "SELECT password_hash FROM users WHERE id=?", u["id"])
    if not verify_password(body.current, full["password_hash"]):
        raise HTTPException(400, "Current password is incorrect.")
    check_password_strength(body.new)
    c.execute("UPDATE users SET password_hash=?, must_change_pw=0 WHERE id=?", (hash_password(body.new), u["id"]))
    return {"ok": True}


# ---------------- student ----------------

def active_semester(c):
    return db.one(c, "SELECT * FROM semesters WHERE status!='archived' ORDER BY id DESC LIMIT 1")


@router.get("/api/student/dashboard")
def dashboard(u=Depends(student), c=Depends(conn)):
    comps = visible_comps(c)
    joined = {r["competition_id"] for r in db.all_(c, "SELECT competition_id FROM competition_participants WHERE user_id=?", u["id"])}
    cards = [public_comp(c, x, u) for x in comps if x["status"] != "archived"]
    sem = active_semester(c)
    sem_info = None
    if sem:
        lb = semester_board(c, sem)
        mine = next((r for r in lb["rows"] if r["user_id"] == u["id"]), None)
        sem_info = {"id": sem["id"], "name": sem["name"], "points": mine["points"] if mine else 0, "rank": mine["rank"] if mine else None,
                    "of": len(lb["rows"])}
    solved = db.one(c, "SELECT COUNT(DISTINCT question_id) AS n FROM submissions WHERE user_id=? AND verdict='AC' AND kind IN ('submit','practice')", u["id"])["n"]
    recent = db.all_(c, "SELECT s.id, s.kind, s.language, s.verdict, s.status, s.score, s.created_at, qv.title, co.title AS competition, s.competition_id "
                        "FROM submissions s JOIN question_versions qv ON qv.id=s.version_id LEFT JOIN competitions co ON co.id=s.competition_id "
                        "WHERE s.user_id=? AND s.kind IN ('submit','practice') ORDER BY s.id DESC LIMIT 8", u["id"])
    stats = db.one(c, "SELECT COUNT(*) AS total, SUM(verdict='AC') AS ac FROM submissions WHERE user_id=? AND kind IN ('submit','practice')", u["id"])
    by_lang = db.all_(c, "SELECT language, COUNT(*) AS n FROM submissions WHERE user_id=? AND kind IN ('submit','practice') GROUP BY language", u["id"])
    return {"me": me_payload(c, u), "semester": sem_info, "competitions_joined": len(joined), "solved": solved,
            "active": [x for x in cards if x["display_status"] in ("Live", "Paused")],
            "upcoming": [x for x in cards if x["display_status"] in ("Upcoming", "Registration Open", "Registration Closed")],
            "previous": [x for x in cards if x["display_status"] == "Completed" and x["id"] in joined],
            "recent": recent, "stats": {"submissions": stats["total"] or 0, "accepted": stats["ac"] or 0, "by_language": by_lang}}


@router.post("/api/student/competitions/{comp_id}/join")
def join(comp_id: int, req: Request, u=Depends(student), c=Depends(conn)):
    comp = contest.get_comp(c, comp_id)
    if not comp or comp["status"] in ("draft", "cancelled"):
        raise HTTPException(404, "Competition not found.")
    ok, why = can_join(c, comp, u)
    if not ok:
        raise HTTPException(400, why)
    c.execute("INSERT OR IGNORE INTO competition_participants(competition_id,user_id,joined_at,source) VALUES(?,?,?,'self')", (comp_id, u["id"], db.now()))
    c.execute("INSERT INTO activity_events(competition_id,user_id,type,detail,ip,created_at) VALUES(?,?,'joined',NULL,?,?)", (comp_id, u["id"], client_ip(req), db.now()))
    return {"ok": True}


def samples(c, version_id):
    return db.all_(c, "SELECT ordinal, input, expected AS output, explanation FROM test_cases WHERE version_id=? AND visibility='sample' ORDER BY ordinal, id", version_id)


def problem_payload(c, v, extra=None):
    d = {"title": v["title"], "statement": v["statement"], "input_format": v["input_format"], "output_format": v["output_format"],
         "constraints": v["constraints"], "time_limit_ms": v["time_limit_ms"], "memory_mb": v["memory_mb"],
         "languages": db.jl(v["languages"]), "samples": samples(c, v["id"]),
         "qtype": v.get("qtype") or "standard", "starter": db.jl(v.get("starter"), {}) or {}, "options": db.jl(v.get("options"))}
    d.update(extra or {})
    return d


def arena_access(c, comp_id, u):
    comp = contest.get_comp(c, comp_id)
    if not comp or comp["status"] in ("draft", "cancelled", "published"):
        raise HTTPException(403 if comp else 404, "The problems open when the competition starts.")
    p = db.one(c, "SELECT * FROM competition_participants WHERE competition_id=? AND user_id=?", comp_id, u["id"])
    if not p:
        raise HTTPException(403, "Join this competition to see its problems.")
    return comp, p


@router.get("/api/student/competitions/{comp_id}/arena")
def arena(comp_id: int, u=Depends(student), c=Depends(conn)):
    comp, p = arena_access(c, comp_id, u)
    qs = db.all_(c, "SELECT cq.question_id, cq.marks, cq.ordinal, qv.* FROM competition_questions cq JOIN question_versions qv ON qv.id=cq.version_id "
                    "WHERE cq.competition_id=? ORDER BY cq.ordinal", comp_id)
    best = {r["question_id"]: r for r in db.all_(c, "SELECT question_id, MAX(score) AS best, MAX(verdict='AC') AS ac, COUNT(*) AS tries FROM submissions "
                                                     "WHERE competition_id=? AND user_id=? AND kind='submit' AND status='done' GROUP BY question_id", comp_id, u["id"])}
    langs = db.jl(comp["languages"])
    problems = []
    for i, q in enumerate(qs):
        b = best.get(q["question_id"], {})
        pl = problem_payload(c, q, {"id": q["question_id"], "label": chr(65 + i), "marks": q["marks"], "best": b.get("best") or 0,
                                    "solved": bool(b.get("ac")), "tries": b.get("tries") or 0})
        special = [l for l in pl["languages"] if l in ("answer", "prompt")]  # written-answer / prompt questions work in any round
        pl["languages"] = special or [l for l in pl["languages"] if l in langs] or langs
        problems.append(pl)
    fin = db.one(c, "SELECT finished_at FROM participant_finish WHERE competition_id=? AND user_id=?", comp_id, u["id"])
    return {"competition": public_comp(c, comp, u), "problems": problems, "disqualified": p["status"] == "disqualified", "finished_at": fin["finished_at"] if fin else None,
            "announcements": db.all_(c, "SELECT id, title, body, created_at FROM announcements WHERE competition_id=? ORDER BY id DESC LIMIT 20", comp_id),
            "server_time": db.now()}


@router.post("/api/student/competitions/{comp_id}/finish")
def finish(comp_id: int, req: Request, u=Depends(student), c=Depends(conn)):
    """Final submission: the student ends their test. Best scores so far are kept; no further submissions."""
    comp, p = arena_access(c, comp_id, u)
    if comp["status"] not in ("live", "paused"):
        raise HTTPException(409, "The competition is not running.")
    pend = db.one(c, "SELECT COUNT(*) AS n FROM submissions WHERE competition_id=? AND user_id=? AND status IN ('queued','running')", comp_id, u["id"])["n"]
    if pend:
        raise HTTPException(409, "Wait for your last submission to be judged, then finish.")
    c.execute("INSERT OR IGNORE INTO participant_finish(competition_id,user_id,finished_at) VALUES(?,?,?)", (comp_id, u["id"], db.now()))
    c.execute("INSERT INTO activity_events(competition_id,user_id,type,detail,ip,created_at) VALUES(?,?,'heartbeat',?,?,?)",
              (comp_id, u["id"], json.dumps({"final_submit": True}), client_ip(req), db.now()))
    lb = contest.compute_leaderboard(c, comp)
    me = next((r for r in lb["rows"] if r["user_id"] == u["id"]), None)
    if not contest.results_visible(comp):  # organisers publish results later: don't reveal score or rank yet
        return {"ok": True, "hidden": True, "solved": me["solved"] if me else 0, "of": len(lb["rows"])}
    return {"ok": True, "score": me["score"] if me else 0, "solved": me["solved"] if me else 0, "rank": me["rank"] if me else None,
            "of": len(lb["rows"]), "max": sum(q["marks"] for q in lb["questions"])}


@router.get("/api/student/practice")
def practice_list(u=Depends(student), c=Depends(conn)):
    rows = db.all_(c, "SELECT q.id, q.slug, q.category, q.difficulty, q.tags, qv.title, qv.qtype FROM questions q JOIN question_versions qv ON qv.id=q.latest_version_id "
                      "WHERE q.practice=1 AND q.status='approved' AND NOT EXISTS (SELECT 1 FROM competition_questions cq JOIN competitions co ON co.id=cq.competition_id "
                      "WHERE cq.question_id=q.id AND co.status IN ('draft','published','live','paused')) ORDER BY q.category, q.id")
    solved = {r["question_id"] for r in db.all_(c, "SELECT DISTINCT question_id FROM submissions WHERE user_id=? AND verdict='AC'", u["id"])}
    tried = {r["question_id"] for r in db.all_(c, "SELECT DISTINCT question_id FROM submissions WHERE user_id=? AND kind='practice'", u["id"])}
    for r in rows:
        r["tags"] = db.jl(r["tags"])
        r["solved"] = r["id"] in solved
        r["tried"] = r["id"] in tried
    return rows


# The order a student should work in. It follows what campus placement tests ask (TCS NQT, Infosys, Wipro, Accenture, Cognizant,
# Capgemini): language basics -> logic -> reading code (output / pseudo-code MCQs) -> CS fundamentals -> coding patterns -> timed mocks.
# Modules an organiser adds later are placed after these automatically.
JOURNEY = [
    ("C Basics", "Start here: input, output, conditions and loops. Every later step builds on this."),
    ("C Logic Building", "Number programs and patterns: the warm-up coding questions of most placement tests."),
    ("Placement: Predict the Output", "Read code and say what it prints. Pseudo-code and output questions are in almost every company's test."),
    ("Placement: CS Fundamentals", "Complexity, data structures, OOP, DBMS and OS basics: the technical multiple-choice section."),
    ("Placement: Logical Thinking", "Series, ratios, time and work, directions and deduction: the aptitude section that comes first in most tests. No code, only thinking."),
    ("Placement: Coding Patterns", "Arrays and strings problems of the size asked in the coding round. Aim to solve each within 20 minutes."),
]


@router.get("/api/student/journey")
def journey(u=Depends(student), c=Depends(conn)):
    rows = practice_list(u, c)
    cats = {}
    for r in rows:
        cats.setdefault(r["category"], []).append(r)
    why = dict(JOURNEY)
    names = [k for k, _ in JOURNEY if k in cats] + sorted(k for k in cats if k not in why)
    rank = {"Easy": 0, "Intermediate": 1, "Challenging": 2}
    stages = []
    for k in names:
        rs = cats[k]
        todo = sorted((r for r in rs if not r["solved"]), key=lambda r: (rank.get(r["difficulty"], 1), r["id"]))
        stages.append({"category": k, "why": why.get(k, "Extra practice added by your organisers."), "total": len(rs), "solved": len(rs) - len(todo),
                       "next_id": todo[0]["id"] if todo else None, "next_title": todo[0]["title"] if todo else None,
                       "done": len(rs) - len(todo) >= max(1, round(len(rs) * 0.8))})   # 80% is enough to move on
    cur = next((i for i, s in enumerate(stages) if not s["done"]), None)
    return {"stages": stages, "current": cur, "mock": "Finish with timed rounds on the Competitions page: one sitting, no going back, like the real test."}


def practice_question(c, qid):
    q = db.one(c, "SELECT q.*, qv.id AS vid FROM questions q JOIN question_versions qv ON qv.id=q.latest_version_id WHERE q.id=? AND q.practice=1 AND q.status='approved'", qid)
    if not q or db.one(c, "SELECT 1 AS x FROM competition_questions cq JOIN competitions co ON co.id=cq.competition_id WHERE cq.question_id=? AND co.status IN ('draft','published','live','paused')", qid):
        raise HTTPException(404, "Practice problem not found.")
    return q


@router.get("/api/student/practice/{qid}")
def practice_get(qid: int, u=Depends(student), c=Depends(conn)):
    q = practice_question(c, qid)
    v = db.one(c, "SELECT * FROM question_versions WHERE id=?", q["vid"])
    solved = bool(db.one(c, "SELECT 1 AS x FROM submissions WHERE user_id=? AND question_id=? AND verdict='AC' LIMIT 1", u["id"], qid))
    return problem_payload(c, v, {"id": qid, "label": "P", "difficulty": q["difficulty"], "category": q["category"], "solved": solved})


class SubmitIn(BaseModel):
    competition_id: int | None = None
    question_id: int
    language: str = Field(max_length=20)
    code: str = Field(max_length=65536)
    kind: str = Field(pattern="^(run|submit)$")
    custom_input: str | None = Field(default=None, max_length=65536)


@router.post("/api/student/submissions")
def submit(body: SubmitIn, req: Request, u=Depends(student), c=Depends(conn)):
    if not body.code.strip():
        raise HTTPException(400, "Your code is empty.")
    if body.language not in judge.LANGS:
        raise HTTPException(400, "Unsupported language.")
    rate_limit(f"run:{u['id']}", 20, 60)
    t = db.now()
    if body.competition_id is None:  # practice mode
        q = practice_question(c, body.question_id)
        v = db.one(c, "SELECT * FROM question_versions WHERE id=?", q["vid"])
        if body.language not in db.jl(v["languages"]):
            raise HTTPException(400, "That language is not allowed for this problem.")
        kind, comp_id, vid = ("run" if body.kind == "run" else "practice"), None, v["id"]
    else:
        comp, p = arena_access(c, body.competition_id, u)
        if p["status"] == "disqualified":
            raise HTTPException(403, "You have been disqualified from this competition.")
        if comp["status"] == "paused":
            raise HTTPException(409, "The competition is paused. Please wait for the organisers to resume it.")
        if comp["status"] != "live" or t >= contest.end_time(comp):
            raise HTTPException(409, "The competition is not accepting submissions right now.")
        if body.kind == "submit" and db.one(c, "SELECT 1 AS x FROM participant_finish WHERE competition_id=? AND user_id=?", comp["id"], u["id"]):
            raise HTTPException(409, "You have already finished this test. Your answers are final.")
        cq = db.one(c, "SELECT * FROM competition_questions WHERE competition_id=? AND question_id=?", comp["id"], body.question_id)
        if not cq:
            raise HTTPException(404, "Problem not found in this competition.")
        v = db.one(c, "SELECT * FROM question_versions WHERE id=?", cq["version_id"])
        allowed = [l for l in db.jl(v["languages"]) if l in ("answer", "prompt")] or [l for l in db.jl(v["languages"]) if l in db.jl(comp["languages"])] or db.jl(comp["languages"])
        if body.language not in allowed:
            raise HTTPException(400, "That language is not allowed in this competition.")
        if body.kind == "submit" and db.jl(v.get("options")) and db.one(
                c, "SELECT 1 AS x FROM submissions WHERE competition_id=? AND user_id=? AND question_id=? AND kind='submit' AND COALESCE(verdict,'')!='IE'",
                comp["id"], u["id"], body.question_id):   # four options: a second try would be a free guess
            raise HTTPException(409, "You have already answered this multiple-choice question. In a competition the first answer is final.")
        kind, comp_id, vid = body.kind, comp["id"], v["id"]
    if kind != "run":
        cool = int(db.setting(c, "submit_cooldown_sec", "5") or 5)
        last = db.one(c, "SELECT MAX(created_at) AS t FROM submissions WHERE user_id=? AND kind IN ('submit','practice')", u["id"])["t"]
        if last and t - last < cool:
            raise HTTPException(429, f"Please wait {cool - (t - last)}s before submitting again.")
    pending = db.one(c, "SELECT COUNT(*) AS n FROM submissions WHERE user_id=? AND status IN ('queued','running')", u["id"])["n"]
    if pending >= 3:
        raise HTTPException(429, "You already have submissions being judged. Wait for them to finish.")
    sid = c.execute("INSERT INTO submissions(competition_id,user_id,question_id,version_id,kind,language,code,custom_input,ip,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (comp_id, u["id"], body.question_id, vid, kind, body.language, body.code,
                     body.custom_input if body.kind == "run" and body.custom_input is not None and body.custom_input != "" else None, client_ip(req), t)).lastrowid
    if comp_id:
        c.execute("INSERT INTO activity_events(competition_id,user_id,question_id,type,detail,ip,created_at) VALUES(?,?,?,?,?,?,?)",
                  (comp_id, u["id"], body.question_id, kind, json.dumps({"submission": sid, "language": body.language, "chars": len(body.code)}), client_ip(req), t))
    return {"id": sid, "queue_ahead": db.one(c, "SELECT COUNT(*) AS n FROM submissions WHERE status='queued' AND id<?", sid)["n"]}


def student_submission(c, s, comp=None):
    show_counts = True
    if s["competition_id"]:
        comp = comp or db.one(c, "SELECT show_test_counts FROM competitions WHERE id=?", s["competition_id"])
        show_counts = bool(comp["show_test_counts"])
    out = {k: s[k] for k in ("id", "competition_id", "question_id", "kind", "language", "status", "verdict", "score", "time_ms", "memory_kb", "created_at", "judged_at")}
    out["verdict_name"] = judge.VERDICT_NAMES.get(s["verdict"] or "PENDING", s["verdict"])
    out["compile_output"] = s["compile_output"] if s["verdict"] in ("CE", "IE") else None
    if s["kind"] == "run":  # samples / custom input: visible to the student by definition
        res = db.all_(c, "SELECT ordinal, visibility, verdict, time_ms, memory_kb, output, stderr FROM judging_results WHERE submission_id=? ORDER BY ordinal", s["id"])
        sam = {x["ordinal"]: x for x in samples(c, s["version_id"])}
        for r in res:
            if r["visibility"] == "sample" and r["ordinal"] in sam:
                r["input"], r["expected"] = sam[r["ordinal"]]["input"], sam[r["ordinal"]]["output"]
            elif r["visibility"] == "custom":
                r["input"] = s["custom_input"]
        out["results"] = res
    else:
        res = db.all_(c, "SELECT visibility, verdict, time_ms FROM judging_results WHERE submission_id=? ORDER BY ordinal", s["id"])
        if show_counts:
            out["passed"], out["total"] = s["passed"], s["total"]
            out["tests"] = [{"n": i + 1, "verdict": r["verdict"], "sample": r["visibility"] == "sample", "time_ms": r["time_ms"]} for i, r in enumerate(res)]
        # first failing SAMPLE case is safe to show; hidden cases never are
        bad = db.one(c, "SELECT ordinal, output, stderr FROM judging_results WHERE submission_id=? AND visibility='sample' AND verdict NOT IN ('AC','OK') ORDER BY ordinal LIMIT 1", s["id"])
        if bad:
            sm = next((x for x in samples(c, s["version_id"]) if x["ordinal"] == bad["ordinal"]), None)
            if sm:
                out["failed_sample"] = {"input": sm["input"], "expected": sm["output"], "output": bad["output"], "stderr": bad["stderr"]}
    return out


@router.get("/api/student/submissions/{sid}")
def get_submission(sid: int, u=Depends(student), c=Depends(conn)):
    s = db.one(c, "SELECT * FROM submissions WHERE id=? AND user_id=?", sid, u["id"])
    if not s:
        raise HTTPException(404, "Submission not found.")
    out = student_submission(c, s)
    out["code"] = s["code"]
    if s["status"] == "queued":
        out["queue_ahead"] = db.one(c, "SELECT COUNT(*) AS n FROM submissions WHERE status='queued' AND id<?", sid)["n"]
    return out


@router.get("/api/student/submissions")
def my_submissions(competition_id: int | None = None, question_id: int | None = None, u=Depends(student), c=Depends(conn)):
    sql = ("SELECT s.id, s.competition_id, s.question_id, s.kind, s.language, s.status, s.verdict, s.score, s.passed, s.total, s.time_ms, s.memory_kb, s.created_at, "
           "qv.title, co.show_test_counts FROM submissions s JOIN question_versions qv ON qv.id=s.version_id LEFT JOIN competitions co ON co.id=s.competition_id "
           "WHERE s.user_id=? AND s.kind IN ('submit','practice')")
    a = [u["id"]]
    if competition_id is not None:
        sql += " AND s.competition_id=?"
        a.append(competition_id)
    if question_id is not None:
        sql += " AND s.question_id=?"
        a.append(question_id)
    rows = db.all_(c, sql + " ORDER BY s.id DESC LIMIT 100", *a)
    for r in rows:
        r["verdict_name"] = judge.VERDICT_NAMES.get(r["verdict"] or "PENDING", r["verdict"])
        if r["show_test_counts"] == 0:
            r["passed"] = r["total"] = None
    return rows


ACTIVITY_TYPES = {"paste", "paste_blocked", "copy", "cut", "tab_hidden", "tab_visible", "blur", "focus", "fullscreen_exit", "snapshot",
                  "lang_change", "reset_code", "keystrokes", "right_click", "devtools", "problem_open", "heartbeat", "internet", "ai_tool", "violation"}


class ActivityIn(BaseModel):
    competition_id: int
    events: list[dict] = Field(max_length=200)


@router.post("/api/student/activity")
def activity(body: ActivityIn, req: Request, u=Depends(student), c=Depends(conn)):
    rate_limit(f"act:{u['id']}", 120, 60)
    if not db.one(c, "SELECT 1 AS x FROM competition_participants WHERE competition_id=? AND user_id=?", body.competition_id, u["id"]):
        raise HTTPException(403, "Not a participant.")
    t, ip = db.now(), client_ip(req)
    rows = []
    for e in body.events:
        typ = str(e.get("type", ""))
        if typ not in ACTIVITY_TYPES:
            continue
        detail = e.get("detail")
        detail = json.dumps(detail)[: (65536 if typ == "snapshot" else 2000)] if detail is not None else None
        qid = e.get("question_id") if isinstance(e.get("question_id"), int) else None
        at = e.get("at") if isinstance(e.get("at"), int) and abs(e["at"] - t) < 600 else t
        rows.append((body.competition_id, u["id"], qid, typ, detail, ip, at))
    if rows:
        c.executemany("INSERT INTO activity_events(competition_id,user_id,question_id,type,detail,ip,created_at) VALUES(?,?,?,?,?,?,?)", rows)
    if any(r[3] == "violation" for r in rows):
        return {"ok": True, "stored": len(rows), **enforce_warnings(c, body.competition_id, u["id"], ip)}
    return {"ok": True, "stored": len(rows)}


def enforce_warnings(c, comp_id, uid, ip):
    """Server-side count of fair-play warnings (left the test, left full screen) since the last reopen; auto-submits at the limit."""
    comp = db.one(c, "SELECT status, lockdown FROM competitions WHERE id=?", comp_id)
    limit = contest.lockdown_cfg(comp["lockdown"])["max_violations"] if comp else 0
    since = db.one(c, "SELECT MAX(created_at) AS t FROM activity_events WHERE competition_id=? AND user_id=? AND type='reopened'", comp_id, uid)["t"] or 0
    n = db.one(c, "SELECT COUNT(*) AS n FROM activity_events WHERE competition_id=? AND user_id=? AND type='violation' AND created_at>=?", comp_id, uid, since)["n"]
    if not limit or n < limit or comp["status"] not in ("live", "paused"):
        return {"warnings": n, "limit": limit}
    if c.execute("INSERT OR IGNORE INTO participant_finish(competition_id,user_id,finished_at) VALUES(?,?,?)", (comp_id, uid, db.now())).rowcount:
        c.execute("INSERT INTO activity_events(competition_id,user_id,type,detail,ip,created_at) VALUES(?,?,'auto_finished',?,?,?)",
                  (comp_id, uid, json.dumps({"warnings": n, "limit": limit}), ip, db.now()))
    return {"warnings": n, "limit": limit, "finished": True}


@router.get("/api/student/profile")
def profile(u=Depends(student), c=Depends(conn)):
    hist = []
    for comp in db.all_(c, "SELECT co.* FROM competitions co JOIN competition_participants cp ON cp.competition_id=co.id WHERE cp.user_id=? ORDER BY co.start_at DESC", u["id"]):
        comp = contest.tick(c, comp)
        row = None
        if contest.results_visible(comp):
            lb = contest.leaderboard(c, comp)
            row = next((r for r in lb["rows"] if r["user_id"] == u["id"]), None)
            total = len(lb["rows"])
        hist.append({"id": comp["id"], "code": comp["code"], "title": comp["title"], "round_no": comp["round_no"], "start_at": comp["start_at"],
                     "display_status": contest.display_status(comp), "rank": row["rank"] if row else None, "of": total if row else None,
                     "score": row["score"] if row else None, "solved": row["solved"] if row else None,
                     "results_pending": not contest.results_visible(comp) and comp["status"] in ("ended", "finalized"),
                     "finished": bool(db.one(c, "SELECT 1 AS x FROM participant_finish WHERE competition_id=? AND user_id=?", comp["id"], u["id"]))})
    return {"me": me_payload(c, u), "history": hist,
            "practice_solved": db.one(c, "SELECT COUNT(DISTINCT question_id) AS n FROM submissions WHERE user_id=? AND kind='practice' AND verdict='AC'", u["id"])["n"]}


# ---------------- gamification: badges, streaks, stats ----------------

BADGES = [  # (key, name, description, icon)
    ("first_ac", "First Blood", "Get your first Accepted verdict", "spark"),
    ("solver10", "Problem Solver", "Solve 10 different problems", "check"),
    ("solver25", "Code Machine", "Solve 25 different problems", "cpu"),
    ("solver50", "Half Century", "Solve 50 different problems", "star"),
    ("debut", "Arena Debut", "Submit in your first competition", "flag"),
    ("veteran", "Veteran", "Compete in 5 competitions", "shield"),
    ("podium", "Podium", "Finish in the top 3 of a completed round", "medal"),
    ("champion", "Champion", "Win a completed round", "crown"),
    ("perfect", "Perfect Round", "Score full marks in a round", "target"),
    ("polyglot", "Polyglot", "Get Accepted in 3 different languages", "globe"),
    ("solver100", "Centurion", "Solve 100 different problems", "crown"),
    ("streak3", "Warming Up", "Code on 3 days in a row", "flame"),
    ("streak7", "On Fire", "Code on 7 days in a row", "flame"),
    ("streak30", "Unstoppable", "Code on 30 days in a row", "flame"),
    ("thinker", "Sharp Reader", "Answer 10 multiple-choice or output questions correctly", "target"),
    ("speed", "Speedster", "Solve a contest problem within 10 minutes of the start", "bolt"),
]


def day_streaks(days: set):
    """days: set of 'YYYY-MM-DD' strings (local tz). Returns (current, longest)."""
    import datetime
    if not days:
        return 0, 0
    ds = sorted(datetime.date.fromisoformat(d) for d in days)
    longest = run = 1
    for a, b in zip(ds, ds[1:]):
        run = run + 1 if (b - a).days == 1 else 1
        longest = max(longest, run)
    today = datetime.datetime.now(__import__("zoneinfo").ZoneInfo(config.APP_TIMEZONE)).date()
    cur, d = 0, today if today in set(ds) else today - datetime.timedelta(days=1)
    s = set(ds)
    while d in s:
        cur += 1
        d -= datetime.timedelta(days=1)
    return cur, longest


def student_stats(c, uid):
    import datetime
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(config.APP_TIMEZONE)
    subs = db.all_(c, "SELECT s.created_at, s.verdict, s.language, s.question_id, s.competition_id, q.difficulty FROM submissions s "
                      "JOIN questions q ON q.id=s.question_id WHERE s.user_id=? AND s.kind IN ('submit','practice')", uid)
    heat = {}
    for s in subs:
        d = datetime.datetime.fromtimestamp(s["created_at"], tz).date().isoformat()
        heat[d] = heat.get(d, 0) + 1
    cur, longest = day_streaks(set(heat))
    solved = {}
    for s in subs:
        if s["verdict"] == "AC":
            solved[s["question_id"]] = s["difficulty"]
    by_diff = {k: sum(1 for v in solved.values() if v == k) for k in ("Easy", "Intermediate", "Challenging")}
    totals = {r["difficulty"]: r["n"] for r in db.all_(c, "SELECT difficulty, COUNT(*) AS n FROM questions WHERE status='approved' GROUP BY difficulty")}
    ac_langs = {s["language"] for s in subs if s["verdict"] == "AC"}
    # competition results
    rounds, podium, champion, perfect, speed = 0, False, False, False, False
    for comp in db.all_(c, "SELECT co.* FROM competitions co JOIN competition_participants cp ON cp.competition_id=co.id WHERE cp.user_id=? AND co.status NOT IN ('draft','cancelled')", uid):
        if not any(s["competition_id"] == comp["id"] for s in subs):
            continue
        rounds += 1
        if comp["status"] in ("ended", "finalized", "archived"):
            lb = contest.leaderboard(c, comp)
            me = next((r for r in lb["rows"] if r["user_id"] == uid), None)
            if me and me["score"] > 0:
                podium |= me["rank"] <= 3
                champion |= me["rank"] == 1
                full = sum(q["marks"] for q in lb["questions"])
                perfect |= full > 0 and me["score"] >= full
        st = comp["started_at"] or comp["start_at"] or 0
        speed |= any(s["competition_id"] == comp["id"] and s["verdict"] == "AC" and s["created_at"] - st <= 600 for s in subs)
    n = len(solved)
    earned = {"first_ac": n >= 1, "solver10": n >= 10, "solver25": n >= 25, "solver50": n >= 50, "debut": rounds >= 1, "veteran": rounds >= 5,
              "podium": podium, "champion": champion, "perfect": perfect, "polyglot": len(ac_langs - {"answer", "prompt"}) >= 3, "streak7": longest >= 7, "speed": speed,
              "solver100": n >= 100, "streak3": longest >= 3, "streak30": longest >= 30,
              "thinker": len({s["question_id"] for s in subs if s["verdict"] == "AC" and s["language"] == "answer"}) >= 10}
    # one badge per practice module, created by itself for every module that exists (also ones added later)
    mods = {}
    for r in db.all_(c, "SELECT id, category FROM questions WHERE practice=1 AND status='approved'"):
        mods.setdefault(r["category"], []).append(r["id"])
    module_badges = [{"key": "module:" + k, "name": k.replace("Placement: ", "") + " Master", "description": f"Solve every problem in {k}", "icon": "award",
                      "earned": all(i in solved for i in ids)} for k, ids in sorted(mods.items()) if len(ids) >= 5]
    return {"heatmap": heat, "streak": cur, "longest_streak": longest, "solved": n, "solved_by_difficulty": by_diff,
            "totals_by_difficulty": {k: totals.get(k, 0) for k in ("Easy", "Intermediate", "Challenging")},
            "languages": sorted(ac_langs), "submissions": len(subs), "accepted": sum(1 for s in subs if s["verdict"] == "AC"), "rounds": rounds,
            "badges": [{"key": k, "name": nm, "description": d, "icon": ic, "earned": earned[k]} for k, nm, d, ic in BADGES] + module_badges}


@router.get("/api/student/stats")
def my_stats(u=Depends(student), c=Depends(conn)):
    st = student_stats(c, u["id"])
    sem = active_semester(c)
    st["semester"] = None
    if sem:
        lb = semester_board(c, sem)
        mine = next((r for r in lb["rows"] if r["user_id"] == u["id"]), None)
        n = len(lb["rows"])
        st["semester"] = {"name": sem["name"], "points": mine["points"] if mine else 0, "rank": mine["rank"] if mine else None, "of": n,
                          "percentile": round((1 - (mine["rank"] - 1) / n) * 100, 1) if mine and n else None}
    return st


@router.get("/api/public/landing")
def landing_data(c=Depends(conn)):
    """Everything the one-screen landing page shows, from the live database."""
    mode = db.setting(c, "name_display", "full")
    comps = [x for x in visible_comps(c) if x["status"] != "archived"]
    live = [x for x in comps if x["status"] in ("live", "paused")]
    upcoming = sorted([x for x in comps if x["status"] == "published" and x["start_at"]], key=lambda x: x["start_at"])
    feature = (live or upcoming or [None])[0]
    board, board_title = [], ""
    sem = active_semester(c)
    if sem:
        lb = semester_board(c, sem)
        board = [{"rank": r["rank"], "name": contest.display_name(r["name"], r["participant_id"], mode), "points": r["points"],
                  "department": r["department"], "solved": r["solved"]} for r in lb["rows"][:5]]
        board_title = sem["name"]
    if not board:
        last = next((x for x in db.all_(c, "SELECT * FROM competitions WHERE status IN ('ended','finalized') ORDER BY ended_at DESC LIMIT 8") if contest.results_visible(x)), None)
        if last:
            lb = contest.leaderboard(c, last)
            board = [{"rank": r["rank"], "name": contest.display_name(r["name"], r["participant_id"], mode), "points": r["score"],
                      "department": r["department"], "solved": r["solved"]} for r in lb["rows"][:5]]
            board_title = last["title"]
    return {"stats": {"students": db.one(c, "SELECT COUNT(*) AS n FROM students")["n"],
                      "problems": db.one(c, "SELECT COUNT(*) AS n FROM questions WHERE status='approved'")["n"],
                      "rounds": db.one(c, "SELECT COUNT(*) AS n FROM competitions WHERE status IN ('ended','finalized','archived')")["n"],
                      "submissions": db.one(c, "SELECT COUNT(*) AS n FROM submissions WHERE kind IN ('submit','practice')")["n"]},
            "board": board, "board_title": board_title,
            "feature": {"id": feature["id"], "code": feature["code"], "title": feature["title"], "display_status": contest.display_status(feature),
                        "start_at": feature["start_at"], "remaining_sec": contest.remaining(feature) if feature["status"] in ("live", "paused") else None,
                        "duration_min": feature["duration_min"], "languages": db.jl(feature["languages"]),
                        "elapsed_sec": max(0, db.now() - (feature["started_at"] or feature["start_at"] or db.now())) if feature["status"] in ("live", "paused") else None,
                        "questions": db.one(c, "SELECT COUNT(*) AS n FROM competition_questions WHERE competition_id=?", feature["id"])["n"],
                        "participants": db.one(c, "SELECT COUNT(*) AS n FROM competition_participants WHERE competition_id=?", feature["id"])["n"],
                        "reg_end": feature["reg_end"]} if feature else None,
            "more": max(0, len(live) + len(upcoming) - 1)}


@router.get("/api/student/history")
def history(u=Depends(student), c=Depends(conn)):
    """Every problem the student has attempted, with where, attempts, best result and the latest code."""
    rows = db.all_(c, "SELECT s.id, s.question_id, s.competition_id, s.language, s.verdict, s.score, s.created_at, s.kind, qv.title, q.difficulty, q.category, co.code AS comp_code, co.title AS comp_title "
                      "FROM submissions s JOIN question_versions qv ON qv.id=s.version_id JOIN questions q ON q.id=s.question_id LEFT JOIN competitions co ON co.id=s.competition_id "
                      "WHERE s.user_id=? AND s.kind IN ('submit','practice') AND s.status='done' ORDER BY s.id", u["id"])
    agg = {}
    for r in rows:
        k = (r["question_id"], r["competition_id"])
        a = agg.setdefault(k, {"question_id": r["question_id"], "competition_id": r["competition_id"], "title": r["title"], "difficulty": r["difficulty"],
                               "category": r["category"], "where": r["comp_title"] or "Practice", "where_code": r["comp_code"], "attempts": 0, "solved": False,
                               "solved_at": None, "best_score": 0, "last_at": 0, "last_submission": None, "languages": []})
        a["attempts"] += 1
        a["last_at"] = r["created_at"]
        a["last_submission"] = r["id"]
        a["best_score"] = max(a["best_score"], r["score"] or 0)
        if r["language"] not in a["languages"]:
            a["languages"].append(r["language"])
        if r["verdict"] == "AC" and not a["solved"]:
            a["solved"], a["solved_at"] = True, r["created_at"]
    items = sorted(agg.values(), key=lambda x: -x["last_at"])
    results = []
    for comp in [x for x in db.all_(c, "SELECT co.* FROM competitions co JOIN competition_participants cp ON cp.competition_id=co.id WHERE cp.user_id=? "
                                    "AND co.status IN ('ended','finalized','archived') ORDER BY COALESCE(co.results_published_at, co.finalized_at, co.ended_at) DESC LIMIT 10", u["id"])
                 if contest.results_visible(x) and (x["results_published_at"] or x["status"] in ("finalized", "archived"))][:5]:
        lb = contest.leaderboard(c, comp)
        me = next((r for r in lb["rows"] if r["user_id"] == u["id"]), None)
        if me:
            results.append({"id": comp["id"], "title": comp["title"], "rank": me["rank"], "of": len(lb["rows"]), "score": me["score"],
                            "max": sum(q["marks"] for q in lb["questions"]), "finalized_at": comp["results_published_at"] or comp["finalized_at"]})
    return {"problems": items, "results": results}


# ---------------- profile pictures ----------------
AVATAR_DIR = config.DATA_DIR / "avatars"
AVATAR_TYPES = {b"\x89PNG": ("png", "image/png"), b"\xff\xd8": ("jpg", "image/jpeg"), b"RIFF": ("webp", "image/webp")}


def _avatar_kind(data: bytes):
    for sig, kind in AVATAR_TYPES.items():
        if data.startswith(sig) and (sig != b"RIFF" or data[8:12] == b"WEBP"):
            return kind
    return None


@router.post("/api/me/avatar")
async def upload_avatar(req: Request, file: UploadFile = File(...), u=Depends(student), c=Depends(conn)):
    rate_limit(f"avatar:{u['id']}", 10, 600)
    data = await file.read(700 * 1024 + 1)
    if len(data) > 700 * 1024:
        raise HTTPException(400, "Choose an image smaller than 700 KB.")
    kind = _avatar_kind(data)
    if not kind:
        raise HTTPException(400, "Use a PNG, JPG or WEBP image.")
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    for old in AVATAR_DIR.glob(f"{u['id']}.*"):
        old.unlink(missing_ok=True)
    (AVATAR_DIR / f"{u['id']}.{kind[0]}").write_bytes(data)
    c.execute("UPDATE users SET avatar=? WHERE id=?", (f"{kind[0]}:{db.now()}", u["id"]))
    return {"avatar": contest.avatar_url(u["id"], f"{kind[0]}:{db.now()}")}


@router.delete("/api/me/avatar")
def delete_avatar(u=Depends(student), c=Depends(conn)):
    for old in AVATAR_DIR.glob(f"{u['id']}.*"):
        old.unlink(missing_ok=True)
    c.execute("UPDATE users SET avatar=NULL WHERE id=?", (u["id"],))
    return {"ok": True}


@router.get("/api/avatar/{uid}")
def get_avatar(uid: int, c=Depends(conn)):
    r = db.one(c, "SELECT avatar FROM users WHERE id=?", uid)
    if not r or not r["avatar"]:
        raise HTTPException(404, "No picture.")
    ext = r["avatar"].split(":")[0]
    f = AVATAR_DIR / f"{uid}.{ext}"
    if not f.is_file():
        raise HTTPException(404, "No picture.")
    media = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}[ext]
    return FileResponse(f, media_type=media, headers={"Cache-Control": "public, max-age=86400", "Content-Security-Policy": "default-src 'none'"})
