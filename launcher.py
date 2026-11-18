"""ASTRA Arena control panel (http://127.0.0.1:8765, this computer only).
Press Run: it checks the machine, starts the web server + judge in the background and restarts them if they crash.
Also: opening hours, address/port, firewall, start-at-boot, backups, Diagnose & fix, and the built-in guide.
    python launcher.py               open the panel (starts it if it is not running)
    python launcher.py --background  run without opening a browser (what the systemd service uses)"""
import http.server
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
from app import config, db, hostinfo, judge  # noqa: E402

PANEL_PORT = 8765
STATE_F = config.DATA_DIR / "launcher.json"
LOGS = config.DATA_DIR / "logs"
LOGS.mkdir(parents=True, exist_ok=True)
SERVICE = "astra-arena"
IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0
TOKEN = secrets.token_urlsafe(16)
DEFAULT = {"port": 8000, "bind": "0.0.0.0", "workers": None, "run_on_open": True, "running": True, "backup_mirror": "", "first_password": None}
APT = {"c": "gcc", "cpp": "g++", "python": "python3", "java": "default-jdk-headless", "javascript": "nodejs", "go": "golang-go", "rust": "rustc"}
PARTS = (("web", "app.main"), ("judge", "app.worker"))


def load():
    try:
        return {**DEFAULT, **json.loads(STATE_F.read_text())}
    except (OSError, ValueError):
        return dict(DEFAULT)


def save():
    tmp = STATE_F.with_suffix(".tmp")
    tmp.write_text(json.dumps(S, indent=1))
    tmp.replace(STATE_F)  # atomic: a power cut never leaves a half-written settings file


S = load()
procs = {}
run = {"wanted": False, "started_at": None, "restarts": 0, "last_error": "", "job": ""}


def sh(*cmd, timeout=20):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def lan_ips():
    ips = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # no packet is sent; picks the LAN interface
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    code, out = sh("hostname", "-I")
    if code == 0:
        ips.update(x for x in out.split() if "." in x)
    return sorted(i for i in ips if not i.startswith(("127.", "172.17.")))


def port_free(port, host="0.0.0.0"):
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def child_env():
    return {**os.environ, "APP_PORT": str(S["port"]), "APP_HOST": S["bind"], "APP_ENV": "production",
            "JUDGE_WORKERS": str(S["workers"] or hostinfo.specs()["recommended_workers"]), "BACKUP_MIRROR": S["backup_mirror"] or ""}


def spawn(name, mod):
    f = LOGS / f"{name}.log"
    if f.exists() and f.stat().st_size > 5_000_000:  # keep logs small: one rotated copy
        f.replace(f.with_suffix(".log.1"))
    log = open(f, "ab")
    procs[name] = subprocess.Popen([sys.executable, "-m", mod], cwd=ROOT, env=child_env(), stdout=log, stderr=log)


def start():
    if procs:
        return
    if not port_free(S["port"], S["bind"]):
        run["last_error"] = f"Port {S['port']} is already used by another program. Run Diagnose, or choose another port under Network."
        return
    run.update(wanted=True, started_at=time.time(), last_error="")
    S["running"] = True
    save()
    for name, mod in PARTS:
        spawn(name, mod)


def stop(remember=True):
    run.update(wanted=False, started_at=None)
    if remember:
        S["running"] = False
        save()
    for p in procs.values():
        p.terminate()
    for p in procs.values():
        try:
            p.wait(15)
        except subprocess.TimeoutExpired:
            p.kill()
    procs.clear()


def supervise():
    """Restart anything that dies while it should be running (crash, killed, out of memory)."""
    while True:
        time.sleep(5)
        if not run["wanted"]:
            continue
        for name, mod in PARTS:
            p = procs.get(name)
            if p and p.poll() is not None:
                run["restarts"] += 1
                run["last_error"] = f"The {name} part stopped unexpectedly (code {p.returncode}) and was restarted automatically. Details: Logs."
                spawn(name, mod)


def first_admin():
    db.init()  # also restores the newest good backup if the database is damaged
    c = db.connect()
    try:
        if not db.one(c, "SELECT 1 AS x FROM users WHERE kind='admin'"):
            from app.security import hash_password
            pw = secrets.token_urlsafe(9)
            c.execute("INSERT INTO users(username,name,password_hash,kind,role,can_approve,must_change_pw,created_at) VALUES('admin','Super Admin',?,'admin','super_admin',1,1,?)",
                      (hash_password(pw), db.now()))
            S["first_password"] = pw
            save()
    finally:
        c.close()


def admin_route():
    c = db.connect()
    try:
        return (config.ADMIN_PATH or db.setting(c, "admin_path", "")).strip("/")
    finally:
        c.close()


def base_url():
    ip = S["bind"] if S["bind"] not in ("0.0.0.0", "127.0.0.1") else (lan_ips() or ["127.0.0.1"])[0]
    return f"http://{ip}:{S['port']}"


def status():
    from app.api_admin import system_status
    c = db.connect()
    try:
        sysst = system_status(c)
        sched = db.jl(db.setting(c, "schedule", ""), {}) or {}
        online = db.one(c, "SELECT COUNT(DISTINCT user_id) AS n FROM sessions WHERE expires_at>? AND created_at>?", db.now(), db.now() - 3600)["n"]
        live = db.all_(c, "SELECT title FROM competitions WHERE status IN ('live','paused')")
    finally:
        c.close()
    open_, nxt = hostinfo.schedule_state(sched)
    up = {n: p.poll() is None for n, p in procs.items()}
    return {"running": bool(procs) and all(up.values()), "wanted": run["wanted"], "uptime": int(time.time() - run["started_at"]) if run["started_at"] else 0,
            "restarts": run["restarts"], "last_error": run["last_error"], "job": run["job"], "settings": S, "specs": hostinfo.specs(), "usage": hostinfo.usage(),
            "ips": lan_ips(), "student_url": base_url() + "/", "admin_url": f"{base_url()}/{admin_route()}/",
            "schedule": sched, "open_now": open_ or bool(live), "next_open": nxt.strftime("%a %d %b %I:%M %p") if nxt else None,
            "live": [x["title"] for x in live], "signed_in_last_hour": online, "alerts": sysst["alerts"], "autostart": autostart_on(), "is_root": IS_ROOT}


# ---------- system integration (Ubuntu) ----------
def autostart_on():
    return sh("systemctl", "is-enabled", SERVICE)[1] == "enabled" if shutil.which("systemctl") else None


def set_autostart(on):
    if not IS_ROOT:
        return "Run the installer (sudo ./ubuntu/install.sh) to start ASTRA Arena at boot."
    code, out = sh("systemctl", "enable" if on else "disable", SERVICE)
    return "ok" if code == 0 else out


def firewall():
    """Allow students on the LAN to reach the port (ufw). Student programs never get network: each runs in its own empty network namespace."""
    if not shutil.which("ufw"):
        return "No ufw firewall on this machine: nothing to open."
    if not IS_ROOT:
        return f"Run: sudo ufw allow {S['port']}/tcp"
    code, out = sh("ufw", "allow", f"{S['port']}/tcp")
    return "ok" if code == 0 else out


def install_langs():
    missing = [APT[k] for k, ok in judge.available_languages().items() if not ok and k in APT and k not in ("go", "rust")]
    if not missing:
        return "All languages are installed."
    if not IS_ROOT:
        return "Run: sudo apt-get install -y " + " ".join(missing)

    def job():
        run["job"] = "Installing " + ", ".join(missing) + " (needs internet, a few minutes)..."
        code, out = sh("apt-get", "install", "-y", *missing, timeout=1800)
        run["job"] = "Languages installed. Restart to use them." if code == 0 else "Install failed (is the internet connected?): " + out[-300:]
    threading.Thread(target=job, daemon=True).start()
    return "Installing in the background; this card updates when it is done."


def diagnose():
    """Every check returns ok/warn/err, what it means, and the fix (a button action where one exists)."""
    out = []

    def add(name, level, detail, fix="", action=None):
        out.append({"name": name, "level": level, "detail": detail, "fix": fix, "action": action})
    up = run["wanted"] and procs and all(p.poll() is None for p in procs.values())
    add("Platform running", "ok" if up else "err", "Web server and judge are running." if up else "The platform is not running.", "" if up else "Press Run.", None if up else "start")
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{S['port']}/api/health", timeout=3).read()
        add("Web server answers", "ok", f"Answers on port {S['port']}.")
    except Exception as e:
        busy = not port_free(S["port"], S["bind"]) and not procs
        who = sh("ss", "-ltnp", f"sport = :{S['port']}")[1] if busy else ""
        add("Web server answers", "err", f"No answer on port {S['port']} ({e.__class__.__name__}).",
            f"Another program holds the port: {who[-200:]}. Choose another port under Network." if busy else "Press Restart. If it keeps failing, open Logs.", None if busy else "restart")
    c = db.connect()
    try:
        from app.api_admin import system_status
        st = system_status(c)
        last_b = int(db.setting(c, "backup:last_at", "0") or 0)
    finally:
        c.close()
    add("Judge workers", "ok" if st["workers_alive"] else "err", f"{st['workers_alive']} worker(s) alive, {st['queue']['queued']} submission(s) waiting.",
        "" if st["workers_alive"] else "Press Restart.", None if st["workers_alive"] else "restart")
    langs = judge.available_languages()
    miss = [judge.LANGS[k]["name"] for k, ok in langs.items() if not ok and k in ("c", "cpp", "python", "java", "javascript")]
    add("Programming languages", "warn" if miss else "ok", ("Missing: " + ", ".join(miss)) if miss else "C, C++, Python, Java and JavaScript are ready.",
        "Install them (needs internet once)." if miss else "", "install_langs" if miss else None)
    add("Prompt challenges (AI)", "ok" if langs.get("prompt") else "warn",
        "AI model configured." if langs.get("prompt") else "No AI model configured, so Prompt challenges are unavailable (everything else works).",
        "" if langs.get("prompt") else "sudo ./ubuntu/install.sh --with-ai")
    add("Student code sandbox", "ok" if judge.IS_ROOT or IS_ROOT else "warn", judge.sandbox_mode(),
        "" if IS_ROOT else "Run as the installed service (sudo systemctl start astra-arena) so each student program gets its own user and no network.")
    healthy = db._healthy(config.DB_PATH)
    add("Database", "ok" if healthy else "err", "Database checked: no damage." if healthy else "Database looks damaged.",
        "" if healthy else "Press Restart: a damaged database is set aside and the newest good backup is restored automatically.", None if healthy else "restart")
    age_h = (time.time() - last_b) / 3600 if last_b else None
    add("Backups", "ok" if age_h is not None and age_h < 26 else "warn", f"Last backup {age_h:.0f} h ago in {config.BACKUP_DIR}." if age_h is not None else "No backup yet.",
        "" if age_h is not None and age_h < 26 else "Back up now.", None if age_h is not None and age_h < 26 else "backup")
    u = hostinfo.usage()
    add("Disk space", "ok" if u["disk_free_gb"] >= 2 else "err", f"{u['disk_free_gb']} GB free.", "" if u["disk_free_gb"] >= 2 else "Free space: delete old files or old backups.")
    add("Memory", "warn" if (u["ram_pct"] or 0) > 90 else "ok", f"{u['ram_pct']}% used." if u["ram_pct"] is not None else "Unknown.",
        "Close other programs, or lower Judge workers under Network." if (u["ram_pct"] or 0) > 90 else "")
    ips = lan_ips()
    add("Network (LAN)", "ok" if ips else "err", ("This computer is at " + ", ".join(ips)) if ips else "Not connected to any network.",
        "" if ips else "Plug in the LAN cable or join the campus Wi-Fi, then press Restart.")
    if shutil.which("ufw"):
        code, fw = sh("ufw", "status")
        blocked = "Status: active" in fw and f"{S['port']}/tcp" not in fw and str(S["port"]) not in fw
        add("Firewall", "err" if blocked else "ok", f"ufw is blocking port {S['port']}." if blocked else "Students can reach the port.",
            "Open the port." if blocked else "", "firewall" if blocked else None)
    yr = time.localtime().tm_year
    add("Clock", "ok" if 2025 <= yr <= 2100 else "err", time.strftime("%a %d %b %Y %H:%M"), "" if 2025 <= yr <= 2100 else "Fix the date and time: competition timings depend on it.")
    return out


# ---------- panel ----------
class Panel(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        path = urlparse(self.path).path
        if self.headers.get("Host", "").split(":")[0] not in ("127.0.0.1", "localhost"):  # blocks DNS-rebinding tricks
            return self.send(403, "{}")
        if path == "/":
            return self.send(200, (ROOT / "launcher.html").read_text().replace("__TOKEN__", TOKEN), "text/html; charset=utf-8")
        if path == "/api/status":
            return self.send(200, json.dumps(status()))
        if path == "/api/diagnose":
            return self.send(200, json.dumps(diagnose()))
        if path == "/api/guide":
            return self.send(200, (ROOT / "docs" / "UBUNTU.md").read_text(), "text/plain; charset=utf-8")
        if path == "/api/logs":
            tail = lambda n: (LOGS / f"{n}.log").read_bytes()[-8000:].decode("utf-8", "replace") if (LOGS / f"{n}.log").exists() else ""
            return self.send(200, json.dumps({"web": tail("web"), "judge": tail("judge")}))
        if path.startswith("/img/"):
            f = ROOT / "static" / "img" / Path(path).name
            if f.is_file():
                return self.send(200, f.read_bytes(), "image/svg+xml" if f.suffix == ".svg" else "image/png")
        self.send(404, "{}")

    def do_POST(self):
        if self.headers.get("X-Token") != TOKEN:  # other web pages cannot drive the panel
            return self.send(403, '{"error":"forbidden"}')
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            return self.send(400, '{"error":"bad request"}')
        path, msg = urlparse(self.path).path, "ok"
        if path == "/api/start":
            start()
        elif path == "/api/stop":
            stop()
        elif path == "/api/restart":
            stop(); start()
        elif path == "/api/settings":
            try:
                if "port" in body:
                    p = int(body["port"])
                    if not 1024 <= p <= 65535:
                        raise ValueError
                    S["port"] = p
                if "workers" in body:
                    S["workers"] = max(1, min(8, int(body["workers"]))) if str(body["workers"]).strip() else None
            except ValueError:
                return self.send(400, '{"error":"Port must be 1024-65535 and workers 1-8."}')
            for k in ("bind", "backup_mirror"):
                if k in body:
                    S[k] = str(body[k]).strip()
            if "run_on_open" in body:
                S["run_on_open"] = bool(body["run_on_open"])
            save()
            if "port" in body and IS_ROOT:
                firewall()
            if procs:
                stop(); start()
        elif path == "/api/schedule":
            c = db.connect()
            db.set_setting(c, "schedule", json.dumps(body))
            c.close()
        elif path == "/api/autostart":
            msg = set_autostart(bool(body.get("on")))
        elif path == "/api/firewall":
            msg = firewall()
        elif path == "/api/install_langs":
            msg = install_langs()
        elif path == "/api/forget-password":
            S["first_password"] = None
            save()
        elif path == "/api/backup":
            from app.worker import backup
            c = db.connect()
            msg = "Backup saved: " + (backup(c, "manual") or "")
            c.close()
        elif path == "/api/quit":
            stop()
            threading.Timer(0.5, lambda: os._exit(0)).start()
        else:
            return self.send(404, "{}")
        self.send(200, json.dumps({"ok": True, "message": msg, "error": run["last_error"]}))


def main():
    bg = "--background" in sys.argv
    try:
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", PANEL_PORT), Panel)
    except OSError:  # already running (e.g. as the service): just show it
        if not bg:
            webbrowser.open(f"http://127.0.0.1:{PANEL_PORT}/")
        return
    first_admin()
    signal.signal(signal.SIGTERM, lambda *_: (stop(remember=False), os._exit(0)))  # systemd stop / shutdown
    threading.Thread(target=supervise, daemon=True).start()
    if S["running"] and (bg or S["run_on_open"]):
        start()
    if not bg:
        threading.Timer(0.8, lambda: webbrowser.open(f"http://127.0.0.1:{PANEL_PORT}/")).start()
    try:
        srv.serve_forever()
    finally:
        stop(remember=False)


if __name__ == "__main__":
    main()
