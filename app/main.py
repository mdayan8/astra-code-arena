"""ASTRA Code Arena web server: `python -m app.main` (or uvicorn app.main:app)."""
import secrets
import sqlite3
import traceback

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config, db

db.init()


def admin_path() -> str:
    if config.ADMIN_PATH:
        return config.ADMIN_PATH.strip("/")
    c = db.connect()
    p = db.setting(c, "admin_path", "")
    if not p:
        p = f"astra-control-{secrets.token_hex(6)}"
        db.set_setting(c, "admin_path", p)
    c.close()
    return p


ADMIN = admin_path()
app = FastAPI(title="ASTRA Code Arena", docs_url=None, redoc_url=None, openapi_url=None)


import ipaddress  # noqa: E402

_NETS = [ipaddress.ip_network(n, strict=False) for n in config.ALLOWED_NETWORKS]
BLOCKED_PAGE = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Campus network only</title>"
                "<body style='font-family:system-ui;display:grid;place-items:center;min-height:90vh;color:#16161d;background:#f7f7fa'><div style='text-align:center;max-width:420px'>"
                "<h2>Campus network only</h2><p>Code Arena is available only on the PESITM campus network. Connect to the college LAN or Wi-Fi and try again.</p></div>")


_sched = {"at": 0, "open": True, "next": None, "msg": ""}


def _schedule_open():
    """Opening hours (System Settings / launcher). Cached 15 s. Never closes while a competition is live."""
    import json
    import time as _t
    if _t.time() - _sched["at"] > 15:
        from . import hostinfo
        c = db.connect()
        try:
            s = json.loads(db.setting(c, "schedule", "{}") or "{}")
            live = db.one(c, "SELECT 1 AS x FROM competitions WHERE status IN ('live','paused') LIMIT 1")
        finally:
            c.close()
        ok, nxt = hostinfo.schedule_state(s)
        _sched.update(at=_t.time(), open=ok or bool(live), next=nxt.strftime("%a %d %b, %I:%M %p") if nxt else None, msg=s.get("message", ""))
    return _sched


@app.middleware("http")
async def schedule_gate(request: Request, call_next):
    p = request.url.path
    if not (p.startswith(f"/{ADMIN}") or p.startswith("/api/admin") or p.startswith("/static") or p in ("/api/health", "/favicon.ico", "/robots.txt")):
        st = _schedule_open()
        if not st["open"]:
            when = f"It opens again {st['next']}." if st["next"] else "Please check back later."
            if p.startswith("/api/"):
                return JSONResponse({"error": f"Code Arena is closed right now. {when}"}, status_code=503)
            return HTMLResponse(CLOSED_PAGE.replace("{when}", when).replace("{msg}", st["msg"] or ""), status_code=503)
    return await call_next(request)


CLOSED_PAGE = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Code Arena is closed</title>"
               "<link rel=icon href=/static/img/arena-logo.svg><body style='margin:0;font-family:system-ui;display:grid;place-items:center;min-height:100vh;background:#f7f7fa;color:#16161d'>"
               "<div style='text-align:center;max-width:440px;padding:24px'><img src=/static/img/arena-logo.svg width=64 height=64 alt=''>"
               "<h1 style='margin:16px 0 8px'>Code Arena is closed</h1><p style='color:#555'>{when}</p><p style='color:#555'>{msg}</p></div>")


@app.middleware("http")
async def headers(request: Request, call_next):
    if _NETS:
        try:
            ip = ipaddress.ip_address(request.client.host if request.client else "0.0.0.0")
            allowed = ip.is_loopback or any(ip in n for n in _NETS)
        except ValueError:
            allowed = False
        if not allowed:
            return HTMLResponse(BLOCKED_PAGE, status_code=403)
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    p = request.url.path
    if p.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    elif p.startswith("/static/vendor/"):
        resp.headers["Cache-Control"] = "public, max-age=604800"  # Monaco bundle: cache a week
    elif p.startswith("/static/") or p.endswith(".js") or p == "/":
        resp.headers["Cache-Control"] = "no-cache"  # revalidate (cheap 304) so updates reach every browser
    if request.url.path.startswith(f"/{ADMIN}") or request.url.path.startswith("/api/admin"):
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


@app.exception_handler(HTTPException)
async def http_err(request: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


@app.exception_handler(sqlite3.IntegrityError)
async def integrity_err(request: Request, exc):
    msg = str(exc)
    if "UNIQUE" in msg:
        field = msg.split(":")[-1].strip().split(".")[-1]
        return JSONResponse({"error": f"That {field.replace('_', ' ')} is already in use."}, status_code=409)
    return JSONResponse({"error": "That change conflicts with existing data."}, status_code=409)


@app.exception_handler(Exception)
async def any_err(request: Request, exc):
    c = db.connect()
    db.log_error(c, "web", f"{request.method} {request.url.path}: {traceback.format_exc()[-1800:]}")
    c.close()
    return JSONResponse({"error": "Something went wrong on the server. It has been logged."}, status_code=500)


from . import api_admin, api_db, api_student, api_tools  # noqa: E402

app.include_router(api_student.router)
app.include_router(api_admin.router)
app.include_router(api_tools.router)
app.include_router(api_db.router)

STATIC = config.ROOT / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/favicon.ico")
def favicon():
    return FileResponse(STATIC / "img" / "favicon.png")


@app.get("/robots.txt")
def robots():
    return Response("User-agent: *\nDisallow: /api/\n", media_type="text/plain")


@app.get(f"/{ADMIN}")
def admin_redirect():
    return RedirectResponse(f"/{ADMIN}/", status_code=308)


@app.get(f"/{ADMIN}/")
def admin_page():
    return FileResponse(config.ROOT / "admin" / "admin.html", headers={"Cache-Control": "no-store"})


@app.get(f"/{ADMIN}/admin.js")
def admin_js():
    return FileResponse(config.ROOT / "admin" / "admin.js", media_type="text/javascript")


@app.get("/api/health")
def health():
    return {"ok": True}


def run():
    import uvicorn
    print(f"ASTRA Code Arena on http://{config.APP_HOST}:{config.APP_PORT}/")
    print(f"Admin route: /{ADMIN}/  (keep private; never link it)")
    uvicorn.run("app.main:app", host=config.APP_HOST, port=config.APP_PORT, workers=1, proxy_headers=False,
                log_level="warning" if config.APP_ENV == "production" else "info", timeout_keep_alive=30)


if __name__ == "__main__":
    run()
