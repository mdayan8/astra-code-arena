"""Password hashing (stdlib scrypt), cookie sessions, CSRF, rate limiting, RBAC."""
import hashlib
import hmac
import secrets
import threading
import time

from fastapi import HTTPException, Request

from . import config, db

STUDENT_COOKIE = "astra_s"
ADMIN_COOKIE = "astra_a"

# ---------- passwords ----------

def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${h.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
        got = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(got.hex(), h)
    except (ValueError, TypeError):
        return False


def check_password_strength(pw: str):
    if len(pw or "") < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")


def temp_password() -> str:
    return secrets.token_urlsafe(8)


# ---------- rate limiting (in-memory, per process) ----------
_hits: dict = {}
_lock = threading.Lock()


def rate_limit(key: str, limit: int, window: int):
    t = time.time()
    with _lock:
        q = [x for x in _hits.get(key, []) if x > t - window]
        if len(q) >= limit:
            _hits[key] = q
            raise HTTPException(429, "Too many requests. Please wait a moment and try again.")
        q.append(t)
        _hits[key] = q
        if len(_hits) > 50000:  # keep memory bounded
            _hits.clear()


def client_ip(req: Request) -> str:
    return req.client.host if req.client else "?"


# ---------- sessions ----------

def _th(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def login(c, user: dict, req: Request, resp, cookie: str):
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    t = db.now()
    c.execute("INSERT INTO sessions(token_hash,user_id,csrf,ip,ua,created_at,expires_at) VALUES(?,?,?,?,?,?,?)",
              (_th(token), user["id"], csrf, client_ip(req), (req.headers.get("user-agent") or "")[:200], t, t + config.SESSION_HOURS * 3600))
    c.execute("UPDATE users SET failed_logins=0, locked_until=0, last_login_at=?, last_login_ip=? WHERE id=?", (t, client_ip(req), user["id"]))
    resp.set_cookie(cookie, token, httponly=True, secure=config.COOKIE_SECURE, samesite="strict",
                    max_age=config.SESSION_HOURS * 3600, path="/")
    return csrf


def logout(c, req: Request, resp, cookie: str):
    tok = req.cookies.get(cookie)
    if tok:
        c.execute("DELETE FROM sessions WHERE token_hash=?", (_th(tok),))
    resp.delete_cookie(cookie, path="/")


def current_user(c, req: Request, cookie: str, kind: str):
    tok = req.cookies.get(cookie)
    if not tok:
        raise HTTPException(401, "Please sign in.")
    s = db.one(c, "SELECT s.csrf, s.expires_at, u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?", _th(tok))
    if not s or s["expires_at"] < db.now() or s["kind"] != kind or s["status"] != "active":
        raise HTTPException(401, "Session expired. Please sign in again.")
    if req.method not in ("GET", "HEAD", "OPTIONS"):
        sent = req.headers.get("x-csrf-token", "")
        if not hmac.compare_digest(sent, s["csrf"]):
            raise HTTPException(403, "Security token mismatch. Refresh the page and try again.")
    s.pop("password_hash", None)
    return s


def attempt_login(c, username: str, password: str, kind: str, req: Request):
    """Shared login check with per-IP rate limit + progressive account lockout."""
    ip = client_ip(req)
    rate_limit(f"login:{ip}", 150, 60)  # generous: a whole lab may share one NAT IP; per-account lockout stops guessing
    u = db.one(c, "SELECT * FROM users WHERE username=? AND kind=?", (username or "").strip(), kind)
    if not u and kind == "student":
        u = db.one(c, "SELECT u.* FROM users u JOIN students s ON s.user_id=u.id WHERE s.participant_id=? COLLATE NOCASE OR s.usn=?",
                   (username or "").strip(), (username or "").strip())
    t = db.now()
    if u and u["locked_until"] > t:
        raise HTTPException(429, f"Account temporarily locked after repeated failures. Try again in {u['locked_until'] - t} seconds.")
    if not u or not verify_password(password or "", u["password_hash"]):
        if u:
            n = u["failed_logins"] + 1
            lock = t + min(15 * 60 * 2 ** max(0, n // 5 - 1), 24 * 3600) if n % 5 == 0 else 0
            c.execute("UPDATE users SET failed_logins=?, locked_until=? WHERE id=?", (n, lock or u["locked_until"], u["id"]))
        time.sleep(0.4)  # slow down guessing
        raise HTTPException(401, "Incorrect username or password.")
    if u["status"] != "active":
        raise HTTPException(403, "This account is suspended. Contact the organisers.")
    return u


# ---------- RBAC ----------
ROLE_PERMS = {
    "super_admin": {"*"},
    "event_admin": {"dashboard", "competitions", "competitions.create", "questions", "questions.approve", "practice", "pdf", "ai", "tests",
                    "participants", "submissions", "leaderboards", "reports", "semesters.view"},
    "reviewer": {"dashboard", "questions", "pdf", "tests"},
}
MODULES = ["dashboard", "competitions", "questions", "practice", "pdf", "ai", "tests", "participants", "submissions", "leaderboards",
           "semesters", "reports", "admins", "audit", "database", "settings"]


def perms(user) -> set:
    p = set(ROLE_PERMS.get(user["role"], set()))
    if user["role"] == "reviewer" and user["can_approve"]:
        p.add("questions.approve")
    return p


def has(user, perm) -> bool:
    p = perms(user)
    return "*" in p or perm in p


def need(user, perm):
    if not has(user, perm):
        raise HTTPException(403, "You don't have permission for this action.")


def can_manage_comp(c, user, comp_id) -> bool:
    if user["role"] == "super_admin":
        return True
    if user["role"] != "event_admin":
        return False
    return bool(db.one(c, "SELECT 1 AS x FROM competition_admins WHERE competition_id=? AND user_id=?", comp_id, user["id"]))


def need_comp(c, user, comp_id):
    if not can_manage_comp(c, user, comp_id):
        raise HTTPException(403, "You are not an organiser of this competition.")


def audit(c, user, action, entity=None, entity_id=None, competition_id=None, detail=None, ip=None):
    import json
    c.execute("INSERT INTO audit_logs(user_id,action,entity,entity_id,competition_id,detail,ip,created_at) VALUES(?,?,?,?,?,?,?,?)",
              (user["id"] if user else None, action, entity, entity_id, competition_id,
               json.dumps(detail, default=str)[:4000] if detail is not None else None, ip, db.now()))
