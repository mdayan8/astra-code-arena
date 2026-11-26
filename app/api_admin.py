"""Admin Control Centre API. Every route re-checks the admin session + role on the server."""
import csv
import hashlib
import io
import json
import re
import secrets
import shutil
import threading
from datetime import datetime
from pathlib import Path
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo

from starlette.concurrency import run_in_threadpool
from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import ai, config, contest, db, judge, pdfimport
from .api_student import check_dob, conn, departments, dob_password, new_participant_id
from .security import (ADMIN_COOKIE, MODULES, attempt_login, audit, can_manage_comp, check_password_strength, client_ip, current_user,
                       has, hash_password, login, logout, need, need_comp, perms, temp_password, verify_password)

router = APIRouter(prefix="/api/admin")
TZ = ZoneInfo(config.APP_TIMEZONE)
CATEGORIES = ["Arrays", "Strings", "Searching", "Sorting", "Frequency Counting", "Basic Mathematics", "Logical Reasoning", "Hash Maps",
              "Stack", "Queue", "Sliding Window", "Basic Recursion", "Pattern Recognition", "Problem Solving"]


def admin(req: Request, c=Depends(conn)):
    return current_user(c, req, ADMIN_COOKIE, "admin")


def ts(v):
    """Accept epoch int or 'YYYY-MM-DDTHH:MM' in the configured timezone."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    try:
        return int(datetime.fromisoformat(str(v)).replace(tzinfo=TZ).timestamp())
    except ValueError:
        raise HTTPException(400, f"Invalid date/time: {v}")


def page(limit, offset):
    return max(1, min(int(limit or 50), 500)), max(0, int(offset or 0))


# ================= auth =================

class LoginIn(BaseModel):
    username: str = Field(max_length=120)
    password: str = Field(max_length=200)


def admin_payload(u, csrf=None):
    p = perms(u)
    mods = [m for m in MODULES if "*" in p or m in p or (m == "semesters" and "semesters.view" in p)]
    out = {"id": u["id"], "username": u["username"], "name": u["name"], "email": u["email"], "role": u["role"],
           "can_approve": bool(u["can_approve"]) or "questions.approve" in p or "*" in p, "modules": mods,
           "must_change_pw": bool(u["must_change_pw"]), "timezone": config.APP_TIMEZONE}
    if csrf:
        out["csrf"] = csrf
    return out


@router.post("/login")
def admin_login(body: LoginIn, req: Request, resp: Response, c=Depends(conn)):
    u = db.one(c, "SELECT * FROM users WHERE (username=? OR email=?) AND kind='admin'", body.username.strip(), body.username.strip())
    u = attempt_login(c, u["username"] if u else body.username, body.password, "admin", req)
    csrf = login(c, u, req, resp, ADMIN_COOKIE)
    audit(c, u, "admin.login", ip=client_ip(req))
    return admin_payload(u, csrf)


@router.post("/logout")
def admin_logout(req: Request, resp: Response, c=Depends(conn)):
    logout(c, req, resp, ADMIN_COOKIE)
    return {"ok": True}


@router.get("/me")
def admin_me(u=Depends(admin)):
    return admin_payload(u, u["csrf"])


class PwIn(BaseModel):
    current: str = Field(max_length=200)
    new: str = Field(max_length=200)


@router.post("/me/password")
def admin_pw(body: PwIn, req: Request, u=Depends(admin), c=Depends(conn)):
    full = db.one(c, "SELECT password_hash FROM users WHERE id=?", u["id"])
    if not verify_password(body.current, full["password_hash"]):
        raise HTTPException(400, "Current password is incorrect.")
    check_password_strength(body.new)
    c.execute("UPDATE users SET password_hash=?, must_change_pw=0 WHERE id=?", (hash_password(body.new), u["id"]))
    audit(c, u, "admin.password_change", "user", u["id"], ip=client_ip(req))
    return {"ok": True}


# ================= dashboard / system =================

def system_status(c):
    workers = []
    for r in db.all_(c, "SELECT key, value FROM platform_settings WHERE key LIKE 'worker:%'"):
        hb = db.jl(r["value"], {})
        age = db.now() - hb.get("at", 0)
        if age < 120:  # only workers that reported recently; restarted workers get new names
            workers.append({"name": r["key"][7:], "state": hb.get("state"), "age_sec": age, "alive": age < 30, "sandbox": hb.get("sandbox")})
    du = shutil.disk_usage(config.DATA_DIR)
    q = db.one(c, "SELECT SUM(status='queued') AS queued, SUM(status='running') AS running, SUM(status='error') AS errors FROM submissions")
    last_b = int(db.setting(c, "backup:last_at", "0") or 0)
    host = db.jl(db.setting(c, "host:stats", ""), {}) or {}
    alerts = []
    if not any(w["alive"] for w in workers):
        alerts.append(("err", "The judge is not running: submissions are queued but not graded. Restart the platform from the launcher."))
    if du.free < 2e9:
        alerts.append(("err", f"Disk almost full ({du.free / 1e9:.1f} GB free). Delete old backups or free space."))
    if host.get("ram_pct") and host["ram_pct"] > 90:
        alerts.append(("warn", f"Memory is {host['ram_pct']}% used. Close other programs on the server or lower the number of judge workers."))
    if host.get("cpu") and host["cpu"] > 90:
        alerts.append(("warn", f"CPU is {host['cpu']}% busy. Grading may be slower."))
    if (q["queued"] or 0) > 50:
        alerts.append(("warn", f"{q['queued']} submissions waiting to be judged."))
    if not last_b or db.now() - last_b > 26 * 3600:
        alerts.append(("warn", "No database backup in the last day. Use Database → Back up now."))
    if int(db.setting(c, "backup:failed_at", "0") or 0) > (last_b or 0):
        alerts.append(("err", "The last automatic backup failed its check. See Recent errors."))
    if int(db.setting(c, "backup:mirror_failed_at", "0") or 0) > db.now() - 86400:
        alerts.append(("warn", "Copying backups to the extra backup folder failed (is the drive connected?)."))
    restored = db.jl(db.setting(c, "db:restored", ""), {}) or {}
    if restored and db.now() - restored.get("at", 0) < 7 * 86400:
        alerts.append(("warn", f"The database was damaged at start-up and was automatically restored from backup {restored.get('from')}. The damaged file was kept as {restored.get('damaged')}."))
    integ = db.jl(db.setting(c, "db:integrity", ""), {}) or {}
    if integ and not integ.get("ok"):
        alerts.append(("err", "Database integrity check failed. Restore the newest backup from the Database page."))
    return {"backend": "ok", "database": "ok", "db_size_mb": round(config.DB_PATH.stat().st_size / 1e6, 2), "host": host,
            "alerts": [{"level": l, "text": t} for l, t in alerts],
            "workers": workers, "workers_alive": sum(w["alive"] for w in workers),
            "queue": {"queued": q["queued"] or 0, "running": q["running"] or 0, "judge_errors": q["errors"] or 0},
            "disk": {"free_gb": round(du.free / 1e9, 1), "total_gb": round(du.total / 1e9, 1), "used_pct": round(du.used / du.total * 100)},
            "backup": {"last_at": last_b or None, "file": db.setting(c, "backup:last_file", "") or None, "dir": str(config.BACKUP_DIR)},
            "languages": {k: {"name": judge.LANGS[k]["name"], "available": v} for k, v in judge.available_languages().items()},
            "ai": ai.status() if ai.enabled() else {"enabled": False},
            "recent_errors": db.all_(c, "SELECT source, message, created_at FROM error_log ORDER BY id DESC LIMIT 15"),
            "server_time": db.now(), "timezone": config.APP_TIMEZONE}


@router.get("/dashboard")
def dashboard(u=Depends(admin), c=Depends(conn)):
    comps = [contest.tick(c, x) for x in db.all_(c, "SELECT * FROM competitions")]
    ds = [contest.display_status(x) for x in comps]
    sysst = system_status(c)
    recent = db.all_(c, "SELECT a.action, a.entity, a.entity_id, a.created_at, u.name FROM audit_logs a LEFT JOIN users u ON u.id=a.user_id "
                        + ("" if u["role"] == "super_admin" else "WHERE a.user_id=? ") + "ORDER BY a.id DESC LIMIT 12", *([] if u["role"] == "super_admin" else [u["id"]]))
    results = []
    for comp in sorted([x for x in comps if x["status"] in ("ended", "finalized")], key=lambda x: -(x["ended_at"] or 0))[:3]:
        lb = contest.leaderboard(c, comp)
        results.append({"id": comp["id"], "title": comp["title"], "code": comp["code"], "participants": len(lb["rows"]),
                        "top": [{"name": r["name"], "score": r["score"]} for r in lb["rows"][:3]]})
    return {"counts": {"competitions": len(comps), "active": sum(d in ("Live", "Paused") for d in ds),
                       "upcoming": sum(d in ("Upcoming", "Registration Open", "Registration Closed") for d in ds),
                       "completed": sum(d == "Completed" for d in ds), "drafts": sum(d == "Draft" for d in ds),
                       "students": db.one(c, "SELECT COUNT(*) AS n FROM students")["n"],
                       "submissions": db.one(c, "SELECT COUNT(*) AS n FROM submissions WHERE kind IN ('submit','practice')")["n"],
                       "questions": db.one(c, "SELECT COUNT(*) AS n FROM questions WHERE status!='archived'")["n"]},
            "live": [{"id": x["id"], "title": x["title"], "code": x["code"], "status": contest.display_status(x), "remaining_sec": contest.remaining(x),
                      "total_sec": (x["duration_min"] + x["extra_min"]) * 60,
                      "participants": db.one(c, "SELECT COUNT(*) AS n FROM competition_participants WHERE competition_id=?", x["id"])["n"],
                      "online": db.one(c, "SELECT COUNT(DISTINCT user_id) AS n FROM activity_events WHERE competition_id=? AND created_at>?", x["id"], db.now() - 60)["n"],
                      "submissions": db.one(c, "SELECT COUNT(*) AS n FROM submissions WHERE competition_id=? AND kind='submit'", x["id"])["n"],
                      "finished": db.one(c, "SELECT COUNT(*) AS n FROM participant_finish WHERE competition_id=?", x["id"])["n"]}
                     for x in comps if x["status"] in ("live", "paused")],
            "upcoming_list": [{"id": x["id"], "title": x["title"], "code": x["code"], "status": contest.display_status(x), "start_at": x["start_at"]}
                              for x in sorted(comps, key=lambda y: y["start_at"] or 0) if x["status"] == "published"][:4],
            "recent_activity": recent, "results": results,
            "system": {k: sysst[k] for k in ("workers_alive", "queue", "disk", "backup", "alerts", "host")}}


@router.get("/system")
def system(u=Depends(admin), c=Depends(conn)):
    need(u, "settings")
    return system_status(c)


@router.post("/system/backup")
def do_backup(req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "settings")
    from .worker import backup
    name = backup(c, "manual")
    audit(c, u, "system.backup", detail={"file": name}, ip=client_ip(req))
    return {"file": name}


DB_TABLES = ["users", "students", "competitions", "competition_participants", "questions", "question_versions", "test_cases", "submissions",
             "judging_results", "activity_events", "audit_logs", "uploaded_documents", "generated_question_drafts", "ai_tools", "ai_tool_uses", "sessions"]


@router.get("/system/database")
def database_info(u=Depends(admin), c=Depends(conn)):
    need(u, "settings")
    wal = config.DB_PATH.with_name(config.DB_PATH.name + "-wal")
    backups = sorted(config.BACKUP_DIR.glob("astra-*.db"), key=lambda f: f.stat().st_mtime, reverse=True)
    return {"size_mb": round(config.DB_PATH.stat().st_size / 1e6, 2), "wal_mb": round(wal.stat().st_size / 1e6, 2) if wal.exists() else 0,
            "path": str(config.DB_PATH), "backup_keep": config.BACKUP_KEEP, "journal": c.execute("PRAGMA journal_mode").fetchone()[0], "schema_version": c.execute("PRAGMA user_version").fetchone()[0],
            "tables": [{"name": t, "rows": c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]} for t in DB_TABLES],
            "integrity": db.jl(db.setting(c, "db:integrity", ""), {}) or None,
            "backups": [{"name": f.name, "size_mb": round(f.stat().st_size / 1e6, 2), "at": int(f.stat().st_mtime)} for f in backups]}


@router.post("/system/database/check")
def database_check(req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "settings")
    from .worker import integrity_check
    ok = integrity_check(c)
    c.execute("PRAGMA wal_checkpoint(PASSIVE)")
    audit(c, u, "system.integrity_check", detail={"ok": ok}, ip=client_ip(req))
    return db.jl(db.setting(c, "db:integrity", ""), {})


@router.get("/system/backups/{name}")
def download_backup(name: str, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "settings")
    if not re.fullmatch(r"astra-[\w-]+\.db", name) or not (config.BACKUP_DIR / name).is_file():
        raise HTTPException(404, "Backup not found.")
    audit(c, u, "system.backup_download", detail={"file": name}, ip=client_ip(req))
    return FileResponse(config.BACKUP_DIR / name, media_type="application/octet-stream", filename=name)


class SettingsIn(BaseModel):
    registration_open: bool | None = None
    name_display: str | None = Field(default=None, pattern="^(full|initials|participant_id)$")
    highlights_enabled: bool | None = None
    site_banner: str | None = Field(default=None, max_length=300)
    submit_cooldown_sec: int | None = Field(default=None, ge=0, le=300)
    departments: list[str] | None = None
    schedule: dict | None = None


@router.get("/settings")
def get_settings(u=Depends(admin), c=Depends(conn)):
    need(u, "settings")
    s = {r["key"]: r["value"] for r in db.all_(c, "SELECT key, value FROM platform_settings") if ":" not in r["key"] and r["key"] != "admin_path"}
    s["departments"] = departments(c)
    s["schedule"] = db.jl(db.setting(c, "schedule", ""), {}) or {"enabled": False, "windows": [], "message": ""}
    return {"settings": s, "env": {"APP_ENV": config.APP_ENV, "APP_PORT": config.APP_PORT, "APP_TIMEZONE": config.APP_TIMEZONE,
                                   "COOKIE_SECURE": config.COOKIE_SECURE, "JUDGE_WORKERS": config.JUDGE_WORKERS,
                                   "AI": f"{ai.model()} @ {ai.base()}" if ai.enabled() else "not installed (optional)"}}


@router.put("/settings")
def put_settings(body: SettingsIn, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "settings")
    d = {k: (str(v).lower() if isinstance(v, bool) else v) for k, v in body.model_dump().items() if v is not None}
    if "schedule" in d:
        sc = d["schedule"]
        wins = [{"days": sorted({int(x) for x in w.get("days", []) if 0 <= int(x) <= 6}), "start": str(w["start"])[:5], "end": str(w["end"])[:5]}
                for w in sc.get("windows", []) if re.fullmatch(r"\d\d:\d\d", str(w.get("start", ""))[:5]) and re.fullmatch(r"\d\d:\d\d", str(w.get("end", ""))[:5])]
        d["schedule"] = json.dumps({"enabled": bool(sc.get("enabled")), "windows": wins, "message": str(sc.get("message", ""))[:200]})
    if "departments" in d:
        deps = []
        for x in d["departments"]:
            x = " ".join(str(x).split()).upper()[:40]
            if x and x not in deps:
                deps.append(x)
        d["departments"] = json.dumps(deps)
    for k, v in d.items():
        db.set_setting(c, k, v)
    audit(c, u, "settings.update", detail=d, ip=client_ip(req))
    return {"ok": True}


# ================= competitions =================

class CompIn(BaseModel):
    title: str = Field(min_length=3, max_length=150)
    code: str = Field(min_length=3, max_length=40)
    description: str = Field(default="", max_length=5000)
    instructions: str = Field(default="", max_length=20000)
    academic_year: str = Field(default="", max_length=20)
    semester_label: str = Field(default="", max_length=40)
    round_no: int = Field(default=1, ge=0, le=999)
    category: str = Field(default="General", max_length=60)
    eligible_years: list[int] = []
    eligible_departments: list[str] = []
    registration_mode: str = Field(default="open", pattern="^(open|invite_only)$")
    reg_start: int | str | None = None
    reg_end: int | str | None = None
    start_at: int | str | None = None
    duration_min: int = Field(default=90, ge=5, le=24 * 60)
    languages: list[str] = ["c", "python"]
    max_marks: int = Field(default=100, ge=1, le=100000)
    scoring: dict = {}
    leaderboard_visibility: str = Field(default="hidden", pattern="^(live|after_end|hidden)$")
    show_test_counts: bool = True
    allow_late_join: bool = False
    auto_start: bool = False
    paste_policy: str = Field(default="log", pattern="^(allow|log|block_external)$")
    lockdown: dict = {}
    draw: int = Field(default=0, ge=0, le=200)   # questions per student, drawn from the competition's list (0 = everyone gets all)
    shuffle: bool = True                          # each student sees the questions (and multiple-choice options) in their own order
    ai_pool: bool = True                          # while students register, the AI adds checked variants of these questions
    ai_prompt: str = Field(default="", max_length=1000)   # what the AI should write for this competition's pool


def comp_values(b: CompIn):
    code = re.sub(r"[^A-Z0-9-]", "-", b.code.strip().upper())
    langs = [l for l in b.languages if l in judge.LANGS]
    if not langs:
        raise HTTPException(400, "Choose at least one programming language.")
    sc = dict(contest.DEFAULT_SCORING)
    sc.update({k: v for k, v in (b.scoring or {}).items() if k in contest.DEFAULT_SCORING})
    if sc["mode"] not in ("partial", "all_or_nothing") or sc["tie_break"] not in ("penalty", "time"):
        raise HTTPException(400, "Invalid scoring configuration.")
    for k in ("negative_marks", "first_blood_bonus", "penalty_minutes", "violation_pct"):
        sc[k] = max(0, float(sc[k]) if k in ("negative_marks", "violation_pct") else int(sc[k]))
    sc["violation_pct"] = min(sc["violation_pct"], 50)
    rs, re_, st = ts(b.reg_start), ts(b.reg_end), ts(b.start_at)
    if rs and re_ and re_ < rs:
        raise HTTPException(400, "Registration end must be after registration start.")
    return {"title": b.title.strip(), "code": code, "description": b.description, "instructions": b.instructions, "academic_year": b.academic_year,
            "semester_label": b.semester_label, "round_no": b.round_no, "category": b.category,
            "eligible_years": json.dumps(sorted(set(b.eligible_years))), "eligible_departments": json.dumps([d.strip().upper() for d in b.eligible_departments if d.strip()]),
            "registration_mode": b.registration_mode, "reg_start": rs, "reg_end": re_, "start_at": st, "duration_min": b.duration_min,
            "languages": json.dumps(langs), "max_marks": b.max_marks, "scoring": json.dumps(sc), "leaderboard_visibility": b.leaderboard_visibility,
            "show_test_counts": int(b.show_test_counts), "allow_late_join": int(b.allow_late_join), "auto_start": int(b.auto_start),
            "paste_policy": b.paste_policy, "lockdown": json.dumps(contest.lockdown_cfg(b.lockdown)),
            "draw": b.draw, "shuffle": int(b.shuffle), "ai_pool": int(b.ai_pool), "ai_prompt": b.ai_prompt.strip()}


def comp_full(c, comp):
    d = dict(comp)
    for k in ("languages", "eligible_years", "eligible_departments"):
        d[k] = db.jl(comp[k])
    d["scoring"] = contest.scoring_cfg(comp)
    d["lockdown"] = contest.lockdown_cfg(comp["lockdown"])
    d["display_status"] = contest.display_status(comp)
    d["end_at"] = contest.end_time(comp)
    d["remaining_sec"] = contest.remaining(comp) if comp["status"] in ("live", "paused") else None
    d["questions"] = db.all_(c, "SELECT cq.question_id, cq.version_id, cq.ordinal, cq.marks, qv.title, qv.version_no, qv.validated_at, q.status AS q_status, "
                                "q.difficulty, q.category, q.latest_version_id FROM competition_questions cq JOIN question_versions qv ON qv.id=cq.version_id "
                                "JOIN questions q ON q.id=cq.question_id WHERE cq.competition_id=? ORDER BY cq.ordinal", comp["id"])
    d["participant_count"] = db.one(c, "SELECT COUNT(*) AS n FROM competition_participants WHERE competition_id=?", comp["id"])["n"]
    from . import pool
    d["pool"] = pool.info(c, comp["id"])
    d["explain"] = db.setting(c, f"explain:{comp['id']}", "1") == "1"
    d["variety"] = contest.variety(db.all_(c, "SELECT q.difficulty, qv.qtype FROM competition_questions cq JOIN questions q ON q.id=cq.question_id "
                                              "JOIN question_versions qv ON qv.id=cq.version_id WHERE cq.competition_id=?", comp["id"]), comp["draw"] or 0, bool(comp["shuffle"]))
    d["submission_count"] = db.one(c, "SELECT COUNT(*) AS n FROM submissions WHERE competition_id=? AND kind='submit'", comp["id"])["n"]
    d["admins"] = db.all_(c, "SELECT u.id, u.name, u.username FROM competition_admins ca JOIN users u ON u.id=ca.user_id WHERE ca.competition_id=?", comp["id"])
    d["announcements"] = db.all_(c, "SELECT * FROM announcements WHERE competition_id=? ORDER BY id DESC", comp["id"])
    d["semester"] = db.one(c, "SELECT s.id, s.name, sc.points FROM semester_competitions sc JOIN semesters s ON s.id=sc.semester_id WHERE sc.competition_id=?", comp["id"])
    d["creator"] = (db.one(c, "SELECT name FROM users WHERE id=?", comp["created_by"]) or {}).get("name")
    return d


def comp_or_404(c, cid, u):
    comp = contest.get_comp(c, cid)
    if not comp:
        raise HTTPException(404, "Competition not found.")
    need_comp(c, u, cid)
    return comp


@router.get("/competitions")
def comps(u=Depends(admin), c=Depends(conn)):
    need(u, "competitions")
    if u["role"] == "super_admin":
        rows = db.all_(c, "SELECT * FROM competitions ORDER BY id DESC")
    else:
        rows = db.all_(c, "SELECT co.* FROM competitions co JOIN competition_admins ca ON ca.competition_id=co.id WHERE ca.user_id=? ORDER BY co.id DESC", u["id"])
    out = []
    for r in rows:
        r = contest.tick(c, r)
        out.append({"id": r["id"], "code": r["code"], "title": r["title"], "round_no": r["round_no"], "status": r["status"],
                    "display_status": contest.display_status(r), "start_at": r["start_at"], "duration_min": r["duration_min"],
                    "languages": db.jl(r["languages"]), "academic_year": r["academic_year"],
                    "questions": db.one(c, "SELECT COUNT(*) AS n FROM competition_questions WHERE competition_id=?", r["id"])["n"],
                    "participants": db.one(c, "SELECT COUNT(*) AS n FROM competition_participants WHERE competition_id=?", r["id"])["n"]})
    return out


@router.post("/competitions")
def create_comp(body: CompIn, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "competitions.create")
    v = comp_values(body)
    t = db.now()
    with db.tx(c):
        cols = ",".join(v.keys())
        cid = c.execute(f"INSERT INTO competitions({cols},status,created_by,created_at,updated_at) VALUES({','.join('?' * len(v))},'draft',?,?,?)",
                        (*v.values(), u["id"], t, t)).lastrowid
        c.execute("INSERT INTO competition_admins(competition_id,user_id) VALUES(?,?)", (cid, u["id"]))
        audit(c, u, "competition.create", "competition", cid, cid, {"code": v["code"]}, client_ip(req))
    return comp_full(c, contest.get_comp(c, cid))


class AssistIn(BaseModel):
    message: str = Field(default="", max_length=2000)
    plan: dict = {}
    create: bool = False
    fill: bool = False
    ref: str = Field(default="", max_length=32)
    tests: bool | None = None
    use_file: bool = False                       # put every question of the attached file into the new competition
    per_student: int = Field(default=0, ge=0, le=200)


@router.post("/assistant/competition")
def assistant_competition(body: AssistIn, req: Request, u=Depends(admin), c=Depends(conn)):
    """Chat box on the Competitions page: describe a round in plain words, review the plan, then create it as a draft."""
    need(u, "competitions.create")
    from . import assistant
    plan = assistant.parse(body.message, body.plan, departments(c)) if body.message.strip() else dict(body.plan)
    random_state = plan.pop("picked", None)  # keep the same questions between turns unless the request changed
    chosen, short, missing = assistant.pick(c, plan)
    if random_state and not body.message.strip():
        keep = {q["id"]: q for q in db.all_(c, "SELECT q.id, q.category, q.difficulty, qv.title, qv.qtype FROM questions q JOIN question_versions qv ON qv.id=q.latest_version_id "
                                               f"WHERE q.status='approved' AND q.id IN ({','.join('?' * len(random_state))})", *random_state)}
        chosen = [keep[i] for i in random_state if i in keep] or chosen
    out = assistant.describe(plan, chosen, short)
    out["missing"] = missing if ai.enabled() and "ai" in u.get("modules", ["ai"]) else []
    if any(m.get("type") in (None, "standard", "debug") for m in out["missing"]) and "tests" not in plan:
        out["ask"] = [{"q": "If the AI writes the missing coding questions, how should answers be checked?",
                       "chips": ["with hidden test cases", "simple input output only"]}] + (out.get("ask") or [])[:1]
    if body.fill and out["missing"]:   # "Write the missing ones with AI": drafts appear in AI Studio for approval
        from . import pool
        ref = ref_text(body.ref)
        tests = body.tests if body.tests is not None else plan.get("tests")
        started = {m.get("type"): pool.spawn(c, u["id"], {**m, "tests": tests}, ref) for m in out["missing"]}
        audit(c, u, "assistant.fill", detail={"started": sum(started.values())}, ip=client_ip(req))
        out["filling"] = {"started": sum(started.values()), "skipped": [assistant.TYPE_NAME[k] for k, v in started.items() if not v]}
    plan["picked"] = [q["id"] for q in chosen]
    out["plan"] = plan
    if body.ref and not body.create:   # a question paper attached in the chat: say what is in it
        out["file"] = found_summary(pdfimport.find_questions(ref_text(body.ref)))
    if not body.create:
        return out
    if not chosen and not (body.use_file and body.ref) and not (ai.enabled() and (plan.get("questions") or plan.get("per_student"))):
        raise HTTPException(400, "No questions matched yet. Tell me which questions you want first, or attach a question paper.")
    title = plan.get("title") or assistant.default_title({**plan, "questions": plan.get("questions") or ([{"type": "mcq"}] if body.use_file else [])}, datetime.now(TZ))
    base = re.sub(r"[^A-Z0-9]+", "-", title.upper()).strip("-") or "ROUND"
    base = base if len(base) <= 24 else (base[:24].rsplit("-", 1)[0] or base[:24])
    code, n = base, 2
    while db.one(c, "SELECT 1 AS x FROM competitions WHERE code=?", code):
        code, n = f"{base}-{n}", n + 1
    marks = int(plan.get("marks") or 100)
    per_student = sum(int(q.get("count") or 0) for q in plan.get("questions") or []) if plan.get("unique") else 0
    yr = datetime.now(TZ)
    comp = create_comp(CompIn(title=title, code=code, description="Created with the competition assistant.", duration_min=int(plan.get("duration_min") or 90),
                              academic_year=f"{yr.year if yr.month >= 6 else yr.year - 1}-{str((yr.year if yr.month >= 6 else yr.year - 1) + 1)[2:]}",
                              eligible_years=plan.get("eligible_years") or [], eligible_departments=plan.get("eligible_departments") or [],
                              start_at=plan.get("start_at"), languages=plan.get("languages") or ["c", "python"], max_marks=max(1, marks * len(chosen)),
                              scoring={"mode": plan.get("scoring_mode") or "partial"}, leaderboard_visibility=plan.get("leaderboard_visibility") or "hidden",
                              lockdown=plan.get("lockdown") or {}, draw=per_student if per_student < len(chosen) else 0), req, u, c)
    if chosen:
        set_questions(comp["id"], QuestionsIn(questions=[{"question_id": q["id"], "marks": marks} for q in chosen]), req, u, c)
    if body.use_file and body.ref:   # every question from the attached paper, as written
        out["file_import"] = import_file(c, u, body.ref, contest.get_comp(c, comp["id"]), plan.get("level") or "Easy", title[:60],
                                         int(plan.get("per_student") or 0) or body.per_student, 10, req)
    # what was asked for: N per student, or the sum of the parts named in the chat
    asked = int(plan.get("per_student") or 0) or body.per_student or sum(int(q.get("count") or 0) for q in plan.get("questions") or [])
    have = db.one(c, "SELECT COUNT(*) AS n FROM competition_questions WHERE competition_id=?", comp["id"])["n"]
    out["writing"] = 0
    if asked and have < asked and ai.enabled():   # short: the AI writes the rest now (from the attached files if any); checked ones join by themselves
        from . import pool
        tag = f"for-comp-{comp['id']}"
        ref = ref_text(body.ref) if body.ref else ""
        todo = missing or [{"count": asked - have, "type": "mcq" if (ref or not plan.get("questions")) else None, "difficulty": plan.get("level"), "topic": ""}]
        for m in todo:
            out["writing"] += pool.spawn(c, u["id"], {**m, "difficulty": m.get("difficulty") or plan.get("level"), "comp_tag": tag,
                                                      "tests": plan.get("tests", False) if m.get("type") in (None, "standard", "debug") else None}, ref)
    if asked and (body.per_student or plan.get("per_student") or plan.get("unique")):
        c.execute("UPDATE competitions SET draw=? WHERE id=?", (asked, comp["id"]))
    mix = {(q.get("type") or "standard"): int(q["count"]) for q in plan.get("questions") or [] if q.get("count")}
    if mix and asked and sum(mix.values()) <= asked:   # "6 fix the bug and 4 mcq" per student: exactly that mix for everyone
        db.set_setting(c, f"mix:{comp['id']}", json.dumps(mix))
    total = int(plan.get("total_marks") or 100)
    if total:   # total marks (100 unless said otherwise) split evenly over what each student answers
        per_q = max(1, round(total / max(1, asked or have or 1)))
        c.execute("UPDATE competition_questions SET marks=? WHERE competition_id=?", (per_q, comp["id"]))
        c.execute("UPDATE competitions SET max_marks=? WHERE id=?", (total, comp["id"]))
        out["marks_each"] = per_q
    out["have"], out["asked"] = have, asked
    out.update(created={"id": comp["id"], "title": title, "code": code})
    return out


class QuickIn(BaseModel):
    minutes: int = Field(default=15, ge=5, le=180)
    mcq: int = Field(default=3, ge=0, le=30)
    coding: int = Field(default=1, ge=0, le=10)
    difficulty: str = Field(default="Easy", pattern="^(Easy|Intermediate|Challenging)$")
    strict: bool = False   # exam rules on: full screen, warnings for leaving / internet, auto-submit at 3


@router.post("/competitions/quick")
def quick_round(body: QuickIn, req: Request, u=Depends(admin), c=Depends(conn)):
    """Instant test round: picks questions, publishes and starts now, so organisers can try the whole flow in a minute."""
    need(u, "competitions.create")
    from . import assistant
    specs = [s for s in ({"count": body.mcq, "type": "mcq", "difficulty": body.difficulty, "topic": ""},
                         {"count": body.coding, "type": "standard", "difficulty": body.difficulty, "topic": ""}) if s["count"]]
    if not specs:
        raise HTTPException(400, "Choose at least one question.")
    chosen, short, _ = assistant.pick(c, {"questions": specs, "languages": ["c", "python"]})
    if not chosen:
        raise HTTPException(400, "The question bank has no approved questions of that kind yet. " + " ".join(short))
    t = datetime.now(TZ)
    code, n = f"TEST-{t:%d%m-%H%M}", 2
    while db.one(c, "SELECT 1 AS x FROM competitions WHERE code=?", code):
        code, n = f"TEST-{t:%d%m-%H%M}-{n}", n + 1
    comp = create_comp(CompIn(title=f"Test round {t:%d %b %I:%M %p}", code=code, start_at=db.now(), auto_start=True, description="Instant test round created from the Competitions page.",
                              duration_min=body.minutes, category="Practice round", languages=["c", "python"], max_marks=100 * len(chosen),
                              allow_late_join=True, leaderboard_visibility="live", lockdown={"max_violations": 3} if body.strict else {"fullscreen": "off", "leave": "allow"}), req, u, c)
    set_questions(comp["id"], QuestionsIn(questions=[{"question_id": q["id"], "marks": 10 if q["qtype"] in judge.ANSWER_QTYPES else 100} for q in chosen]), req, u, c)
    comp_action(comp["id"], ActionIn(action="publish"), req, u, c)
    if contest.get_comp(c, comp["id"])["status"] != "live":   # start time is now, so it usually starts by itself
        comp_action(comp["id"], ActionIn(action="start"), req, u, c)
    return {"id": comp["id"], "code": code, "questions": len(chosen), "problems": short}


class VariantsIn(BaseModel):
    per: int = Field(default=1, ge=1, le=3)


@router.post("/competitions/{cid}/variants")
def make_variants(cid: int, body: VariantsIn, req: Request, u=Depends(admin), c=Depends(conn)):
    """Instant number variants of the predict-the-output questions in this competition (no AI)."""
    comp_or_404(c, cid, u)
    from . import pool
    n = pool.number_variants(c, cid, body.per, u["id"])
    if n < 0:
        raise HTTPException(409, "Variants can only be added before the competition starts.")
    if n == 0:
        raise HTTPException(400, "Add some predict-the-output questions (with a C program) first; those are the ones that get new numbers. Or wait for the current job to finish.")
    audit(c, u, "competition.variants", "competition", cid, cid, {"questions": n, "per": body.per}, client_ip(req))
    return {"questions": n, "per": body.per}


class PoolIn(BaseModel):
    count: int = Field(default=3, ge=1, le=15)


@router.post("/competitions/{cid}/pool")
def grow_pool(cid: int, body: PoolIn, req: Request, u=Depends(admin), c=Depends(conn)):
    comp = comp_or_404(c, cid, u)
    from . import pool
    need_ai()
    if comp["status"] not in ("draft", "published"):
        raise HTTPException(409, "The question pool can only grow before the competition starts.")
    if not any(pool.provable(q) for q in db.all_(c, "SELECT qv.qtype, qv.statement FROM competition_questions cq JOIN question_versions qv ON qv.id=cq.version_id WHERE cq.competition_id=?", cid)):
        raise HTTPException(400, "Add at least one coding or predict-the-output question first: the AI writes variants of those and proves each answer by running it.")
    started = pool.maybe_grow(c, cid, body.count)
    audit(c, u, "competition.pool", "competition", cid, cid, {"count": body.count}, client_ip(req))
    return {"started": started, **pool.info(c, cid)}


@router.get("/competitions/{cid}")
def get_comp(cid: int, u=Depends(admin), c=Depends(conn)):
    return comp_full(c, comp_or_404(c, cid, u))


@router.put("/competitions/{cid}")
def update_comp(cid: int, body: CompIn, req: Request, u=Depends(admin), c=Depends(conn)):
    comp = comp_or_404(c, cid, u)
    if comp["status"] in ("finalized", "archived", "cancelled"):
        raise HTTPException(409, "This competition is closed; its settings can no longer change.")
    v = comp_values(body)
    if comp["status"] in ("live", "paused", "ended"):  # timing/scoring locked once running: use pause/extend instead
        for k in ("start_at", "duration_min", "scoring", "languages", "draw", "shuffle"):
            v.pop(k)
    with db.tx(c):
        c.execute(f"UPDATE competitions SET {','.join(k + '=?' for k in v)}, updated_at=? WHERE id=?", (*v.values(), db.now(), cid))
        audit(c, u, "competition.update", "competition", cid, cid, {k: v[k] for k in v if k not in ("instructions", "description")}, client_ip(req))
    return comp_full(c, contest.get_comp(c, cid))


class QuestionsIn(BaseModel):
    questions: list[dict]  # [{question_id, marks}] in order


@router.put("/competitions/{cid}/questions")
def set_questions(cid: int, body: QuestionsIn, req: Request, u=Depends(admin), c=Depends(conn)):
    comp = comp_or_404(c, cid, u)
    if comp["status"] != "draft":
        raise HTTPException(409, "Questions can only be changed while the competition is a draft. Unpublish it first.")
    with db.tx(c):
        c.execute("DELETE FROM competition_questions WHERE competition_id=?", (cid,))
        for i, q in enumerate(body.questions):
            row = db.one(c, "SELECT * FROM questions WHERE id=? AND status!='archived'", int(q["question_id"]))
            if not row:
                raise HTTPException(400, f"Question {q['question_id']} not found or archived.")
            c.execute("INSERT INTO competition_questions(competition_id,question_id,version_id,ordinal,marks) VALUES(?,?,?,?,?)",
                      (cid, row["id"], row["latest_version_id"], i, max(1, int(q.get("marks") or 100))))
        audit(c, u, "competition.questions", "competition", cid, cid, {"questions": [q["question_id"] for q in body.questions]}, client_ip(req))
    return comp_full(c, contest.get_comp(c, cid))


def publish_checks(c, comp):
    problems = []
    qs = db.all_(c, "SELECT cq.question_id, q.latest_version_id, q.status, qv.title, qv.validated_at, qv.languages FROM competition_questions cq "
                    "JOIN questions q ON q.id=cq.question_id JOIN question_versions qv ON qv.id=q.latest_version_id WHERE cq.competition_id=?", comp["id"])
    if not qs:
        problems.append("Add at least one question.")
    for q in qs:
        if q["status"] != "approved":
            problems.append(f"“{q['title']}” is not approved yet.")
        if not q["validated_at"]:
            problems.append(f"“{q['title']}” has not passed reference-solution validation.")
    if not comp["start_at"]:
        problems.append("Set a start date and time.")
    avail = judge.available_languages()
    missing = [l for l in db.jl(comp["languages"]) if not avail.get(l)]
    if missing:
        problems.append(f"The judge does not have these languages installed: {', '.join(missing)}.")
    return problems, qs


class ActionIn(BaseModel):
    action: str
    minutes: int | None = Field(default=None, ge=1, le=600)
    reason: str | None = Field(default=None, max_length=300)


TRANSITIONS = {
    "publish": ({"draft"}, "published"), "unpublish": ({"published"}, "draft"), "start": ({"published"}, "live"),
    "pause": ({"live"}, "paused"), "resume": ({"paused"}, "live"), "extend": ({"live", "paused"}, None),
    "end": ({"live", "paused"}, "ended"), "finalize": ({"ended"}, "finalized"), "archive": ({"ended", "finalized", "cancelled"}, "archived"),
    "cancel": ({"draft", "published"}, "cancelled"),
}


@router.post("/competitions/{cid}/action")
def comp_action(cid: int, body: ActionIn, req: Request, u=Depends(admin), c=Depends(conn)):
    comp = comp_or_404(c, cid, u)
    if body.action not in TRANSITIONS:
        raise HTTPException(400, "Unknown action.")
    allowed, new = TRANSITIONS[body.action]
    if comp["status"] not in allowed:
        raise HTTPException(409, f"Cannot {body.action} a competition that is {comp['status']}.")
    t = db.now()
    with db.tx(c):
        comp = db.one(c, "SELECT * FROM competitions WHERE id=?", cid)  # re-read inside the write lock
        if comp["status"] not in allowed:
            raise HTTPException(409, "The competition changed state; refresh and try again.")
        sets = {"status": new} if new else {}
        if body.action == "publish":
            problems, qs = publish_checks(c, comp)
            if problems:
                raise HTTPException(400, "Cannot publish yet: " + " ".join(problems))
            for q in qs:  # pin the exact validated versions
                c.execute("UPDATE competition_questions SET version_id=? WHERE competition_id=? AND question_id=?", (q["latest_version_id"], cid, q["question_id"]))
        elif body.action == "start":
            sets.update(start_at=t, started_at=t, paused_total_sec=0, extra_min=comp["extra_min"])
        elif body.action == "pause":
            sets["paused_at"] = t
        elif body.action == "resume":
            sets.update(paused_total_sec=comp["paused_total_sec"] + (t - (comp["paused_at"] or t)), paused_at=None)
        elif body.action == "extend":
            if not body.minutes:
                raise HTTPException(400, "Give the number of minutes to extend.")
            sets["extra_min"] = comp["extra_min"] + body.minutes
        elif body.action == "end":
            if comp["status"] == "paused":
                sets.update(paused_total_sec=comp["paused_total_sec"] + (t - (comp["paused_at"] or t)), paused_at=None)
            sets["ended_at"] = t
        elif body.action == "finalize":
            pend = db.one(c, "SELECT COUNT(*) AS n FROM submissions WHERE competition_id=? AND status IN ('queued','running')", cid)["n"]
            if pend:
                raise HTTPException(409, f"{pend} submission(s) are still being judged. Finalize once the queue is empty.")
            contest.finalize(c, comp)
            sets["finalized_at"] = t
        if sets:
            c.execute(f"UPDATE competitions SET {','.join(k + '=?' for k in sets)}, updated_at=? WHERE id=?", (*sets.values(), t, cid))
        if body.action in ("pause", "resume", "extend", "end"):
            msg = {"pause": "The competition is paused. The timer is frozen; submissions resume when organisers resume.",
                   "resume": "The competition has resumed.", "extend": f"Time extended by {body.minutes} minutes for everyone.",
                   "end": "The competition has ended."}[body.action]
            c.execute("INSERT INTO announcements(competition_id,title,body,created_by,created_at) VALUES(?,?,?,?,?)",
                      (cid, msg, body.reason or "", u["id"], t))
        audit(c, u, f"competition.{body.action}", "competition", cid, cid, {"minutes": body.minutes, "reason": body.reason}, client_ip(req))
    return comp_full(c, contest.get_comp(c, cid))


@router.post("/competitions/{cid}/duplicate")
def duplicate(cid: int, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "competitions.create")
    comp = comp_or_404(c, cid, u)
    t = db.now()
    skip = {"id", "code", "title", "status", "created_by", "created_at", "updated_at", "started_at", "ended_at", "finalized_at",
            "paused_at", "paused_total_sec", "extra_min", "start_at", "reg_start", "reg_end"}
    v = {k: comp[k] for k in comp if k not in skip}
    base = f"{comp['code']}-COPY"
    code, n = base, 2
    while db.one(c, "SELECT 1 AS x FROM competitions WHERE code=?", code):
        code, n = f"{base}{n}", n + 1
    v.update(code=code, title=f"{comp['title']} (Copy)", status="draft", created_by=u["id"], created_at=t, updated_at=t)
    with db.tx(c):
        nid = c.execute(f"INSERT INTO competitions({','.join(v)}) VALUES({','.join('?' * len(v))})", tuple(v.values())).lastrowid
        c.execute("INSERT INTO competition_admins(competition_id,user_id) VALUES(?,?)", (nid, u["id"]))
        c.execute("INSERT INTO competition_questions(competition_id,question_id,version_id,ordinal,marks) "
                  "SELECT ?, cq.question_id, q.latest_version_id, cq.ordinal, cq.marks FROM competition_questions cq JOIN questions q ON q.id=cq.question_id "
                  "WHERE cq.competition_id=? AND q.status!='archived'", (nid, cid))
        audit(c, u, "competition.duplicate", "competition", nid, nid, {"from": cid}, client_ip(req))
    return comp_full(c, contest.get_comp(c, nid))


@router.delete("/competitions/{cid}")
def delete_comp(cid: int, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "*") if u["role"] != "super_admin" else None
    comp = comp_or_404(c, cid, u)
    if comp["status"] not in ("draft", "cancelled"):
        raise HTTPException(409, "Only draft or cancelled competitions can be deleted. Archive finished ones instead.")
    if db.one(c, "SELECT 1 AS x FROM submissions WHERE competition_id=? LIMIT 1", cid):
        raise HTTPException(409, "This competition has submissions; archive it instead of deleting.")
    with db.tx(c):
        c.execute("DELETE FROM activity_events WHERE competition_id=?", (cid,))
        c.execute("DELETE FROM competitions WHERE id=?", (cid,))
        audit(c, u, "competition.delete", "competition", cid, None, {"code": comp["code"]}, client_ip(req))
    return {"ok": True}


class AdminsIn(BaseModel):
    user_ids: list[int]


@router.put("/competitions/{cid}/admins")
def set_comp_admins(cid: int, body: AdminsIn, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "admins")
    comp_or_404(c, cid, u)
    with db.tx(c):
        c.execute("DELETE FROM competition_admins WHERE competition_id=?", (cid,))
        for uid in set(body.user_ids):
            if db.one(c, "SELECT 1 AS x FROM users WHERE id=? AND kind='admin'", uid):
                c.execute("INSERT INTO competition_admins(competition_id,user_id) VALUES(?,?)", (cid, uid))
        audit(c, u, "competition.admins", "competition", cid, cid, {"user_ids": body.user_ids}, client_ip(req))
    return {"ok": True}


class AnnounceIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    body: str = Field(default="", max_length=5000)
    competition_id: int | None = None


@router.post("/announcements")
def announce(body: AnnounceIn, req: Request, u=Depends(admin), c=Depends(conn)):
    if body.competition_id:
        comp_or_404(c, body.competition_id, u)
    else:
        need(u, "settings")
    aid = c.execute("INSERT INTO announcements(competition_id,title,body,created_by,created_at) VALUES(?,?,?,?,?)",
                    (body.competition_id, body.title, body.body, u["id"], db.now())).lastrowid
    audit(c, u, "announcement.create", "announcement", aid, body.competition_id, {"title": body.title}, client_ip(req))
    return {"id": aid}


@router.get("/announcements")
def list_announcements(u=Depends(admin), c=Depends(conn)):
    return db.all_(c, "SELECT a.*, co.title AS competition FROM announcements a LEFT JOIN competitions co ON co.id=a.competition_id ORDER BY a.id DESC LIMIT 100")


@router.delete("/announcements/{aid}")
def del_announcement(aid: int, req: Request, u=Depends(admin), c=Depends(conn)):
    a = db.one(c, "SELECT * FROM announcements WHERE id=?", aid)
    if not a:
        raise HTTPException(404, "Not found.")
    comp_or_404(c, a["competition_id"], u) if a["competition_id"] else need(u, "settings")
    c.execute("DELETE FROM announcements WHERE id=?", (aid,))
    audit(c, u, "announcement.delete", "announcement", aid, a["competition_id"], ip=client_ip(req))
    return {"ok": True}


# ----- competition participants, live monitor, integrity -----

class AssignIn(BaseModel):
    identifiers: list[str] = []  # participant IDs or USNs
    user_ids: list[int] = []


@router.get("/competitions/{cid}/participants")
def comp_participants(cid: int, u=Depends(admin), c=Depends(conn)):
    comp_or_404(c, cid, u)
    rows = db.all_(c, "SELECT cp.user_id, cp.joined_at, cp.source, cp.status, us.name, us.last_login_ip, s.participant_id, s.usn, s.department, s.year, s.section "
                      "FROM competition_participants cp JOIN users us ON us.id=cp.user_id LEFT JOIN students s ON s.user_id=us.id WHERE cp.competition_id=? ORDER BY us.name", cid)
    subs = {r["user_id"]: r for r in db.all_(c, "SELECT user_id, COUNT(*) AS n, SUM(verdict='AC') AS ac, MAX(created_at) AS last FROM submissions WHERE competition_id=? AND kind='submit' GROUP BY user_id", cid)}
    online = {r["user_id"] for r in db.all_(c, "SELECT DISTINCT user_id FROM activity_events WHERE competition_id=? AND created_at>?", cid, db.now() - 60)}
    fin = {r["user_id"]: r["finished_at"] for r in db.all_(c, "SELECT user_id, finished_at FROM participant_finish WHERE competition_id=?", cid)}
    for r in rows:
        s = subs.get(r["user_id"], {})
        r.update(submissions=s.get("n", 0), accepted=s.get("ac") or 0, last_submission=s.get("last"), online=r["user_id"] in online, finished_at=fin.get(r["user_id"]))
    return rows


@router.post("/competitions/{cid}/participants")
def assign(cid: int, body: AssignIn, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "participants")
    comp_or_404(c, cid, u)
    added, missing = 0, []
    with db.tx(c):
        ids = list(body.user_ids)
        for ident in body.identifiers:
            ident = ident.strip()
            if not ident:
                continue
            s = db.one(c, "SELECT user_id FROM students WHERE participant_id=? COLLATE NOCASE OR usn=?", ident, ident)
            (ids.append(s["user_id"]) if s else missing.append(ident))
        for uid in set(ids):
            if db.one(c, "SELECT 1 AS x FROM students WHERE user_id=?", uid):
                added += c.execute("INSERT OR IGNORE INTO competition_participants(competition_id,user_id,joined_at,source) VALUES(?,?,?,'organiser')",
                                   (cid, uid, db.now())).rowcount
        audit(c, u, "competition.assign", "competition", cid, cid, {"added": added, "missing": missing}, client_ip(req))
    return {"added": added, "missing": missing}


class PartStatusIn(BaseModel):
    status: str = Field(pattern="^(registered|disqualified|removed)$")
    reason: str | None = Field(default=None, max_length=300)


@router.post("/competitions/{cid}/participants/{uid}")
def part_status(cid: int, uid: int, body: PartStatusIn, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "participants")
    comp_or_404(c, cid, u)
    if body.status == "removed":
        if db.one(c, "SELECT 1 AS x FROM submissions WHERE competition_id=? AND user_id=? AND kind='submit' LIMIT 1", cid, uid):
            raise HTTPException(409, "This participant has submissions; disqualify instead of removing.")
        c.execute("DELETE FROM competition_participants WHERE competition_id=? AND user_id=?", (cid, uid))
    else:
        c.execute("UPDATE competition_participants SET status=? WHERE competition_id=? AND user_id=?", (body.status, cid, uid))
    audit(c, u, f"participant.{body.status}", "user", uid, cid, {"reason": body.reason}, client_ip(req))
    return {"ok": True}


@router.get("/competitions/{cid}/monitor")
def monitor(cid: int, u=Depends(admin), c=Depends(conn)):
    comp = comp_or_404(c, cid, u)
    t = db.now()
    feed = db.all_(c, "SELECT s.id, s.user_id, s.question_id, s.language, s.status, s.verdict, s.score, s.created_at, us.name, qv.title FROM submissions s "
                      "JOIN users us ON us.id=s.user_id JOIN question_versions qv ON qv.id=s.version_id WHERE s.competition_id=? AND s.kind='submit' ORDER BY s.id DESC LIMIT 30", cid)
    flags = db.all_(c, "SELECT a.type, a.detail, a.created_at, a.user_id, us.name FROM activity_events a JOIN users us ON us.id=a.user_id WHERE a.competition_id=? "
                       "AND a.type IN ('paste','paste_blocked','copy','cut','tab_hidden','devtools','fullscreen_exit','internet') ORDER BY a.id DESC LIMIT 40", cid)
    per_q = db.all_(c, "SELECT question_id, COUNT(*) AS attempts, COUNT(DISTINCT CASE WHEN verdict='AC' THEN user_id END) AS solvers FROM submissions "
                       "WHERE competition_id=? AND kind='submit' GROUP BY question_id", cid)
    return {"status": comp["status"], "display_status": contest.display_status(comp), "remaining_sec": contest.remaining(comp),
            "online": db.one(c, "SELECT COUNT(DISTINCT user_id) AS n FROM activity_events WHERE competition_id=? AND created_at>?", cid, t - 60)["n"],
            "finished": db.one(c, "SELECT COUNT(*) AS n FROM participant_finish WHERE competition_id=?", cid)["n"],
            "participants": db.one(c, "SELECT COUNT(*) AS n FROM competition_participants WHERE competition_id=?", cid)["n"],
            "queue": db.one(c, "SELECT COUNT(*) AS n FROM submissions WHERE competition_id=? AND status IN ('queued','running')", cid)["n"],
            "last_5min": db.one(c, "SELECT COUNT(*) AS n FROM submissions WHERE competition_id=? AND kind='submit' AND created_at>?", cid, t - 300)["n"],
            "feed": feed, "flags": flags, "per_question": per_q}


def integrity_rows(c, cid):
    evs = db.all_(c, "SELECT user_id, type, detail, created_at, ip FROM activity_events WHERE competition_id=? AND type!='snapshot' ORDER BY created_at, id", cid)
    users = {r["user_id"]: r for r in db.all_(c, "SELECT cp.user_id, us.name, s.participant_id, s.usn, s.department FROM competition_participants cp "
                                                  "JOIN users us ON us.id=cp.user_id LEFT JOIN students s ON s.user_id=us.id WHERE cp.competition_id=?", cid)}
    st = {uid: {"pastes": 0, "paste_chars": 0, "large_pastes": 0, "blocked_pastes": 0, "copies": 0, "tab_switches": 0, "away_sec": 0,
                "blurs": 0, "devtools": 0, "fullscreen_exits": 0, "ips": set(), "keystrokes": 0, "internet": 0, "ai_uses": 0, "_hidden_at": None} for uid in users}
    for e in evs:
        s = st.get(e["user_id"])
        if not s:
            continue
        d = db.jl(e["detail"], {}) if e["detail"] else {}
        if e["ip"]:
            s["ips"].add(e["ip"])
        typ = e["type"]
        if typ == "paste":
            n = int(d.get("chars", 0)) if isinstance(d, dict) else 0
            s["pastes"] += 1
            s["paste_chars"] += n
            if n >= 80 and not (isinstance(d, dict) and d.get("internal")):
                s["large_pastes"] += 1
        elif typ == "paste_blocked":
            s["blocked_pastes"] += 1
        elif typ in ("copy", "cut"):
            s["copies"] += 1
        elif typ == "tab_hidden":
            s["tab_switches"] += 1
            s["_hidden_at"] = e["created_at"]
        elif typ == "tab_visible" and s["_hidden_at"]:
            s["away_sec"] += max(0, e["created_at"] - s["_hidden_at"])
            s["_hidden_at"] = None
        elif typ == "blur":
            s["blurs"] += 1
        elif typ == "devtools":
            s["devtools"] += 1
        elif typ == "fullscreen_exit":
            s["fullscreen_exits"] += 1
        elif typ == "internet":
            s["internet"] += 1
        elif typ == "ai_tool":
            s["ai_uses"] += 1
        elif typ == "keystrokes":
            s["keystrokes"] += int(d.get("n", 0)) if isinstance(d, dict) else 0
    ip_users = {}
    for uid, s in st.items():
        for ip in s["ips"]:
            ip_users.setdefault(ip, set()).add(uid)
    out = []
    for uid, s in st.items():
        shared = sorted({users[o]["name"] for ip in s["ips"] for o in ip_users[ip] if o != uid and o in users})
        risk = min(100, s["large_pastes"] * 15 + s["blocked_pastes"] * 10 + s["tab_switches"] * 3 + s["away_sec"] // 30 + s["devtools"] * 10 + len(shared) * 15
                   + min(30, s["internet"] * 10) + s["fullscreen_exits"] * 4)
        out.append({"user_id": uid, **{k: users[uid][k] for k in ("name", "participant_id", "usn", "department")},
                    **{k: v for k, v in s.items() if not k.startswith("_") and k != "ips"}, "ips": sorted(s["ips"]), "shared_ip_with": shared, "risk": risk})
    return sorted(out, key=lambda r: -r["risk"])


@router.get("/competitions/{cid}/integrity")
def integrity(cid: int, u=Depends(admin), c=Depends(conn)):
    comp_or_404(c, cid, u)
    return integrity_rows(c, cid)


@router.get("/competitions/{cid}/activity/{uid}")
def activity(cid: int, uid: int, u=Depends(admin), c=Depends(conn)):
    comp_or_404(c, cid, u)
    events = db.all_(c, "SELECT id, question_id, type, detail, ip, created_at FROM activity_events WHERE competition_id=? AND user_id=? ORDER BY created_at, id", cid, uid)
    for e in events:
        e["detail"] = db.jl(e["detail"], {}) if e["detail"] else None
    subs = db.all_(c, "SELECT id, question_id, language, verdict, score, created_at, code FROM submissions WHERE competition_id=? AND user_id=? AND kind='submit' ORDER BY id", cid, uid)
    person = db.one(c, "SELECT us.name, us.last_login_ip, s.* FROM users us LEFT JOIN students s ON s.user_id=us.id WHERE us.id=?", uid)
    fin = db.one(c, "SELECT finished_at FROM participant_finish WHERE competition_id=? AND user_id=?", cid, uid)
    return {"person": person, "events": events, "submissions": subs, "finished_at": fin["finished_at"] if fin else None}


@router.post("/competitions/{cid}/participants/{uid}/reopen")
def reopen_test(cid: int, uid: int, req: Request, u=Depends(admin), c=Depends(conn)):
    """Let a student continue after they finished or were auto-submitted for fair-play warnings (their warning count restarts)."""
    comp = comp_or_404(c, cid, u)
    if comp["status"] not in ("live", "paused"):
        raise HTTPException(409, "Only possible while the competition is running.")
    c.execute("DELETE FROM participant_finish WHERE competition_id=? AND user_id=?", (cid, uid))
    c.execute("INSERT INTO activity_events(competition_id,user_id,type,detail,ip,created_at) VALUES(?,?,'reopened',?,?,?)",
              (cid, uid, json.dumps({"by": u["username"]}), client_ip(req), db.now()))
    audit(c, u, "competition.reopen_participant", "user", uid, cid, ip=client_ip(req))
    return {"ok": True}


def _norm_code(code):
    code = re.sub(r"//[^\n]*|#[^\n]*|/\*.*?\*/", "", code, flags=re.S)
    return re.sub(r"\s+", "", code)


@router.get("/competitions/{cid}/similarity")
def similarity(cid: int, threshold: float = 0.85, u=Depends(admin), c=Depends(conn)):
    """Pairs of participants whose latest submissions to the same question are near-identical after removing comments/whitespace.
    ponytail: O(n^2) per question with difflib, fine for a few hundred participants; use winnowing/MOSS beyond that."""
    comp_or_404(c, cid, u)
    rows = db.all_(c, "SELECT s.user_id, s.question_id, s.language, s.code, us.name, qv.title FROM submissions s JOIN users us ON us.id=s.user_id "
                      "JOIN question_versions qv ON qv.id=s.version_id WHERE s.id IN (SELECT MAX(id) FROM submissions WHERE competition_id=? AND kind='submit' "
                      "GROUP BY user_id, question_id)", cid)
    by_q = {}
    for r in rows:
        r["n"] = _norm_code(r["code"])
        by_q.setdefault((r["question_id"], r["language"]), []).append(r)
    pairs = []
    for (qid, lang), lst in by_q.items():
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                a, b = lst[i], lst[j]
                if not a["n"] or not b["n"]:
                    continue
                sm = SequenceMatcher(None, a["n"], b["n"], autojunk=False)
                if sm.real_quick_ratio() < threshold or sm.quick_ratio() < threshold:
                    continue
                r = sm.ratio()
                if r >= threshold:
                    pairs.append({"question": a["title"], "language": lang, "similarity": round(r * 100, 1),
                                  "a": {"user_id": a["user_id"], "name": a["name"]}, "b": {"user_id": b["user_id"], "name": b["name"]}})
    return sorted(pairs, key=lambda p: -p["similarity"])


@router.get("/competitions/{cid}/leaderboard")
def admin_lb(cid: int, u=Depends(admin), c=Depends(conn)):
    comp = comp_or_404(c, cid, u)
    lb = contest.leaderboard(c, comp)
    lb["display_status"] = contest.display_status(comp)
    lb["visibility"] = comp["leaderboard_visibility"]
    lb["results_published_at"] = comp["results_published_at"]
    lb["results_visible"] = contest.results_visible(comp)
    lb["status"] = comp["status"]
    return lb


class PublishIn(BaseModel):
    publish: bool = True
    at: int | str | None = None   # schedule: release at this time instead of now (only with publish=true)


@router.post("/competitions/{cid}/results")
def publish_results(cid: int, body: PublishIn, req: Request, u=Depends(admin), c=Depends(conn)):
    """Release (or withdraw) results to students: their scores, ranks, the leaderboard and the 'results are out' celebration."""
    comp = comp_or_404(c, cid, u)
    when = ts(body.at) if body.publish and body.at else None
    if when and when > db.now():   # scheduled: released by itself at that time, once the round has ended
        if comp["status"] in ("draft", "cancelled"):
            raise HTTPException(409, "Publish the competition first.")
        c.execute("UPDATE competitions SET results_at=?, results_published_at=NULL, updated_at=? WHERE id=?", (when, db.now(), cid))
        audit(c, u, "competition.results_schedule", "competition", cid, cid, {"at": when}, client_ip(req))
        return comp_full(c, contest.get_comp(c, cid))
    if body.publish and comp["status"] not in ("ended", "finalized", "archived"):
        raise HTTPException(409, "End the competition before publishing results, or schedule them for a later time.")
    if body.publish:
        pend = db.one(c, "SELECT COUNT(*) AS n FROM submissions WHERE competition_id=? AND status IN ('queued','running')", cid)["n"]
        if pend:
            raise HTTPException(409, f"{pend} submission(s) are still being judged. Publish once the queue is empty.")
    c.execute("UPDATE competitions SET results_published_at=?, results_at=NULL, updated_at=? WHERE id=?", (db.now() if body.publish else None, db.now(), cid))
    if body.publish:
        c.execute("INSERT INTO announcements(competition_id,title,body,created_by,created_at) VALUES(?,?,?,?,?)",
                  (cid, "Results are out!", "Open your profile or the leaderboard to see your rank.", u["id"], db.now()))
    audit(c, u, "competition.results_" + ("publish" if body.publish else "unpublish"), "competition", cid, cid, ip=client_ip(req))
    return comp_full(c, contest.get_comp(c, cid))


@router.post("/competitions/{cid}/grade-saved")
def grade_saved(cid: int, req: Request, u=Depends(admin), c=Depends(conn)):
    """Judge each student's last auto-saved answer (editor snapshot) where it is newer than their last submission.
    For when a PC, browser or the network failed during the round. Runs through the normal judge, so it is scored like any submission."""
    comp = comp_or_404(c, cid, u)
    if comp["status"] not in ("paused", "ended", "finalized"):
        raise HTTPException(409, "Pause or end the competition first.")
    langs = db.jl(comp["languages"])
    queued, t = 0, db.now()
    with db.tx(c):
        for p in db.all_(c, "SELECT user_id, questions FROM competition_participants WHERE competition_id=? AND status='registered'", cid):
            mine = set(db.jl(p["questions"]) or [])
            snaps = db.all_(c, "SELECT question_id, detail, MAX(created_at) AS at FROM activity_events WHERE competition_id=? AND user_id=? AND type='snapshot' "
                               "AND question_id IS NOT NULL GROUP BY question_id", cid, p["user_id"])
            for sn in snaps:
                d = db.jl(sn["detail"], {})
                code, lang = str(d.get("code") or ""), d.get("language")
                if not code.strip() or (mine and sn["question_id"] not in mine):
                    continue
                cq = db.one(c, "SELECT cq.version_id, qv.languages FROM competition_questions cq JOIN question_versions qv ON qv.id=cq.version_id WHERE cq.competition_id=? AND cq.question_id=?", cid, sn["question_id"])
                if not cq or (lang not in db.jl(cq["languages"]) and lang not in langs):
                    continue
                last = db.one(c, "SELECT MAX(created_at) AS t, MAX(code=?) AS same FROM submissions WHERE competition_id=? AND user_id=? AND question_id=? AND kind='submit'", code, cid, p["user_id"], sn["question_id"])
                if last["same"] or (last["t"] and last["t"] >= sn["at"]):
                    continue   # already judged, or they submitted after this save
                c.execute("INSERT INTO submissions(competition_id,user_id,question_id,version_id,kind,language,code,ip,created_at) VALUES(?,?,?,?,'submit',?,?,'saved-work',?)",
                          (cid, p["user_id"], sn["question_id"], cq["version_id"], lang, code[:65536], sn["at"]))
                queued += 1
        audit(c, u, "competition.grade_saved", "competition", cid, cid, {"queued": queued}, client_ip(req))
    return {"queued": queued, "message": f"{queued} saved answer(s) sent to the judge. Scores update in a minute." if queued else "Nothing new to grade: every saved answer was already submitted."}


class CompChatIn(BaseModel):
    message: str = Field(min_length=2, max_length=1000)


@router.post("/competitions/{cid}/assistant")
def comp_assistant(cid: int, body: CompChatIn, req: Request, u=Depends(admin), c=Depends(conn)):
    """Change a draft round in plain words: '10 questions per student, 6 fix the bug and 4 mcq, total 100 marks, 90 minutes'.
    Missing questions are written by the AI into this competition (checked ones join by themselves)."""
    from . import assistant, pool
    comp = comp_or_404(c, cid, u)
    if comp["status"] != "draft":
        raise HTTPException(409, "Only a draft competition can be changed here. Unpublish it first.")
    p = assistant.parse(body.message, {}, departments(c))
    done = []
    sets = {}
    if p.get("duration_min"):
        sets["duration_min"] = p["duration_min"]; done.append(f"Duration {p['duration_min']} minutes")
    if p.get("start_at"):
        sets["start_at"] = ts(p["start_at"]); done.append(f"Starts {p['start_at'].replace('T', ' ')}")
    if "eligible_years" in p:
        sets["eligible_years"] = json.dumps(p["eligible_years"]); done.append("Years " + (", ".join(map(str, p["eligible_years"])) or "all"))
    specs = p.get("questions") or []
    per = int(p.get("per_student") or 0) or (sum(s["count"] for s in specs) if specs and re.search(r"per student|each student", body.message.lower()) else 0)
    if per:
        sets["draw"] = per; done.append(f"{per} questions per student")
    if specs:
        mix = {(s.get("type") or "standard"): s["count"] for s in specs}
        db.set_setting(c, f"mix:{cid}", json.dumps(mix))
        done.append("Each student gets " + ", ".join(f"{n} {assistant.TYPE_NAME.get(k) or 'coding'}" for k, n in mix.items()))
    if sets:
        c.execute(f"UPDATE competitions SET {','.join(k + '=?' for k in sets)}, updated_at=? WHERE id=?", (*sets.values(), db.now(), cid))
    # enough questions of each kind? pool target = asked per kind x 2 (so papers differ); the AI writes what is missing
    writing = 0
    have = {}
    for r in db.all_(c, "SELECT qv.qtype FROM competition_questions cq JOIN question_versions qv ON qv.id=cq.version_id WHERE cq.competition_id=?", cid):
        have[contest.KIND.get(r["qtype"] or "standard", "coding")] = have.get(contest.KIND.get(r["qtype"] or "standard", "coding"), 0) + 1
    for s in specs:
        k = contest.KIND.get(s.get("type") or "standard", "coding")
        need_n = s["count"] * 2 - have.get(k, 0)
        if need_n > 0 and ai.enabled():
            writing += pool.spawn(c, u["id"], {**s, "count": need_n, "comp_tag": f"for-comp-{cid}", "tests": False if k in ("coding", "debug") else None})
    if writing:
        done.append(f"The AI is writing {writing} more question(s) for this round; checked ones join by themselves")
    elif specs and not ai.enabled():
        done.append("Not enough questions of some kinds and the AI is off: add them from the bank or a file")
    total = int(p.get("total_marks") or 0) or (100 if per or specs else 0)
    if total:
        each = max(1, round(total / max(1, per or db.one(c, "SELECT draw FROM competitions WHERE id=?", cid)["draw"] or
                                            db.one(c, "SELECT COUNT(*) AS n FROM competition_questions WHERE competition_id=?", cid)["n"] or 1)))
        c.execute("UPDATE competition_questions SET marks=? WHERE competition_id=?", (each, cid))
        c.execute("UPDATE competitions SET max_marks=? WHERE id=?", (total, cid))
        done.append(f"Total {total} marks: {each} per question")
    audit(c, u, "competition.assistant", "competition", cid, cid, {"message": body.message[:300]}, client_ip(req))
    return {"done": done or ["I did not find anything to change. Try: '10 questions per student, 6 fix the bug and 4 mcq, total 100 marks'."]}


class ExplainIn(BaseModel):
    on: bool


@router.post("/competitions/{cid}/explain")
def set_explain(cid: int, body: ExplainIn, req: Request, u=Depends(admin), c=Depends(conn)):
    """Whether students see how ranks were decided (the rules in order and a reason on each row)."""
    comp_or_404(c, cid, u)
    db.set_setting(c, f"explain:{cid}", "1" if body.on else "0")
    audit(c, u, "competition.explain", "competition", cid, cid, {"on": body.on}, client_ip(req))
    return {"on": body.on}


@router.get("/practice")
def practice_admin(u=Depends(admin), c=Depends(conn)):
    need(u, "questions")
    rows = db.all_(c, "SELECT q.id, q.practice, q.status, q.difficulty, q.category, qv.title, qv.validated_at, "
                      "(SELECT COUNT(DISTINCT s.user_id) FROM submissions s WHERE s.question_id=q.id AND s.kind='practice') AS tried, "
                      "(SELECT COUNT(DISTINCT s.user_id) FROM submissions s WHERE s.question_id=q.id AND s.kind='practice' AND s.verdict='AC') AS solved, "
                      "(SELECT COUNT(*) FROM submissions s WHERE s.question_id=q.id AND s.kind='practice') AS attempts, "
                      "EXISTS (SELECT 1 FROM competition_questions cq JOIN competitions co ON co.id=cq.competition_id WHERE cq.question_id=q.id "
                      "AND co.status IN ('draft','published','live','paused')) AS reserved "
                      "FROM questions q JOIN question_versions qv ON qv.id=q.latest_version_id WHERE q.status='approved' ORDER BY q.practice DESC, qv.title")
    return rows


class PracticeIn(BaseModel):
    question_id: int
    on: bool


@router.post("/practice")
def practice_toggle(body: PracticeIn, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "questions")
    if not db.one(c, "SELECT 1 AS x FROM questions WHERE id=? AND status='approved'", body.question_id):
        raise HTTPException(400, "Only approved questions can be used for practice.")
    c.execute("UPDATE questions SET practice=?, updated_at=? WHERE id=?", (int(body.on), db.now(), body.question_id))
    audit(c, u, "question.practice_" + ("on" if body.on else "off"), "question", body.question_id, ip=client_ip(req))
    return {"ok": True}


@router.delete("/students/{uid}/avatar")
def remove_avatar(uid: int, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "participants")
    from .api_student import AVATAR_DIR
    for f in AVATAR_DIR.glob(f"{uid}.*"):
        f.unlink(missing_ok=True)
    c.execute("UPDATE users SET avatar=NULL WHERE id=? AND kind='student'", (uid,))
    audit(c, u, "student.avatar_removed", "user", uid, ip=client_ip(req))
    return {"ok": True}


@router.post("/competitions/{cid}/rejudge")
def rejudge_comp(cid: int, req: Request, question_id: int | None = None, only_errors: bool = False, u=Depends(admin), c=Depends(conn)):
    comp = comp_or_404(c, cid, u)
    if comp["status"] in ("finalized", "archived"):
        raise HTTPException(409, "Results are finalized; rejudging would change history.")
    sql, a = "UPDATE submissions SET status='queued', attempts=0, worker=NULL WHERE competition_id=? AND kind='submit' AND status!='running'", [cid]
    if question_id:
        sql += " AND question_id=?"
        a.append(question_id)
    if only_errors:
        sql += " AND (status='error' OR verdict='IE')"
    n = c.execute(sql, a).rowcount
    audit(c, u, "competition.rejudge", "competition", cid, cid, {"question_id": question_id, "count": n}, client_ip(req))
    return {"queued": n}


# ================= questions =================

class TestIn(BaseModel):
    input: str = Field(default="", max_length=2_000_000)
    expected: str = Field(default="", max_length=2_000_000)
    visibility: str = Field(default="hidden", pattern="^(sample|hidden)$")
    kind: str = Field(default="normal", pattern="^(normal|boundary|edge|stress)$")
    weight: int = Field(default=1, ge=0, le=1000)
    explanation: str = Field(default="", max_length=5000)
    description: str = Field(default="", max_length=500)


class QuestionIn(BaseModel):
    title: str = Field(min_length=2, max_length=150)
    slug: str | None = Field(default=None, max_length=160)
    category: str = Field(default="Problem Solving", max_length=60)
    difficulty: str = Field(default="Easy", pattern="^(Easy|Intermediate|Challenging)$")
    tags: list[str] = []
    statement: str = Field(default="", max_length=50000)
    input_format: str = Field(default="", max_length=10000)
    output_format: str = Field(default="", max_length=10000)
    constraints: str = Field(default="", max_length=10000)
    languages: list[str] = list(judge.CODE_LANGS)
    time_limit_ms: int = Field(default=2000, ge=100, le=20000)
    memory_mb: int = Field(default=256, ge=16, le=2048)
    max_marks: int = Field(default=100, ge=1, le=10000)
    practice: bool = False
    references: dict[str, str] = {}
    qtype: str = Field(default="standard", pattern="^(standard|debug|complete|prompt|output|trace|mcq)$")
    starter: dict[str, str] = {}
    options: list[str] = Field(default=[], max_length=6)   # multiple choice: the texts students pick from
    answer: str = Field(default="", max_length=20000)      # written-answer and multiple-choice types: the correct answer
    tests: list[TestIn] | None = None  # only used on create


def slugify(s):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-")[:80] or "question"


def unique_slug(c, base, exclude=None):
    slug, n = base, 2
    while (r := db.one(c, "SELECT id FROM questions WHERE slug=?", slug)) and r["id"] != exclude:
        slug, n = f"{base}-{n}", n + 1
    return slug


def version_locked(c, vid):
    return bool(db.one(c, "SELECT 1 AS x FROM competition_questions cq JOIN competitions co ON co.id=cq.competition_id WHERE cq.version_id=? AND co.status!='draft' LIMIT 1", vid)
                or db.one(c, "SELECT 1 AS x FROM submissions WHERE version_id=? AND kind IN ('submit','practice') LIMIT 1", vid))


def editable_version(c, qid, u):
    """Return (version_id, test_id_map). Versions used by a published/finished competition or by student submissions
    are frozen for history; editing them forks a new version (copying tests and references)."""
    q = db.one(c, "SELECT * FROM questions WHERE id=?", qid)
    if not q:
        raise HTTPException(404, "Question not found.")
    vid = q["latest_version_id"]
    if not version_locked(c, vid):
        return vid, {}
    v = db.one(c, "SELECT * FROM question_versions WHERE id=?", vid)
    nv = {k: v[k] for k in v if k not in ("id", "version_no", "created_by", "created_at")}
    nv.update(version_no=v["version_no"] + 1, created_by=u["id"], created_at=db.now())
    nid = c.execute(f"INSERT INTO question_versions({','.join(nv)}) VALUES({','.join('?' * len(nv))})", tuple(nv.values())).lastrowid
    idmap = {}
    for t in db.all_(c, "SELECT * FROM test_cases WHERE version_id=? ORDER BY ordinal, id", vid):
        old = t.pop("id")
        t["version_id"] = nid
        idmap[old] = c.execute(f"INSERT INTO test_cases({','.join(t)}) VALUES({','.join('?' * len(t))})", tuple(t.values())).lastrowid
    c.execute("INSERT INTO reference_solutions(version_id,language,code) SELECT ?, language, code FROM reference_solutions WHERE version_id=?", (nid, vid))
    c.execute("UPDATE questions SET latest_version_id=?, updated_at=? WHERE id=?", (nid, db.now(), qid))
    c.execute("UPDATE competition_questions SET version_id=? WHERE question_id=? AND competition_id IN (SELECT id FROM competitions WHERE status='draft')", (nid, qid))
    return nid, idmap


def invalidate(c, vid):
    c.execute("UPDATE question_versions SET validated_at=NULL, validation='{}' WHERE id=?", (vid,))


def renumber(c, vid):
    for i, t in enumerate(db.all_(c, "SELECT id FROM test_cases WHERE version_id=? ORDER BY CASE visibility WHEN 'sample' THEN 0 ELSE 1 END, ordinal, id", vid)):
        c.execute("UPDATE test_cases SET ordinal=? WHERE id=?", (i + 1, t["id"]))


def insert_tests(c, vid, tests):
    start = (db.one(c, "SELECT MAX(ordinal) AS m FROM test_cases WHERE version_id=?", vid)["m"] or 0) + 1
    for i, t in enumerate(tests):
        t = t if isinstance(t, dict) else t.model_dump()
        c.execute("INSERT INTO test_cases(version_id,ordinal,input,expected,visibility,kind,weight,explanation,description,validated) VALUES(?,?,?,?,?,?,?,?,?,0)",
                  (vid, start + i, t.get("input", ""), t.get("expected", ""), t.get("visibility", "hidden"), t.get("kind", "normal"),
                   int(t.get("weight", 1)), t.get("explanation", ""), t.get("description", "")))
    renumber(c, vid)


def clean_options(b):
    """Multiple choice: 2 to 6 different options, and the correct answer must be one of them."""
    opts = list(dict.fromkeys(o.strip()[:500] for o in b.options if o.strip()))
    if b.qtype == "mcq" and len(opts) < 2:
        raise HTTPException(400, "A multiple-choice question needs at least two different options.")
    if opts and b.answer.strip() and judge.normalize(b.answer) not in [judge.normalize(o) for o in opts]:
        raise HTTPException(400, "The correct answer must be one of the options.")
    return opts if b.qtype in judge.ANSWER_QTYPES else []


def set_answer(c, vid, answer):
    """Written-answer / multiple-choice question: one hidden test holding the correct text. Nothing to run, so it is valid at once."""
    t = db.now()
    c.execute("DELETE FROM test_cases WHERE version_id=?", (vid,))
    c.execute("INSERT INTO test_cases(version_id,ordinal,input,expected,visibility,validated) VALUES(?,1,'',?,'hidden',1)", (vid, answer))
    c.execute("DELETE FROM reference_solutions WHERE version_id=?", (vid,))
    c.execute("INSERT INTO reference_solutions(version_id,language,code) VALUES(?,'answer',?)", (vid, answer))
    c.execute("UPDATE question_versions SET validated_at=?, validation=? WHERE id=?",
              (t, json.dumps({"answer": {"verdict": "AC", "passed": 1, "total": 1, "at": t, "compile_output": "", "failed": []}}), vid))


def create_question(c, b: QuestionIn, u, source="manual"):
    t = db.now()
    opts = clean_options(b)
    langs = ["prompt"] if b.qtype == "prompt" else ["answer"] if b.qtype in judge.ANSWER_QTYPES else [l for l in b.languages if l in judge.CODE_LANGS] or list(judge.CODE_LANGS)
    qid = c.execute("INSERT INTO questions(slug,category,difficulty,tags,status,source,practice,created_by,created_at,updated_at) VALUES(?,?,?,?, 'draft', ?,?,?,?,?)",
                    (unique_slug(c, slugify(b.slug or b.title)), b.category, b.difficulty, json.dumps([x.strip() for x in b.tags if x.strip()][:12]),
                     source, int(b.practice), u["id"], t, t)).lastrowid
    vid = c.execute("INSERT INTO question_versions(question_id,version_no,title,statement,input_format,output_format,constraints,languages,time_limit_ms,memory_mb,max_marks,validation,qtype,starter,options,created_by,created_at) "
                    "VALUES(?,1,?,?,?,?,?,?,?,?,?,'{}',?,?,?,?,?)",
                    (qid, b.title.strip(), b.statement, b.input_format, b.output_format, b.constraints, json.dumps(langs), b.time_limit_ms, b.memory_mb,
                     b.max_marks, b.qtype, json.dumps({k: v for k, v in b.starter.items() if k in judge.LANGS and v.strip()}), json.dumps(opts), u["id"], t)).lastrowid
    c.execute("UPDATE questions SET latest_version_id=? WHERE id=?", (vid, qid))
    for lang, code in b.references.items():
        if lang in judge.LANGS and code.strip():
            c.execute("INSERT INTO reference_solutions(version_id,language,code) VALUES(?,?,?)", (vid, lang, code))
    if b.tests:
        insert_tests(c, vid, b.tests)
    if b.qtype in judge.ANSWER_QTYPES and b.answer.strip():
        set_answer(c, vid, b.answer)
    return qid


def question_full(c, qid, include_versions=True):
    q = db.one(c, "SELECT q.*, u.name AS creator, ap.name AS approver FROM questions q LEFT JOIN users u ON u.id=q.created_by LEFT JOIN users ap ON ap.id=q.approved_by WHERE q.id=?", qid)
    if not q:
        raise HTTPException(404, "Question not found.")
    v = db.one(c, "SELECT * FROM question_versions WHERE id=?", q["latest_version_id"])
    q["tags"] = db.jl(q["tags"])
    q["version"] = {**v, "languages": db.jl(v["languages"]), "validation": db.jl(v["validation"], {}) or {}, "starter": db.jl(v.get("starter"), {}) or {}, "options": db.jl(v.get("options"))}
    q["tests"] = db.all_(c, "SELECT * FROM test_cases WHERE version_id=? ORDER BY ordinal, id", v["id"])
    q["references"] = {r["language"]: r["code"] for r in db.all_(c, "SELECT language, code FROM reference_solutions WHERE version_id=?", v["id"])}
    q["locked"] = version_locked(c, v["id"])
    if include_versions:
        q["versions"] = db.all_(c, "SELECT id, version_no, title, created_at, validated_at FROM question_versions WHERE question_id=? ORDER BY version_no DESC", qid)
        q["used_in"] = db.all_(c, "SELECT co.id, co.code, co.title, co.status, cq.version_id FROM competition_questions cq JOIN competitions co ON co.id=cq.competition_id WHERE cq.question_id=?", qid)
    q["pending_validation"] = db.all_(c, "SELECT id, language, status FROM submissions WHERE version_id=? AND kind='validate' AND status IN ('queued','running')", v["id"])
    return q


@router.get("/questions/meta")
def q_meta(u=Depends(admin), c=Depends(conn)):
    return {"departments": departments(c), "categories": CATEGORIES, "difficulties": ["Easy", "Intermediate", "Challenging"],
            "languages": {k: {"name": v["name"], "monaco": v["monaco"], "template": judge.TEMPLATES.get(k, "")} for k, v in judge.LANGS.items()},
            "available": judge.available_languages()}


@router.get("/questions")
def list_questions(q: str = "", category: str = "", difficulty: str = "", status: str = "", u=Depends(admin), c=Depends(conn)):
    need(u, "questions")
    sql = ("SELECT qq.id, qq.slug, qq.category, qq.difficulty, qq.tags, qq.status, qq.source, qq.practice, qq.updated_at, qv.title, qv.version_no, qv.validated_at, qv.max_marks, "
           "(SELECT COUNT(*) FROM test_cases t WHERE t.version_id=qv.id) AS tests, (SELECT COUNT(*) FROM test_cases t WHERE t.version_id=qv.id AND t.visibility='hidden') AS hidden, "
           "(SELECT COUNT(*) FROM competition_questions cq WHERE cq.question_id=qq.id) AS used, u.name AS creator "
           "FROM questions qq JOIN question_versions qv ON qv.id=qq.latest_version_id LEFT JOIN users u ON u.id=qq.created_by WHERE 1=1")
    a = []
    if q:
        sql += " AND (qv.title LIKE ? OR qq.tags LIKE ? OR qq.slug LIKE ? OR qv.statement LIKE ?)"
        a += [f"%{q}%"] * 4
    if category:
        sql += " AND qq.category=?"
        a.append(category)
    if difficulty:
        sql += " AND qq.difficulty=?"
        a.append(difficulty)
    if status:
        sql += " AND qq.status=?"
        a.append(status)
    else:
        sql += " AND qq.status!='archived'"
    rows = db.all_(c, sql + " ORDER BY qq.updated_at DESC LIMIT 500", *a)
    for r in rows:
        r["tags"] = db.jl(r["tags"])
    return rows


@router.post("/questions")
def new_question(body: QuestionIn, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "questions")
    if u["role"] == "reviewer":
        raise HTTPException(403, "Reviewers can edit existing questions but not create new ones.")
    with db.tx(c):
        qid = create_question(c, body, u)
        audit(c, u, "question.create", "question", qid, detail={"title": body.title}, ip=client_ip(req))
    return question_full(c, qid)


@router.get("/questions/{qid}")
def get_question(qid: int, u=Depends(admin), c=Depends(conn)):
    need(u, "questions")
    return question_full(c, qid)


@router.get("/questions/{qid}/versions/{vid}")
def get_version(qid: int, vid: int, u=Depends(admin), c=Depends(conn)):
    need(u, "questions")
    v = db.one(c, "SELECT * FROM question_versions WHERE id=? AND question_id=?", vid, qid)
    if not v:
        raise HTTPException(404, "Version not found.")
    v["tests"] = db.all_(c, "SELECT * FROM test_cases WHERE version_id=? ORDER BY ordinal", vid)
    v["references"] = {r["language"]: r["code"] for r in db.all_(c, "SELECT language, code FROM reference_solutions WHERE version_id=?", vid)}
    return v


@router.put("/questions/{qid}")
def edit_question(qid: int, body: QuestionIn, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "questions")
    with db.tx(c):
        vid, _ = editable_version(c, qid, u)
        old = db.one(c, "SELECT * FROM question_versions WHERE id=?", vid)
        langs = ["prompt"] if body.qtype == "prompt" else ["answer"] if body.qtype in judge.ANSWER_QTYPES else [l for l in body.languages if l in judge.CODE_LANGS] or list(judge.CODE_LANGS)
        c.execute("UPDATE question_versions SET title=?, statement=?, input_format=?, output_format=?, constraints=?, languages=?, time_limit_ms=?, memory_mb=?, max_marks=?, qtype=?, starter=?, options=? WHERE id=?",
                  (body.title.strip(), body.statement, body.input_format, body.output_format, body.constraints, json.dumps(langs), body.time_limit_ms,
                   body.memory_mb, body.max_marks, body.qtype, json.dumps({k: v for k, v in body.starter.items() if k in judge.LANGS and v.strip()}), json.dumps(clean_options(body)), vid))
        c.execute("UPDATE questions SET slug=?, category=?, difficulty=?, tags=?, practice=?, updated_at=? WHERE id=?",
                  (unique_slug(c, slugify(body.slug or body.title), qid), body.category, body.difficulty, json.dumps([x.strip() for x in body.tags if x.strip()][:12]),
                   int(body.practice), db.now(), qid))
        old_refs = {r["language"]: r["code"] for r in db.all_(c, "SELECT language, code FROM reference_solutions WHERE version_id=?", vid)}
        new_refs = {k: v for k, v in body.references.items() if k in judge.LANGS and v.strip()}
        if old_refs != new_refs:
            c.execute("DELETE FROM reference_solutions WHERE version_id=?", (vid,))
            for k, v in new_refs.items():
                c.execute("INSERT INTO reference_solutions(version_id,language,code) VALUES(?,?,?)", (vid, k, v))
        if old_refs != new_refs or old["time_limit_ms"] != body.time_limit_ms or old["memory_mb"] != body.memory_mb:
            invalidate(c, vid)
        if body.qtype in judge.ANSWER_QTYPES and body.answer.strip():
            set_answer(c, vid, body.answer)
        audit(c, u, "question.update", "question", qid, detail={"version_id": vid}, ip=client_ip(req))
    return question_full(c, qid)


@router.post("/questions/{qid}/duplicate")
def dup_question(qid: int, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "questions")
    q = question_full(c, qid, False)
    v = q["version"]
    body = QuestionIn(title=f"{v['title']} (Copy)", category=q["category"], difficulty=q["difficulty"], tags=q["tags"], statement=v["statement"],
                      input_format=v["input_format"], output_format=v["output_format"], constraints=v["constraints"], languages=v["languages"],
                      time_limit_ms=v["time_limit_ms"], memory_mb=v["memory_mb"], max_marks=v["max_marks"], references=q["references"], qtype=v.get("qtype") or "standard", starter=v["starter"], options=v["options"],
                      tests=[TestIn(**{k: t[k] for k in ("input", "expected", "visibility", "kind", "weight", "explanation", "description")}) for t in q["tests"]])
    with db.tx(c):
        nid = create_question(c, body, u, "duplicate")
        audit(c, u, "question.duplicate", "question", nid, detail={"from": qid}, ip=client_ip(req))
    return question_full(c, nid)


class QStatusIn(BaseModel):
    status: str = Field(pattern="^(draft|approved|archived)$")


@router.post("/questions/{qid}/status")
def q_status(qid: int, body: QStatusIn, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "questions.approve" if body.status == "approved" else "questions")
    q = question_full(c, qid, False)
    if body.status == "approved":
        v = q["version"]
        missing = [k for k in (("statement",) if v.get("qtype") in judge.ANSWER_QTYPES else ("statement", "input_format", "output_format")) if not v[k].strip()]
        if missing:
            raise HTTPException(400, f"Fill in: {', '.join(missing)} before approving.")
        if not q["tests"]:
            raise HTTPException(400, "Add at least one test case (an example counts) before approving.")
        if not v["validated_at"]:
            raise HTTPException(400, "Run reference-solution validation successfully before approving.")
    c.execute("UPDATE questions SET status=?, approved_by=?, updated_at=? WHERE id=?",
              (body.status, u["id"] if body.status == "approved" else q["approved_by"], db.now(), qid))
    m = re.search(r"\bfor-comp-(\d+)\b", " ".join(q["tags"] or []))
    if body.status == "approved" and m:   # written by the AI for a competition: approving it adds it there (while that round is a draft)
        comp = db.one(c, "SELECT id, status FROM competitions WHERE id=?", int(m.group(1)))
        if comp and comp["status"] == "draft":
            c.execute("INSERT OR IGNORE INTO competition_questions(competition_id,question_id,version_id,ordinal,marks) VALUES(?,?,?,"
                      "(SELECT COALESCE(MAX(ordinal),0)+1 FROM competition_questions WHERE competition_id=?),?)",
                      (comp["id"], qid, q["version"]["id"], comp["id"], 10 if q["version"].get("qtype") in judge.ANSWER_QTYPES else 100))
    audit(c, u, f"question.{body.status}", "question", qid, ip=client_ip(req))
    return question_full(c, qid)


# ----- test cases -----

@router.post("/questions/{qid}/tests")
def add_test(qid: int, body: TestIn, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "tests")
    with db.tx(c):
        vid, _ = editable_version(c, qid, u)
        insert_tests(c, vid, [body])
        invalidate(c, vid)
        audit(c, u, "test.create", "question", qid, ip=client_ip(req))
    return question_full(c, qid)


@router.put("/questions/{qid}/tests/{tid}")
def edit_test(qid: int, tid: int, body: TestIn, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "tests")
    with db.tx(c):
        vid, idmap = editable_version(c, qid, u)
        tid = idmap.get(tid, tid)
        if not db.one(c, "SELECT 1 AS x FROM test_cases WHERE id=? AND version_id=?", tid, vid):
            raise HTTPException(404, "Test case not found in the current version.")
        c.execute("UPDATE test_cases SET input=?, expected=?, visibility=?, kind=?, weight=?, explanation=?, description=?, validated=0 WHERE id=?",
                  (body.input, body.expected, body.visibility, body.kind, body.weight, body.explanation, body.description, tid))
        renumber(c, vid)
        invalidate(c, vid)
        audit(c, u, "test.update", "question", qid, detail={"test_id": tid}, ip=client_ip(req))
    return question_full(c, qid)


@router.delete("/questions/{qid}/tests/{tid}")
def del_test(qid: int, tid: int, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "tests")
    with db.tx(c):
        vid, idmap = editable_version(c, qid, u)
        c.execute("DELETE FROM test_cases WHERE id=? AND version_id=?", (idmap.get(tid, tid), vid))
        renumber(c, vid)
        invalidate(c, vid)
        audit(c, u, "test.delete", "question", qid, detail={"test_id": tid}, ip=client_ip(req))
    return question_full(c, qid)


class BulkTestsIn(BaseModel):
    tests: list[TestIn] = []
    text: str | None = Field(default=None, max_length=5_000_000)  # '### input' / '### output' blocks
    replace: bool = False


def parse_bulk_text(text):
    """Format:  ### input  ...  ### output  ...  (repeat). Optional '### hidden' / '### sample' marker lines."""
    tests, cur, key, vis = [], None, None, "hidden"
    for line in text.replace("\r\n", "\n").split("\n"):
        m = re.match(r"^###\s*(input|output|expected|sample|hidden)\s*$", line.strip(), re.I)
        if m:
            tag = m.group(1).lower()
            if tag in ("sample", "hidden"):
                vis = tag
                continue
            if tag == "input":
                cur = {"input": "", "expected": "", "visibility": vis}
                tests.append(cur)
                key = "input"
            elif cur is not None:
                key = "expected"
            continue
        if cur is not None and key:
            cur[key] += line + "\n"
    for t in tests:
        t["input"] = t["input"].rstrip("\n") + "\n" if t["input"].strip() else ""
        t["expected"] = t["expected"].rstrip("\n") + "\n" if t["expected"].strip() else ""
    return tests


@router.post("/questions/{qid}/tests/bulk")
def bulk_tests(qid: int, body: BulkTestsIn, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "tests")
    tests = [t.model_dump() for t in body.tests] + (parse_bulk_text(body.text) if body.text else [])
    if not tests:
        raise HTTPException(400, "No test cases found. Use '### input' and '### output' markers.")
    with db.tx(c):
        vid, _ = editable_version(c, qid, u)
        if body.replace:
            c.execute("DELETE FROM test_cases WHERE version_id=? AND visibility='hidden'", (vid,))
        insert_tests(c, vid, tests)
        invalidate(c, vid)
        audit(c, u, "test.bulk_import", "question", qid, detail={"count": len(tests), "replace": body.replace}, ip=client_ip(req))
    return question_full(c, qid)


class ValidateIn(BaseModel):
    fill_expected: bool = True


def queue_validation(c, qid, u, fill=True):
    q = db.one(c, "SELECT latest_version_id FROM questions WHERE id=?", qid)
    vid = q["latest_version_id"]
    refs = db.all_(c, "SELECT language, code FROM reference_solutions WHERE version_id=? ORDER BY CASE language WHEN 'c' THEN 0 WHEN 'python' THEN 1 ELSE 2 END", vid)
    if not refs:
        raise HTTPException(400, "Add at least one reference solution first.")
    if not db.one(c, "SELECT 1 AS x FROM test_cases WHERE version_id=? LIMIT 1", vid):
        raise HTTPException(400, "Add test cases first.")
    empty = db.one(c, "SELECT COUNT(*) AS n FROM test_cases WHERE version_id=? AND TRIM(expected)=''", vid)["n"]
    ids = []
    # If outputs need filling, the first reference fills them and the worker then queues the others to cross-check.
    for i, r in enumerate(refs[:1] if (fill and empty) else refs):
        ids.append(c.execute("INSERT INTO submissions(competition_id,user_id,question_id,version_id,kind,language,code,fill_expected,created_at) VALUES(NULL,?,?,?,'validate',?,?,?,?)",
                             (u["id"], qid, vid, r["language"], r["code"], int(fill and bool(empty)), db.now())).lastrowid)
    return ids


@router.post("/questions/{qid}/validate")
def validate(qid: int, body: ValidateIn, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "tests")
    with db.tx(c):
        vid, _ = editable_version(c, qid, u) if body.fill_expected else (None, {})
        ids = queue_validation(c, qid, u, body.fill_expected)
        audit(c, u, "question.validate", "question", qid, detail={"jobs": ids}, ip=client_ip(req))
    return {"jobs": ids}


class GenIn(BaseModel):
    code: str = Field(min_length=5, max_length=65536)
    count: int = Field(default=10, ge=1, le=50)


@router.post("/questions/{qid}/generate")
def generate_tests(qid: int, body: GenIn, req: Request, u=Depends(admin), c=Depends(conn)):
    """Platform-generated tests: run a Python generator in the sandbox once per seed (seed arrives on stdin)."""
    need(u, "tests")
    q = db.one(c, "SELECT latest_version_id FROM questions WHERE id=?", qid)
    if not q:
        raise HTTPException(404, "Question not found.")
    ids = [c.execute("INSERT INTO submissions(competition_id,user_id,question_id,version_id,kind,language,code,custom_input,created_at) VALUES(NULL,?,?,?,'run','python',?,?,?)",
                     (u["id"], qid, q["latest_version_id"], body.code, f"{i}\n", db.now())).lastrowid for i in range(1, body.count + 1)]
    audit(c, u, "test.generate", "question", qid, detail={"count": body.count}, ip=client_ip(req))
    return {"jobs": ids}


class CollectIn(BaseModel):
    jobs: list[int] = Field(max_length=50)
    kind: str = Field(default="normal", pattern="^(normal|boundary|edge|stress)$")


@router.post("/questions/{qid}/generate/collect")
def collect_generated(qid: int, body: CollectIn, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "tests")
    rows = [db.one(c, "SELECT s.id, s.status, s.verdict, s.compile_output, s.custom_input, jr.output, jr.stderr FROM submissions s LEFT JOIN judging_results jr ON jr.submission_id=s.id "
                      "WHERE s.id=? AND s.user_id=? AND s.question_id=? AND s.kind='run'", j, u["id"], qid) for j in body.jobs]
    if any(r is None for r in rows):
        raise HTTPException(404, "Unknown generator job.")
    if any(r["status"] in ("queued", "running") for r in rows):
        raise HTTPException(409, "Generator still running.")
    good = [r for r in rows if r["verdict"] in ("OK", "AC") and (r["output"] or "").strip()]
    failed = [{"seed": (r["custom_input"] or "").strip(), "error": (r["stderr"] or r["compile_output"] or r["verdict"] or "")[-300:]} for r in rows if r not in good]
    seen, tests = set(), []
    for r in good:
        if r["output"] not in seen:
            seen.add(r["output"])
            tests.append({"input": r["output"] if r["output"].endswith("\n") else r["output"] + "\n", "expected": "", "visibility": "hidden",
                          "kind": body.kind, "description": f"generated (seed {(r['custom_input'] or '').strip()})"})
    jobs = []
    if tests:
        with db.tx(c):
            vid, _ = editable_version(c, qid, u)
            insert_tests(c, vid, tests)
            invalidate(c, vid)
            if db.one(c, "SELECT 1 AS x FROM reference_solutions WHERE version_id=?", vid):
                jobs = queue_validation(c, qid, u, True)
            audit(c, u, "test.generate_collect", "question", qid, detail={"added": len(tests), "failed": len(failed)}, ip=client_ip(req))
    return {"added": len(tests), "duplicates": len(good) - len(tests), "failed": failed, "validation_jobs": jobs}


@router.get("/validation/{sid}")
def validation_result(sid: int, u=Depends(admin), c=Depends(conn)):
    need(u, "tests")
    s = db.one(c, "SELECT id, language, status, verdict, passed, total, compile_output, time_ms, memory_kb FROM submissions WHERE id=? AND kind='validate'", sid)
    if not s:
        raise HTTPException(404, "Not found.")
    s["results"] = db.all_(c, "SELECT ordinal, visibility, verdict, time_ms, memory_kb, substr(output,1,2000) AS output, stderr FROM judging_results WHERE submission_id=? ORDER BY ordinal", sid)
    return s


class TryIn(BaseModel):
    language: str
    code: str = Field(max_length=65536)


@router.post("/questions/{qid}/try")
def try_solution(qid: int, body: TryIn, u=Depends(admin), c=Depends(conn)):
    """Run any code against all tests of the latest version without changing validation (preview as a student would be judged)."""
    need(u, "questions")
    if body.language not in judge.LANGS:
        raise HTTPException(400, "Unsupported language.")
    q = db.one(c, "SELECT latest_version_id FROM questions WHERE id=?", qid)
    if not q:
        raise HTTPException(404, "Question not found.")
    sid = c.execute("INSERT INTO submissions(competition_id,user_id,question_id,version_id,kind,language,code,created_at) VALUES(NULL,?,?,?,'run',?,?,?)",
                    (u["id"], qid, q["latest_version_id"], body.language, body.code, db.now())).lastrowid
    return {"id": sid}


@router.get("/runs/{sid}")
def run_result(sid: int, u=Depends(admin), c=Depends(conn)):
    s = db.one(c, "SELECT id, language, status, verdict, passed, total, compile_output, run_output, time_ms, memory_kb FROM submissions WHERE id=? AND user_id=?", sid, u["id"])
    if not s:
        raise HTTPException(404, "Not found.")
    s["results"] = db.all_(c, "SELECT ordinal, visibility, verdict, time_ms, memory_kb, substr(output,1,2000) AS output, stderr FROM judging_results WHERE submission_id=? ORDER BY ordinal", sid)
    return s


# ================= PDF import + drafts =================

@router.post("/pdf")
async def upload_pdf(req: Request, file: UploadFile = File(...), u=Depends(admin), c=Depends(conn)):
    """Upload a question document: PDF, .docx, .txt/.md or an image."""
    need(u, "pdf")
    data = await file.read(config.MAX_UPLOAD_MB * 1024 * 1024 + 1)
    try:
        kind = pdfimport.validate_upload(data, file.filename or "")
    except ValueError as e:
        raise HTTPException(400, str(e))
    sha = hashlib.sha256(data).hexdigest()
    ext = Path(file.filename).suffix.lower()
    stored = f"{sha[:16]}-{secrets.token_hex(4)}{ext}"
    (config.UPLOAD_DIR / stored).write_bytes(data)
    (config.UPLOAD_DIR / stored).chmod(0o600)
    name = re.sub(r"[^\w.\- ]", "_", file.filename or "upload")[:120]
    did = c.execute("INSERT INTO uploaded_documents(filename,stored_name,sha256,size,uploaded_by,created_at) VALUES(?,?,?,?,?,?)",
                    (name, stored, sha, len(data), u["id"], db.now())).lastrowid
    audit(c, u, "document.upload", "document", did, detail={"filename": name, "size": len(data), "kind": kind}, ip=client_ip(req))
    threading.Thread(target=pdfimport.process, args=(did,), daemon=True).start()
    return {"id": did}


@router.get("/pdf")
def list_pdfs(u=Depends(admin), c=Depends(conn)):
    need(u, "pdf")
    return db.all_(c, "SELECT d.id, d.filename, d.size, d.pages, d.status, d.created_at, u.name AS uploader, "
                      "(SELECT COUNT(*) FROM generated_question_drafts g WHERE g.document_id=d.id) AS drafts FROM uploaded_documents d LEFT JOIN users u ON u.id=d.uploaded_by ORDER BY d.id DESC")


@router.get("/pdf/{did}")
def get_pdf(did: int, u=Depends(admin), c=Depends(conn)):
    need(u, "pdf")
    d = db.one(c, "SELECT * FROM uploaded_documents WHERE id=?", did)
    if not d:
        raise HTTPException(404, "Document not found.")
    d["warnings"] = db.jl(d["warnings"])
    d["kind"] = pdfimport.KINDS.get(Path(d["stored_name"]).suffix.lower(), "pdf")
    d["drafts"] = drafts_for(c, "document_id=?", did)
    return d


@router.get("/pdf/{did}/file")
def pdf_file(did: int, u=Depends(admin), c=Depends(conn)):
    need(u, "pdf")
    d = db.one(c, "SELECT * FROM uploaded_documents WHERE id=?", did)
    if not d:
        raise HTTPException(404, "Document not found.")
    kind = pdfimport.KINDS.get(Path(d["stored_name"]).suffix.lower(), "pdf")
    ext = Path(d["stored_name"]).suffix.lower().lstrip(".")
    media = {"image": "image/" + {"jpg": "jpeg", "tif": "tiff"}.get(ext, ext)}.get(kind) or pdfimport.MEDIA.get(kind, "application/octet-stream")
    inline = kind in ("pdf", "image") or media.startswith("text/")
    return FileResponse(config.UPLOAD_DIR / d["stored_name"], media_type=media,
                        headers={"Content-Disposition": "inline" if inline else f'attachment; filename="{d["filename"]}"', "Cache-Control": "no-store"})


def drafts_for(c, where, *a):
    rows = db.all_(c, f"SELECT g.*, u.name AS creator FROM generated_question_drafts g LEFT JOIN users u ON u.id=g.created_by WHERE {where} ORDER BY g.id", *a)
    for r in rows:
        r["payload"] = db.jl(r["payload"], {})
        r["warnings"] = db.jl(r["warnings"])
    return rows


@router.get("/drafts")
def list_drafts(source: str = "", status: str = "", u=Depends(admin), c=Depends(conn)):
    need(u, "questions")
    where, a = "1=1", []
    if source:
        where += " AND g.source=?"
        a.append(source)
    if status:
        where += " AND g.status=?"
        a.append(status)
    return list(reversed(drafts_for(c, where, *a)))[:200]


class DraftIn(BaseModel):
    payload: dict


@router.put("/drafts/{gid}")
def edit_draft(gid: int, body: DraftIn, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "questions")
    g = db.one(c, "SELECT * FROM generated_question_drafts WHERE id=?", gid)
    if not g or g["status"] not in ("pending", "failed"):
        raise HTTPException(409, "Only pending drafts can be edited.")
    c.execute("UPDATE generated_question_drafts SET payload=?, status='pending' WHERE id=?", (json.dumps(body.payload)[:2_000_000], gid))
    return drafts_for(c, "g.id=?", gid)[0]


@router.post("/drafts/{gid}/approve")
def approve_draft(gid: int, req: Request, u=Depends(admin), c=Depends(conn)):
    """Saves the draft into the question bank as an unapproved question; validation is queued automatically."""
    need(u, "questions.approve")
    g = db.one(c, "SELECT * FROM generated_question_drafts WHERE id=?", gid)
    if not g or g["status"] != "pending":
        raise HTTPException(409, "Only pending drafts can be saved to the bank.")
    if g["question_id"]:
        raise HTTPException(409, "This draft is already in the question bank. Open the question to review and approve it.")
    p = db.jl(g["payload"], {})
    tests = [{"input": s.get("input", ""), "expected": s.get("output", ""), "visibility": "sample", "explanation": s.get("explanation", "")} for s in p.get("samples", [])]
    if g["source"] == "ai":  # never trust model-written outputs for samples either: recomputed by the reference
        tests = [{**t, "expected": "", "description": "AI sample: output recomputed by reference"} for t in tests]
    tests += [{"input": t.get("input", ""), "expected": t.get("expected", ""), "visibility": "hidden", "kind": t.get("kind", "normal"),
               "description": t.get("description", "")} for t in p.get("tests", [])]
    body = QuestionIn(title=(p.get("title") or "Untitled")[:150], category=p.get("category") or "Problem Solving",
                      difficulty=p.get("difficulty") if p.get("difficulty") in ("Easy", "Intermediate", "Challenging") else "Easy",
                      tags=p.get("tags") or [], statement=p.get("statement", ""), input_format=p.get("input_format", ""), output_format=p.get("output_format", ""),
                      constraints=p.get("constraints", ""), references={k: v for k, v in (p.get("reference") or {}).items() if k in judge.LANGS and v},
                      qtype=p.get("qtype") if p.get("qtype") in ("standard", "debug", "complete", "prompt", "output", "trace", "mcq") else "standard",
                      options=[str(o) for o in (p.get("options") or [])][:6], answer=str(p.get("answer") or ""),
                      starter={k: str(v) for k, v in (p.get("starter") or {}).items() if k in judge.LANGS and v},
                      tests=[TestIn(**{k: v for k, v in t.items() if k in TestIn.model_fields}) for t in tests])
    with db.tx(c):
        qid = create_question(c, body, u, g["source"])
        c.execute("UPDATE generated_question_drafts SET status='approved', question_id=?, reviewed_by=? WHERE id=?", (qid, u["id"], gid))
        jobs = []
        if body.references and body.tests:
            jobs = queue_validation(c, qid, u, True)
        audit(c, u, "draft.approve", "draft", gid, detail={"question_id": qid, "validation_jobs": jobs}, ip=client_ip(req))
    return {"question_id": qid, "validation_jobs": jobs}


@router.post("/drafts/{gid}/reject")
def reject_draft(gid: int, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "questions")
    c.execute("UPDATE generated_question_drafts SET status='rejected', reviewed_by=? WHERE id=? AND status IN ('pending','failed','generating')", (u["id"], gid))
    audit(c, u, "draft.reject", "draft", gid, ip=client_ip(req))
    return {"ok": True}


# ================= AI assistant =================

@router.get("/ai/status")
def ai_status(u=Depends(admin), c=Depends(conn)):
    need(u, "ai")
    return {**ai.status(), "autogrow": db.setting(c, "practice_autogrow", "true") == "true"}


class AISetIn(BaseModel):
    model: str | None = Field(default=None, max_length=120)
    autogrow: bool | None = None


@router.put("/ai/settings")
def ai_settings(body: AISetIn, req: Request, u=Depends(admin), c=Depends(conn)):
    """Pick which installed model to use, and switch practice auto-grow on or off. Takes effect within 20 seconds, no restart."""
    need(u, "ai")
    if body.model is not None:
        if body.model and body.model not in ai.status()["models"]:
            raise HTTPException(400, "That model is not installed on this computer.")
        db.set_setting(c, "ai_model", body.model)
    if body.autogrow is not None:
        db.set_setting(c, "practice_autogrow", "true" if body.autogrow else "false")
    audit(c, u, "ai.settings", detail=body.model_dump(exclude_none=True), ip=client_ip(req))
    return {**ai.status(), "autogrow": db.setting(c, "practice_autogrow", "true") == "true"}


REF_DIR = config.DATA_DIR / "ai-refs"


@router.post("/ai/reference")
async def ai_reference(req: Request, file: UploadFile | None = File(None), files: list[UploadFile] = File(default=[]), u=Depends(admin), c=Depends(conn)):
    """One or several documents (notes, syllabus, question papers): PDF, Word, PowerPoint, text, images...
    The text is extracted once and kept privately; each file's questions are found separately (numbering restarts per file)."""
    need(u, "ai")
    ups = ([file] if file else []) + list(files or [])
    if not ups:
        raise HTTPException(400, "Choose at least one file.")
    if len(ups) > 20:
        raise HTTPException(400, "Attach at most 20 files at once.")
    REF_DIR.mkdir(parents=True, exist_ok=True)
    tok, parts, warnings, n, names = secrets.token_hex(8), [], [], 0, []
    for up in ups:
        data = await up.read(config.MAX_UPLOAD_MB * 1024 * 1024 + 1)
        try:
            kind = pdfimport.validate_upload(data, up.filename or "")
        except ValueError as e:
            raise HTTPException(400, f"{up.filename}: {e}")
        if kind in ("zip", "xlsx"):
            raise HTTPException(400, f"{up.filename}: attach documents (PDF, Word, PowerPoint, text or images), not a ZIP or a spreadsheet.")
        src = REF_DIR / f"{tok}-{len(parts)}{Path(up.filename or 'x.txt').suffix.lower()}"
        src.write_bytes(data)
        try:
            pages, w, k = await run_in_threadpool(pdfimport.extract_any, src, kind)
        except Exception as e:
            raise HTTPException(400, f"Could not read {up.filename}: {str(e)[:200]}")
        finally:
            src.unlink(missing_ok=True)
        t = re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", "\n".join(pages))).strip()
        if len(t) >= 30:
            parts.append(f"{pdfimport.FILE_MARK} {up.filename} =====\n{t}" if len(ups) > 1 else t)
            names.append(up.filename or "document")
        warnings += [f"{up.filename}: {x}" for x in w[:2]] if len(ups) > 1 else w
        n += k
    text = "\n\n".join(parts)
    if len(text) < 30:
        raise HTTPException(400, "No readable text was found in " + ("those documents. " if len(ups) > 1 else "that document. ") + " ".join(warnings[:2]))
    (REF_DIR / f"{tok}.txt").write_text(text[:400_000])
    file = type("F", (), {"filename": names[0] if len(names) == 1 else f"{len(names)} files"})()
    found = pdfimport.find_questions(text)
    audit(c, u, "ai.reference", detail={"file": (file.filename or "")[:120], "chars": len(text), "questions": len(found)}, ip=client_ip(req))
    return {"ref": tok, "name": (file.filename or "document")[:120], "chars": len(text), "pages": n, "preview": text[:400], "warnings": warnings[:3],
            "found": found_summary(found)}


def found_summary(found):
    return {"total": len(found) if len(found) > 1 or (found and found[0]["kind"] == "mcq") else 0,
            "mcq_ready": sum(1 for q in found if q["kind"] == "mcq" and q["answer"]),
            "mcq_no_answer": sum(1 for q in found if q["kind"] == "mcq" and not q["answer"]),
            "other": sum(1 for q in found if q["kind"] == "other") if len(found) > 1 else 0,
            "sample": [(q.get("statement") or q["raw"])[:90] for q in found[:3]]}


class SimilarIn(BaseModel):
    count: int = Field(default=10, ge=1, le=40)
    difficulty: str = Field(default="", pattern="^(|Easy|Intermediate|Challenging)$")
    competition_id: int | None = None


@router.post("/ai/reference/{tok}/similar")
def ref_similar(tok: str, body: SimilarIn, req: Request, u=Depends(admin), c=Depends(conn)):
    """The AI writes NEW questions like the ones in the attached paper: same kind, topics and level, different content.
    They wait for approval in AI Studio; approving one that was written for a competition adds it to that competition."""
    need(u, "ai")
    need_ai()
    from . import pool
    text = ref_text(tok)
    found = pdfimport.find_questions(text)
    s = found_summary(found)
    if body.competition_id:
        comp_or_404(c, body.competition_id, u)
    m = {"count": body.count, "type": "mcq" if s["mcq_ready"] + s["mcq_no_answer"] >= s["other"] else None,
         "difficulty": body.difficulty or None, "topic": "new questions like the ones in the organiser's paper (same kind, topics and level, different wording and numbers)",
         "tests": False, **({"comp_tag": f"for-comp-{body.competition_id}"} if body.competition_id else {})}
    started = pool.spawn(c, u["id"], m, text)
    audit(c, u, "ai.similar", detail={"count": body.count, "competition_id": body.competition_id}, ip=client_ip(req))
    return {"started": started, "kind": "multiple choice" if m["type"] == "mcq" else "coding"}


class RefImportIn(BaseModel):
    difficulty: str = Field(default="Easy", pattern="^(Easy|Intermediate|Challenging)$")
    category: str = Field(default="", max_length=60)
    competition_id: int | None = None
    per_student: int = Field(default=0, ge=0, le=200)
    marks: int = Field(default=10, ge=1, le=1000)


@router.post("/ai/reference/{tok}/import")
def ref_import(tok: str, body: RefImportIn, req: Request, u=Depends(admin), c=Depends(conn)):
    """Every question in the attached file, taken as it is. Multiple choice with a marked answer is ready at once;
    multiple choice without an answer and other questions are saved as drafts to finish in the Question Bank."""
    need(u, "questions")
    comp = None
    if body.competition_id:
        comp = comp_or_404(c, body.competition_id, u)
    return import_file(c, u, tok, comp, body.difficulty, body.category, body.per_student, body.marks, req)


def import_file(c, u, tok, comp, difficulty="Easy", category="", per_student=0, marks=10, req=None):
    text = ref_text(tok)
    found = pdfimport.find_questions(text)
    if found_summary(found)["total"] == 0:
        raise HTTPException(400, "No separate questions were found in this file. Number them 1, 2, 3 ... (or Q1, Q2 ...), or use it as a reference for the AI instead.")
    if comp and comp["status"] != "draft":
        raise HTTPException(409, "Questions can only be added while the competition is a draft. Unpublish it first.")
    cat = (category or "").strip() or "From file"
    ready, drafts = [], []
    with db.tx(c):
        for i, q in enumerate(found, 1):
            if q["kind"] == "mcq":
                title = re.sub(r"\s+", " ", q["statement"])[:70].rstrip(" ?.:") or f"Question {i}"
                qid = create_question(c, QuestionIn(title=title, category=cat, difficulty=difficulty, tags=["from-file"], statement=q["statement"][:20000],
                                                    qtype="mcq", options=q["options"], answer=q["answer"] or ""), u, "pdf")
                if q["answer"]:
                    c.execute("UPDATE questions SET status='approved' WHERE id=?", (qid,))
                    ready.append(qid)
                else:
                    drafts.append(qid)
            else:
                first = q["raw"].strip().split("\n")[0]
                title = re.sub(r"^\s*(?:(?:question|problem|q)\s*[-#:.]?\s*)?\d{1,3}\s*[.):\]]\s*", "", first, flags=re.I)[:70] or f"Question {i}"
                drafts.append(create_question(c, QuestionIn(title=title, category=cat, difficulty=difficulty, tags=["from-file", "to-finish"],
                                                            statement=q["raw"][:20000]), u, "pdf"))
        if comp and ready:
            start = db.one(c, "SELECT COALESCE(MAX(ordinal),0) AS n FROM competition_questions WHERE competition_id=?", comp["id"])["n"]
            for n, qid in enumerate(ready, 1):
                c.execute("INSERT OR IGNORE INTO competition_questions(competition_id,question_id,version_id,ordinal,marks) "
                          "VALUES(?,?,(SELECT latest_version_id FROM questions WHERE id=?),?,?)", (comp["id"], qid, qid, start + n, marks))
            total = db.one(c, "SELECT COUNT(*) AS n FROM competition_questions WHERE competition_id=?", comp["id"])["n"]
            per = per_student or 0   # kept even while the list is still smaller: questions added later then make different papers
            c.execute("UPDATE competitions SET draw=?, updated_at=? WHERE id=?", (per, db.now(), comp["id"]))
        audit(c, u, "questions.from_file", detail={"ready": len(ready), "drafts": len(drafts), "competition_id": comp["id"] if comp else None},
              ip=client_ip(req) if req else None)
    return {"ready": len(ready), "drafts": len(drafts), "draft_ids": drafts[:200], "competition_id": comp["id"] if comp else None,
            "in_competition": len(ready) if comp else 0}


def ref_text(tok):
    if not tok:
        return ""
    if not re.fullmatch(r"[0-9a-f]{16}", tok):
        raise HTTPException(400, "Unknown reference document.")
    f = REF_DIR / f"{tok}.txt"
    if not f.exists():
        raise HTTPException(400, "The reference document is no longer available. Attach it again.")
    return f.read_text()


class AIPromptIn(BaseModel):
    text: str = Field(min_length=3, max_length=1000)
    ref: str = Field(default="", max_length=32)        # from /ai/reference
    level: str = Field(default="", pattern="^(|Easy|Intermediate|Challenging)$")   # applied to every part that names no level
    tests: bool | None = None   # coding questions: hidden test cases (True) or simple input/output examples only (False); None = ask


def tests_intent(text):
    """What the organiser said about test cases, if anything: True, False or None (not said)."""
    t = text.lower()
    if re.search(r"\b(?:no|without|skip)\s+(?:hidden\s+)?test ?cases?|simple (?:input|i/?o|examples?)|just (?:the )?(?:examples?|samples?|input)|only (?:examples?|samples?)", t):
        return False
    if re.search(r"(?:with|add|include|hidden|write|generate)\s+(?:hidden\s+)?test ?cases?|test ?cases? (?:too|also|included)|auto[- ]?(?:check|judge|grade)", t):
        return True
    return None


@router.post("/ai/prompt")
def ai_prompt(body: AIPromptIn, req: Request, u=Depends(admin), c=Depends(conn)):
    """One box: "10 easy mcq on pointers and 2 medium coding on arrays" -> the right generation jobs."""
    need(u, "ai")
    need_ai()
    from . import assistant, pool
    specs = assistant.parse(body.text, {}).get("questions") or []
    if not specs:   # no count given: one set of 5 on whatever they wrote
        typ = next((k for k, rx in assistant.TYPES if re.search(rx, body.text.lower())), "mcq")
        diff = next((k for k, rx in assistant.DIFFS if re.search(rx, body.text.lower())), None)
        specs = [{"count": 5 if typ in ("mcq", "output", "trace") else 1, "type": typ, "difficulty": diff, "topic": body.text[:80], "auto": True}]
    ref = ref_text(body.ref)
    if ref and len(specs) == 1 and specs[0].get("auto") and specs[0]["type"] in ("mcq", "output", "trace", None):
        # a document and no number: as many as the document holds (its own questions, or about one per 600 characters)
        found = found_summary(pdfimport.find_questions(ref))["total"]
        specs[0]["count"] = max(5, min(40, found or len(ref) // 600))
    # "8 questions from the notes" names no kind: with a document that means a quiz on it
    specs = [{**m, "difficulty": m.get("difficulty") or body.level or None, "type": m.get("type") or ("mcq" if ref else None)} for m in specs]
    tests = body.tests if body.tests is not None else tests_intent(body.text)
    if tests is None and any(m["type"] in (None, "standard", "debug") for m in specs):   # coding asked, test cases not mentioned: ask first
        return {"ask": {"q": "For the coding questions, how should answers be checked?",
                        "options": [{"label": "With hidden test cases (auto-checked, edge cases too)", "tests": True},
                                    {"label": "Simple input/output only (judged on the examples students see)", "tests": False}]}, "plan": []}
    specs = [{**m, "tests": tests} for m in specs]
    plan = [{**m, "started": pool.spawn(c, u["id"], m, ref)} for m in specs]
    audit(c, u, "ai.prompt", detail={"text": body.text[:300], "plan": plan, "reference": bool(ref)}, ip=client_ip(req))
    return {"plan": [{"count": m["count"], "type": assistant.TYPE_NAME.get(m["type"]) or "coding", "difficulty": m["difficulty"] or "Easy",
                      "topic": m["topic"], "started": m["started"]} for m in plan]}


class AIMcqIn(BaseModel):
    topic: str = Field(min_length=2, max_length=200)
    difficulty: str = Field(default="Easy", pattern="^(Easy|Intermediate|Challenging)$")
    style: str = Field(default="output", pattern="^(output|concept)$")
    count: int = Field(default=5, ge=1, le=10)


@router.post("/ai/mcq")
def ai_mcq(body: AIMcqIn, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "ai")
    need_ai()
    gid = c.execute("INSERT INTO generated_question_drafts(source,status,payload,created_by,created_at) VALUES('ai','generating',?,?,?)",
                    (json.dumps({"request": body.model_dump(), "title": f"{body.count} multiple-choice: {body.topic}"}), u["id"], db.now())).lastrowid
    audit(c, u, "ai.mcq", "draft", gid, detail=body.model_dump(), ip=client_ip(req))
    threading.Thread(target=ai.run_mcq, args=(gid, body.model_dump(), u["id"]), daemon=True).start()
    return {"draft_id": gid}


class AIGenIn(BaseModel):
    topic: str = Field(min_length=2, max_length=200)
    difficulty: str = Field(default="Easy", pattern="^(Easy|Intermediate|Challenging)$")
    language: str = Field(default="C and Python", max_length=40)
    style: str = Field(default="", max_length=300)
    constraints: str = Field(default="", max_length=500)
    samples: int = Field(default=2, ge=1, le=5)


def need_ai():
    if not ai.enabled():
        raise HTTPException(503, ai.status(False).get("reason") or "No AI model is available. Questions can still be created by hand.")


@router.post("/ai/generate")
def ai_generate(body: AIGenIn, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "ai")
    need_ai()
    gid = c.execute("INSERT INTO generated_question_drafts(source,status,payload,created_by,created_at) VALUES('ai','generating',?,?,?)",
                    (json.dumps({"request": body.model_dump()}), u["id"], db.now())).lastrowid
    audit(c, u, "ai.generate", "draft", gid, detail=body.model_dump(), ip=client_ip(req))
    threading.Thread(target=ai.run_draft, args=(gid, "generate", body.model_dump(), u["id"]), daemon=True).start()
    return {"draft_id": gid}


class AIVarIn(BaseModel):
    question_id: int


@router.post("/ai/variation")
def ai_variation(body: AIVarIn, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "ai")
    need_ai()
    q = question_full(c, body.question_id, False)
    params = {k: q["version"][k] for k in ("title", "statement", "input_format", "output_format", "constraints")}
    gid = c.execute("INSERT INTO generated_question_drafts(source,status,payload,created_by,created_at) VALUES('ai','generating',?,?,?)",
                    (json.dumps({"request": {"variation_of": body.question_id}}), u["id"], db.now())).lastrowid
    audit(c, u, "ai.variation", "draft", gid, detail={"question_id": body.question_id}, ip=client_ip(req))
    threading.Thread(target=ai.run_draft, args=(gid, "variation", params, u["id"]), daemon=True).start()
    return {"draft_id": gid}


class AITestsIn(BaseModel):
    question_id: int
    kinds: list[str] = ["normal", "boundary", "edge"]
    count: int = Field(default=6, ge=1, le=20)


@router.post("/ai/tests")
def ai_tests(body: AITestsIn, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "ai")
    need_ai()
    q = question_full(c, body.question_id, False)
    v = q["version"]
    sample = next((t["input"] for t in q["tests"] if t["visibility"] == "sample"), "")
    try:
        tests = ai.suggest_tests({**{k: v[k] for k in ("title", "statement", "input_format", "constraints")}, "sample_input": sample}, body.kinds, body.count)
    except Exception as e:
        raise HTTPException(502, f"AI request failed: {e}")
    if not tests:
        raise HTTPException(502, "The AI returned no usable test inputs. Try again.")
    with db.tx(c):
        vid, _ = editable_version(c, body.question_id, u)
        insert_tests(c, vid, [{**t, "expected": "", "visibility": "hidden", "description": ("AI: " + t["description"])[:500]} for t in tests])
        invalidate(c, vid)
        jobs = queue_validation(c, body.question_id, u, True) if db.one(c, "SELECT 1 AS x FROM reference_solutions WHERE version_id=?", vid) else []
        audit(c, u, "ai.tests", "question", body.question_id, detail={"count": len(tests), "validation_jobs": jobs}, ip=client_ip(req))
    return {"added": len(tests), "validation_jobs": jobs}


# ================= participants (students) =================

class StudentIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    usn: str = Field(min_length=3, max_length=20)
    department: str = Field(default="", max_length=40)
    year: int = Field(default=1, ge=1, le=5)
    semester: int | None = Field(default=None, ge=1, le=10)
    section: str = Field(default="", max_length=10)
    email: str = Field(default="", max_length=120)
    dob: str | None = Field(default=None, max_length=10)
    password: str | None = Field(default=None, max_length=200)


def add_student(c, b: StudentIn, u):
    usn = re.sub(r"\s", "", b.usn).upper()
    if not re.fullmatch(r"[A-Z0-9]{3,20}", usn):
        raise HTTPException(400, f"Invalid USN: {b.usn}")
    if db.one(c, "SELECT 1 AS x FROM students WHERE usn=?", usn) or db.one(c, "SELECT 1 AS x FROM users WHERE username=?", usn):
        raise HTTPException(409, f"USN {usn} already exists.")
    dob = check_dob(b.dob) if b.dob else None
    pw = b.password or (dob_password(dob) if dob else temp_password())
    uid = c.execute("INSERT INTO users(username,email,name,password_hash,kind,must_change_pw,created_by,created_at) VALUES(?,?,?,?,'student',?,?,?)",
                    (usn, b.email.strip() or None, " ".join(b.name.split()), hash_password(pw), 0 if (b.password or dob) else 1, u["id"], db.now())).lastrowid
    pid = new_participant_id(c)
    c.execute("INSERT INTO students(user_id,participant_id,usn,department,year,section,dob,semester) VALUES(?,?,?,?,?,?,?,?)",
              (uid, pid, usn, b.department.strip().upper(), b.year, b.section.strip().upper(), dob, b.semester))
    return uid, pid, usn, pw


@router.get("/students")
def students(q: str = "", department: str = "", year: str = "", limit: int = 100, offset: int = 0, u=Depends(admin), c=Depends(conn)):
    need(u, "participants")
    limit, offset = page(limit, offset)
    sql = ("SELECT us.id, us.name, us.email, us.status, us.last_login_at, us.created_at, s.participant_id, s.usn, s.department, s.year, s.section, s.semester, s.dob, "
           "(SELECT COUNT(*) FROM competition_participants cp WHERE cp.user_id=us.id) AS competitions, "
           "(SELECT COUNT(DISTINCT question_id) FROM submissions sb WHERE sb.user_id=us.id AND sb.verdict='AC') AS solved "
           "FROM users us JOIN students s ON s.user_id=us.id WHERE 1=1")
    a = []
    if q:
        sql += " AND (us.name LIKE ? OR s.usn LIKE ? OR s.participant_id LIKE ? OR us.email LIKE ?)"
        a += [f"%{q}%"] * 4
    if department:
        sql += " AND s.department=?"
        a.append(department.upper())
    if year.isdigit():
        sql += " AND s.year=?"
        a.append(int(year))
    total = db.one(c, f"SELECT COUNT(*) AS n FROM ({sql})", *a)["n"]
    return {"total": total, "rows": db.all_(c, sql + " ORDER BY us.id DESC LIMIT ? OFFSET ?", *a, limit, offset),
            "departments": [r["department"] for r in db.all_(c, "SELECT DISTINCT department FROM students WHERE department!='' ORDER BY 1")]}


@router.post("/students")
def create_student(body: StudentIn, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "participants")
    with db.tx(c):
        uid, pid, usn, pw = add_student(c, body, u)
        audit(c, u, "student.create", "user", uid, detail={"usn": usn}, ip=client_ip(req))
    return {"id": uid, "participant_id": pid, "username": usn, "temporary_password": None if (body.password or body.dob) else pw}


@router.post("/students/import")
async def import_students(req: Request, file: UploadFile = File(...), competition_id: int | None = None, u=Depends(admin), c=Depends(conn)):
    """CSV columns: name, usn, department, year, section, email[, password]. Returns a credentials CSV for new accounts."""
    need(u, "participants")
    if competition_id:
        comp_or_404(c, competition_id, u)
    raw = (await file.read(5 * 1024 * 1024)).decode("utf-8-sig", "replace")
    reader = csv.DictReader(io.StringIO(raw))
    if not reader.fieldnames or not {"name", "usn"} <= {f.strip().lower() for f in reader.fieldnames}:
        raise HTTPException(400, "CSV must have a header row with at least: name, usn (optional: department, year, section, email, password).")
    created, skipped, creds = 0, [], []
    with db.tx(c):
        for i, row in enumerate(reader, start=2):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            try:
                yr = int(row.get("year") or 1)
                dob = (row.get("dob") or "").strip()
                if dob and re.fullmatch(r"\d{2}[-/.]\d{2}[-/.]\d{4}", dob):  # DD-MM-YYYY -> YYYY-MM-DD
                    dob = f"{dob[6:]}-{dob[3:5]}-{dob[:2]}"
                b = StudentIn(name=row.get("name", ""), usn=row.get("usn", ""), department=row.get("department", ""), year=max(1, min(5, yr)),
                              section=row.get("section", ""), email=row.get("email", ""), dob=dob or None, password=row.get("password") or None,
                              semester=int(row["semester"]) if (row.get("semester") or "").isdigit() else None)
                uid, pid, usn, pw = add_student(c, b, u)
                created += 1
                creds.append({"name": b.name, "usn": usn, "participant_id": pid, "password": "date of birth (DDMMYYYY)" if (b.dob and not b.password) else pw})
            except HTTPException as e:
                existing = db.one(c, "SELECT user_id FROM students WHERE usn=?", re.sub(r"\s", "", row.get("usn", "")).upper())
                uid = existing["user_id"] if existing else None
                skipped.append({"row": i, "usn": row.get("usn"), "reason": e.detail})
            except (ValueError, TypeError) as e:
                uid = None
                skipped.append({"row": i, "usn": row.get("usn"), "reason": str(e)[:200]})
            if competition_id and uid:
                c.execute("INSERT OR IGNORE INTO competition_participants(competition_id,user_id,joined_at,source) VALUES(?,?,?,'organiser')", (competition_id, uid, db.now()))
        audit(c, u, "student.import", detail={"created": created, "skipped": len(skipped), "competition_id": competition_id}, ip=client_ip(req))
    return {"created": created, "skipped": skipped, "credentials": creds}


class StudentEditIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    department: str = Field(default="", max_length=40)
    year: int = Field(default=1, ge=1, le=5)
    semester: int | None = Field(default=None, ge=1, le=10)
    dob: str | None = Field(default=None, max_length=10)
    section: str = Field(default="", max_length=10)
    email: str = Field(default="", max_length=120)
    status: str = Field(default="active", pattern="^(active|suspended)$")


@router.put("/students/{uid}")
def edit_student(uid: int, body: StudentEditIn, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "participants")
    if not db.one(c, "SELECT 1 AS x FROM students WHERE user_id=?", uid):
        raise HTTPException(404, "Student not found.")
    with db.tx(c):
        c.execute("UPDATE users SET name=?, email=?, status=? WHERE id=?", (body.name.strip(), body.email.strip() or None, body.status, uid))
        c.execute("UPDATE students SET department=?, year=?, section=?, semester=?, dob=? WHERE user_id=?",
                  (body.department.strip().upper(), body.year, body.section.strip().upper(), body.semester, check_dob(body.dob) if body.dob else None, uid))
        if body.status == "suspended":
            c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        audit(c, u, "student.update", "user", uid, detail=body.model_dump(), ip=client_ip(req))
    return {"ok": True}


@router.post("/students/{uid}/reset-password")
def reset_student_pw(uid: int, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "participants")
    st = db.one(c, "SELECT s.dob FROM users u JOIN students s ON s.user_id=u.id WHERE u.id=? AND u.kind='student'", uid)
    if not st:
        raise HTTPException(404, "Student not found.")
    pw = dob_password(st["dob"]) if st["dob"] else temp_password()
    c.execute("UPDATE users SET password_hash=?, must_change_pw=?, failed_logins=0, locked_until=0 WHERE id=?", (hash_password(pw), 0 if st["dob"] else 1, uid))
    c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    audit(c, u, "student.reset_password", "user", uid, ip=client_ip(req))
    return {"temporary_password": pw, "is_dob": bool(st["dob"])}


@router.get("/students/{uid}")
def student_detail(uid: int, u=Depends(admin), c=Depends(conn)):
    need(u, "participants")
    s = db.one(c, "SELECT us.id, us.name, us.email, us.status, us.avatar, us.last_login_at, us.last_login_ip, us.created_at, s.* FROM users us JOIN students s ON s.user_id=us.id WHERE us.id=?", uid)
    if not s:
        raise HTTPException(404, "Student not found.")
    s["history"] = []
    for comp in db.all_(c, "SELECT co.* FROM competitions co JOIN competition_participants cp ON cp.competition_id=co.id WHERE cp.user_id=? ORDER BY co.id DESC", uid):
        lb = contest.leaderboard(c, comp)
        r = next((x for x in lb["rows"] if x["user_id"] == uid), None)
        s["history"].append({"id": comp["id"], "code": comp["code"], "title": comp["title"], "status": contest.display_status(comp),
                             "rank": r["rank"] if r else None, "score": r["score"] if r else None, "solved": r["solved"] if r else None, "of": len(lb["rows"])})
    s["submissions"] = db.all_(c, "SELECT s.id, s.kind, s.language, s.verdict, s.score, s.created_at, qv.title, co.code AS competition FROM submissions s "
                                  "JOIN question_versions qv ON qv.id=s.version_id LEFT JOIN competitions co ON co.id=s.competition_id WHERE s.user_id=? AND s.kind IN ('submit','practice') ORDER BY s.id DESC LIMIT 50", uid)
    return s


# ================= submissions =================

@router.get("/submissions")
def list_subs(competition_id: str = "", user_id: str = "", question_id: str = "", verdict: str = "",
              kind: str = "submit", limit: int = 100, offset: int = 0, u=Depends(admin), c=Depends(conn)):
    need(u, "submissions")
    limit, offset = page(limit, offset)
    sql = ("SELECT s.id, s.competition_id, s.user_id, s.question_id, s.kind, s.language, s.status, s.verdict, s.score, s.passed, s.total, s.time_ms, s.memory_kb, "
           "s.created_at, s.ip, us.name, st.participant_id, qv.title, co.code AS competition FROM submissions s JOIN users us ON us.id=s.user_id "
           "LEFT JOIN students st ON st.user_id=us.id JOIN question_versions qv ON qv.id=s.version_id LEFT JOIN competitions co ON co.id=s.competition_id WHERE s.kind=?")
    a = [kind if kind in ("submit", "practice", "run", "validate") else "submit"]
    if u["role"] != "super_admin":
        sql += " AND (s.competition_id IN (SELECT competition_id FROM competition_admins WHERE user_id=?) OR s.competition_id IS NULL)"
        a.append(u["id"])
    for col, val in (("s.competition_id", competition_id), ("s.user_id", user_id), ("s.question_id", question_id)):
        if str(val).isdigit():
            sql += f" AND {col}=?"
            a.append(int(val))
    if verdict:
        sql += " AND (s.verdict=? OR s.status=?)"
        a += [verdict, verdict]
    total = db.one(c, f"SELECT COUNT(*) AS n FROM ({sql})", *a)["n"]
    return {"total": total, "rows": db.all_(c, sql + " ORDER BY s.id DESC LIMIT ? OFFSET ?", *a, limit, offset)}


@router.get("/submissions/{sid}")
def sub_detail(sid: int, u=Depends(admin), c=Depends(conn)):
    need(u, "submissions")
    s = db.one(c, "SELECT s.*, us.name, qv.title FROM submissions s JOIN users us ON us.id=s.user_id JOIN question_versions qv ON qv.id=s.version_id WHERE s.id=?", sid)
    if not s:
        raise HTTPException(404, "Submission not found.")
    if s["competition_id"] and not can_manage_comp(c, u, s["competition_id"]):
        raise HTTPException(403, "Not your competition.")
    s["results"] = db.all_(c, "SELECT jr.ordinal, jr.visibility, jr.verdict, jr.weight, jr.time_ms, jr.memory_kb, substr(jr.output,1,3000) AS output, jr.stderr, "
                              "substr(t.input,1,3000) AS input, substr(t.expected,1,3000) AS expected FROM judging_results jr LEFT JOIN test_cases t ON t.id=jr.test_case_id "
                              "WHERE jr.submission_id=? ORDER BY jr.ordinal", sid)
    return s


@router.post("/submissions/{sid}/rejudge")
def rejudge(sid: int, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "submissions")
    s = db.one(c, "SELECT * FROM submissions WHERE id=?", sid)
    if not s:
        raise HTTPException(404, "Submission not found.")
    if s["competition_id"]:
        comp = comp_or_404(c, s["competition_id"], u)
        if comp["status"] in ("finalized", "archived"):
            raise HTTPException(409, "Results are finalized.")
    c.execute("UPDATE submissions SET status='queued', attempts=0, worker=NULL WHERE id=? AND status!='running'", (sid,))
    audit(c, u, "submission.rejudge", "submission", sid, s["competition_id"], ip=client_ip(req))
    return {"ok": True}


# ================= semesters =================

class SemesterIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    academic_year: str = Field(min_length=4, max_length=20)
    term: str = Field(default="", max_length=40)


@router.get("/semesters")
def list_semesters(u=Depends(admin), c=Depends(conn)):
    if not (has(u, "semesters") or has(u, "semesters.view")):
        need(u, "semesters")
    rows = db.all_(c, "SELECT * FROM semesters ORDER BY id DESC")
    for r in rows:
        r["competitions"] = db.all_(c, "SELECT sc.points, co.id, co.code, co.title, co.round_no, co.status FROM semester_competitions sc JOIN competitions co ON co.id=sc.competition_id WHERE sc.semester_id=? ORDER BY co.start_at", r["id"])
    return rows


@router.post("/semesters")
def create_semester(body: SemesterIn, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "semesters")
    sid = c.execute("INSERT INTO semesters(name,academic_year,term,created_by,created_at) VALUES(?,?,?,?,?)",
                    (body.name, body.academic_year, body.term, u["id"], db.now())).lastrowid
    audit(c, u, "semester.create", "semester", sid, detail=body.model_dump(), ip=client_ip(req))
    return {"id": sid}


@router.put("/semesters/{sid}")
def edit_semester(sid: int, body: SemesterIn, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "semesters")
    c.execute("UPDATE semesters SET name=?, academic_year=?, term=? WHERE id=?", (body.name, body.academic_year, body.term, sid))
    audit(c, u, "semester.update", "semester", sid, detail=body.model_dump(), ip=client_ip(req))
    return {"ok": True}


class SemCompIn(BaseModel):
    competition_id: int
    points: int = Field(default=100, ge=0, le=10000)


def sem_open(c, sid):
    s = db.one(c, "SELECT * FROM semesters WHERE id=?", sid)
    if not s:
        raise HTTPException(404, "Semester not found.")
    if s["status"] != "active":
        raise HTTPException(409, f"Semester is {s['status']}; unfreeze it to change contributions.")
    return s


@router.post("/semesters/{sid}/competitions")
def sem_add(sid: int, body: SemCompIn, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "semesters")
    sem_open(c, sid)
    if not db.one(c, "SELECT 1 AS x FROM competitions WHERE id=?", body.competition_id):
        raise HTTPException(404, "Competition not found.")
    other = db.one(c, "SELECT semester_id FROM semester_competitions WHERE competition_id=?", body.competition_id)
    if other and other["semester_id"] != sid:
        raise HTTPException(409, "That competition already counts towards another semester.")
    c.execute("INSERT INTO semester_competitions(semester_id,competition_id,points) VALUES(?,?,?) ON CONFLICT(semester_id,competition_id) DO UPDATE SET points=excluded.points",
              (sid, body.competition_id, body.points))
    audit(c, u, "semester.add_competition", "semester", sid, body.competition_id, body.model_dump(), client_ip(req))
    return {"ok": True}


@router.delete("/semesters/{sid}/competitions/{cid}")
def sem_remove(sid: int, cid: int, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "semesters")
    sem_open(c, sid)
    c.execute("DELETE FROM semester_competitions WHERE semester_id=? AND competition_id=?", (sid, cid))
    audit(c, u, "semester.remove_competition", "semester", sid, cid, ip=client_ip(req))
    return {"ok": True}


class SemActionIn(BaseModel):
    action: str = Field(pattern="^(freeze|unfreeze|finalize|archive)$")


@router.post("/semesters/{sid}/action")
def sem_action(sid: int, body: SemActionIn, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "semesters")
    s = db.one(c, "SELECT * FROM semesters WHERE id=?", sid)
    if not s:
        raise HTTPException(404, "Semester not found.")
    flow = {"freeze": ({"active"}, "frozen"), "unfreeze": ({"frozen"}, "active"), "finalize": ({"active", "frozen"}, "finalized"),
            "archive": ({"finalized"}, "archived")}
    allowed, new = flow[body.action]
    if s["status"] not in allowed:
        raise HTTPException(409, f"Cannot {body.action} a semester that is {s['status']}.")
    with db.tx(c):
        if body.action in ("freeze", "finalize"):
            db.set_setting(c, f"semester_snapshot:{sid}", json.dumps(contest.semester_leaderboard(c, sid)))
        elif body.action == "unfreeze":
            c.execute("DELETE FROM platform_settings WHERE key=?", (f"semester_snapshot:{sid}",))
        c.execute("UPDATE semesters SET status=? WHERE id=?", (new, sid))
        audit(c, u, f"semester.{body.action}", "semester", sid, ip=client_ip(req))
    return {"ok": True}


@router.get("/semesters/{sid}/leaderboard")
def admin_sem_lb(sid: int, u=Depends(admin), c=Depends(conn)):
    if not (has(u, "semesters") or has(u, "semesters.view")):
        need(u, "semesters")
    from .api_student import semester_board
    s = db.one(c, "SELECT * FROM semesters WHERE id=?", sid)
    if not s:
        raise HTTPException(404, "Semester not found.")
    return {"semester": s, **semester_board(c, s)}


# ================= reports (CSV) =================

def csv_resp(name, header, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow([("'" + str(x)) if isinstance(x, str) and x[:1] in ("=", "+", "-", "@") else ("" if x is None else x) for x in r])
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'})


def fmt(t):
    return datetime.fromtimestamp(t, TZ).strftime("%Y-%m-%d %H:%M:%S") if t else ""


@router.get("/reports/{kind}.csv")
def report(kind: str, req: Request, competition_id: int | None = None, semester_id: int | None = None, user_id: int | None = None,
           u=Depends(admin), c=Depends(conn)):
    need(u, "reports")
    audit(c, u, "report.export", detail={"kind": kind, "competition_id": competition_id, "semester_id": semester_id, "user_id": user_id}, ip=client_ip(req))
    if kind == "semester":
        if not semester_id:
            raise HTTPException(400, "semester_id required.")
        from .api_student import semester_board
        s = db.one(c, "SELECT * FROM semesters WHERE id=?", semester_id)
        if not s:
            raise HTTPException(404, "Semester not found.")
        lb = semester_board(c, s)
        cols = lb["competitions"]
        return csv_resp(f"semester-{semester_id}-leaderboard.csv", ["Rank", "Participant ID", "Name", "Department", "Year", "Total Points", "Problems Solved", "Rounds Played"]
                        + [f"{x['code']} ({x['points']} pts)" for x in cols],
                        [[r["rank"], r["participant_id"], r["name"], r["department"], r["year"], r["points"], r["solved"], r["played"]]
                         + [r["rounds"].get(str(x["id"]), "") for x in cols] for r in lb["rows"]])
    if kind == "student":
        if not user_id:
            raise HTTPException(400, "user_id required.")
        s = student_detail(user_id, u, c)
        return csv_resp(f"student-{s['participant_id']}.csv", ["Participant ID", "Name", "USN", "Department", "Competition", "Title", "Status", "Rank", "Of", "Score", "Solved"],
                        [[s["participant_id"], s["name"], s["usn"], s["department"], h["code"], h["title"], h["status"], h["rank"], h["of"], h["score"], h["solved"]] for h in s["history"]])
    if not competition_id:
        raise HTTPException(400, "competition_id required.")
    comp = comp_or_404(c, competition_id, u)
    code = comp["code"]
    if kind == "participants":
        rows = comp_participants(competition_id, u, c)
        return csv_resp(f"{code}-participants.csv", ["Participant ID", "Name", "USN", "Department", "Year", "Section", "Joined", "Source", "Status", "Submissions", "Accepted"],
                        [[r["participant_id"], r["name"], r["usn"], r["department"], r["year"], r["section"], fmt(r["joined_at"]), r["source"], r["status"], r["submissions"], r["accepted"]] for r in rows])
    if kind in ("scores", "rankings"):
        lb = contest.leaderboard(c, comp)
        return csv_resp(f"{code}-final-rankings.csv", ["Rank", "Participant ID", "Name", "Department", "Year", "Score", "Solved", "Penalty (min)"]
                        + [f"{q['label']}. {q['title']} ({q['marks']})" for q in lb["questions"]],
                        [[r["rank"], r["participant_id"], r["name"], r["department"], r["year"], r["score"], r["solved"], r["penalty"]]
                         + [r["per"].get(str(q["id"]), {}).get("score", "") for q in lb["questions"]] for r in lb["rows"]])
    if kind == "questions":
        rows = db.all_(c, "SELECT cq.ordinal, qv.title, cq.marks, COUNT(s.id) AS attempts, COUNT(DISTINCT s.user_id) AS tried, "
                          "COUNT(DISTINCT CASE WHEN s.verdict='AC' THEN s.user_id END) AS solved, ROUND(AVG(s.score),2) AS avg_score, "
                          "SUM(s.verdict='WA') AS wa, SUM(s.verdict='TLE') AS tle, SUM(s.verdict='RE') AS re, SUM(s.verdict='CE') AS ce "
                          "FROM competition_questions cq JOIN question_versions qv ON qv.id=cq.version_id LEFT JOIN submissions s ON s.competition_id=cq.competition_id "
                          "AND s.question_id=cq.question_id AND s.kind='submit' WHERE cq.competition_id=? GROUP BY cq.question_id ORDER BY cq.ordinal", competition_id)
        return csv_resp(f"{code}-question-performance.csv", ["#", "Question", "Marks", "Attempts", "Participants tried", "Solved by", "Avg score", "WA", "TLE", "RE", "CE"],
                        [[chr(65 + r["ordinal"]), r["title"], r["marks"], r["attempts"], r["tried"], r["solved"], r["avg_score"], r["wa"], r["tle"], r["re"], r["ce"]] for r in rows])
    if kind == "submissions":
        rows = db.all_(c, "SELECT s.id, s.created_at, st.participant_id, us.name, qv.title, s.language, s.verdict, s.score, s.passed, s.total, s.time_ms, s.memory_kb, s.ip "
                          "FROM submissions s JOIN users us ON us.id=s.user_id LEFT JOIN students st ON st.user_id=us.id JOIN question_versions qv ON qv.id=s.version_id "
                          "WHERE s.competition_id=? AND s.kind='submit' ORDER BY s.id", competition_id)
        return csv_resp(f"{code}-submissions.csv", ["Submission", "Time", "Participant ID", "Name", "Question", "Language", "Verdict", "Score", "Passed", "Total", "Time (ms)", "Memory (KB)", "IP"],
                        [[r["id"], fmt(r["created_at"]), r["participant_id"], r["name"], r["title"], r["language"], r["verdict"], r["score"], r["passed"], r["total"], r["time_ms"], r["memory_kb"], r["ip"]] for r in rows])
    if kind == "integrity":
        rows = integrity_rows(c, competition_id)
        return csv_resp(f"{code}-integrity.csv", ["Participant ID", "Name", "Risk", "Pastes", "Large pastes", "Blocked pastes", "Copies", "Tab switches", "Away (s)", "Window blurs", "DevTools", "IPs", "Shared IP with"],
                        [[r["participant_id"], r["name"], r["risk"], r["pastes"], r["large_pastes"], r["blocked_pastes"], r["copies"], r["tab_switches"], r["away_sec"], r["blurs"], r["devtools"],
                          " ".join(r["ips"]), "; ".join(r["shared_ip_with"])] for r in rows])
    raise HTTPException(404, "Unknown report.")


# ================= admin users =================

class AdminUserIn(BaseModel):
    username: str = Field(min_length=3, max_length=60, pattern=r"^[A-Za-z0-9._@-]+$")
    name: str = Field(min_length=2, max_length=100)
    email: str = Field(default="", max_length=120)
    role: str = Field(pattern="^(super_admin|event_admin|reviewer)$")
    can_approve: bool = False
    password: str | None = Field(default=None, max_length=200)


class AdminEditIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: str = Field(default="", max_length=120)
    role: str = Field(pattern="^(super_admin|event_admin|reviewer)$")
    can_approve: bool = False
    status: str = Field(pattern="^(active|suspended)$")


@router.get("/users")
def list_admins(u=Depends(admin), c=Depends(conn)):
    need(u, "admins")
    return db.all_(c, "SELECT id, username, name, email, role, can_approve, status, last_login_at, last_login_ip, created_at, locked_until FROM users WHERE kind='admin' ORDER BY id")


@router.post("/users")
def create_admin(body: AdminUserIn, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "admins")
    if body.role == "super_admin" and u["role"] != "super_admin":
        raise HTTPException(403, "Only a Super Admin can grant Super Admin.")
    pw = body.password or temp_password()
    if body.password:
        check_password_strength(pw)
    uid = c.execute("INSERT INTO users(username,email,name,password_hash,kind,role,can_approve,must_change_pw,created_by,created_at) VALUES(?,?,?,?,'admin',?,?,?,?,?)",
                    (body.username, body.email or None, body.name, hash_password(pw), body.role, int(body.can_approve), 0 if body.password else 1, u["id"], db.now())).lastrowid
    audit(c, u, "admin.create", "user", uid, detail={"username": body.username, "role": body.role}, ip=client_ip(req))
    return {"id": uid, "temporary_password": None if body.password else pw}


@router.put("/users/{uid}")
def edit_admin(uid: int, body: AdminEditIn, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "admins")
    t = db.one(c, "SELECT * FROM users WHERE id=? AND kind='admin'", uid)
    if not t:
        raise HTTPException(404, "Admin not found.")
    if (body.role == "super_admin" or t["role"] == "super_admin") and u["role"] != "super_admin":
        raise HTTPException(403, "Only a Super Admin can change Super Admin accounts.")
    if uid == u["id"] and (body.role != "super_admin" or body.status != "active"):
        raise HTTPException(400, "You cannot demote or suspend yourself.")
    if t["role"] == "super_admin" and (body.role != "super_admin" or body.status != "active"):
        if db.one(c, "SELECT COUNT(*) AS n FROM users WHERE kind='admin' AND role='super_admin' AND status='active'")["n"] <= 1:
            raise HTTPException(400, "There must always be at least one active Super Admin.")
    c.execute("UPDATE users SET name=?, email=?, role=?, can_approve=?, status=? WHERE id=?", (body.name, body.email or None, body.role, int(body.can_approve), body.status, uid))
    if body.status == "suspended":
        c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    audit(c, u, "admin.update", "user", uid, detail=body.model_dump(), ip=client_ip(req))
    return {"ok": True}


@router.post("/users/{uid}/reset-password")
def reset_admin_pw(uid: int, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "admins")
    t = db.one(c, "SELECT * FROM users WHERE id=? AND kind='admin'", uid)
    if not t:
        raise HTTPException(404, "Admin not found.")
    pw = temp_password()
    c.execute("UPDATE users SET password_hash=?, must_change_pw=1, failed_logins=0, locked_until=0 WHERE id=?", (hash_password(pw), uid))
    c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    audit(c, u, "admin.reset_password", "user", uid, ip=client_ip(req))
    return {"temporary_password": pw}


# ================= audit =================

@router.get("/audit")
def audit_logs(action: str = "", user_id: str = "", competition_id: str = "", limit: int = 100, offset: int = 0, u=Depends(admin), c=Depends(conn)):
    need(u, "audit")
    limit, offset = page(limit, offset)
    sql = "SELECT a.*, us.name, us.username FROM audit_logs a LEFT JOIN users us ON us.id=a.user_id WHERE 1=1"
    a = []
    if action:
        sql += " AND a.action LIKE ?"
        a.append(f"%{action}%")
    if str(user_id).isdigit():
        sql += " AND a.user_id=?"
        a.append(int(user_id))
    if str(competition_id).isdigit():
        sql += " AND a.competition_id=?"
        a.append(int(competition_id))
    total = db.one(c, f"SELECT COUNT(*) AS n FROM ({sql})", *a)["n"]
    return {"total": total, "rows": db.all_(c, sql + " ORDER BY a.id DESC LIMIT ? OFFSET ?", *a, limit, offset)}
