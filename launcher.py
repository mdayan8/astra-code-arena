"""ASTRA Arena control panel (http://127.0.0.1:8765, this computer only).
Press Run: it checks the machine, starts the web server + judge in the background and restarts them if they crash.
Also: opening hours, address/port, firewall, start-at-boot, backups, Diagnose & fix, and the built-in guide.
    python launcher.py               open the panel (starts it if it is not running)
    python launcher.py --background  run without opening a browser (what the systemd service uses)"""
import http.server
import ipaddress
import json
import os
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

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
SESSIONS, FAILS = {}, {"n": 0, "until": 0}  # panel sign-ins (in memory: a restart signs everyone out)
UPDATE_REPO = os.environ.get("UPDATE_REPO") or "https://github.com/mdayan8/astra-code-arena.git"
UPDATE_BRANCH = os.environ.get("UPDATE_BRANCH") or "main"
UPD_DIR = config.DATA_DIR / "update-src"
UPD_STATUS = config.DATA_DIR / "update-status.json"  # written by install.sh --update (result, rollback)
RESET_F = config.DATA_DIR / "panel.reset"   # created (as root) by "astra-arena reset-panel-password": forget the app password
DEFAULT = {"panel_pw": None, "auto_update": True, "update_token": "", "port": 8000, "bind": "0.0.0.0", "workers": None, "run_on_open": True, "running": True, "backup_mirror": "", "first_password": None, "auto_ip": True, "fixed_ip": None}
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
    os.chmod(tmp, 0o600)  # holds the first admin password and the update token
    tmp.replace(STATE_F)  # atomic: a power cut never leaves a half-written settings file


S = load()
procs = {}
run = {"wanted": False, "started_at": None, "restarts": 0, "last_error": "", "job": "", "update": {}}


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


# ---------- fixed address: the student link never changes, even if DHCP gives this PC a new address ----------
def net_info():
    """-> (device, gateway, [IPv4Interface on that device]) for the default route, or None."""
    code, out = sh("ip", "-4", "route", "show", "default")
    if code or " dev " not in out:
        return None
    first = out.splitlines()[0]
    dev = first.split(" dev ")[1].split()[0]
    gw = first.split(" via ")[1].split()[0] if " via " in first else None
    addrs = [ipaddress.ip_interface(l.split()[3]) for l in sh("ip", "-4", "-o", "addr", "show", "dev", dev)[1].splitlines() if " inet " in l]
    return dev, gw, addrs


def candidates(primary, gw):
    """Addresses near the top of this PC's subnet (DHCP pools usually hand out the low ones first); a /16 or bigger uses this PC's /24."""
    net = primary.network if primary.network.prefixlen >= 24 else ipaddress.ip_network(f"{primary.ip}/24", strict=False)
    hi = int(net.broadcast_address) - 4  # skip the very top, where routers often sit
    skip = {int(primary.ip), int(ipaddress.ip_address(gw)) if gw else -1}
    for n in range(hi, max(int(net.network_address), hi - 100), -1):
        if n not in skip:
            yield ipaddress.ip_address(n)


def ip_free(ip, dev):
    if sh("ping", "-c", "2", "-W", "1", str(ip), timeout=6)[0] == 0:
        return False
    return "lladdr" not in sh("ip", "neigh", "show", str(ip), "dev", dev)[1]  # silent hosts still answer ARP


def reachable(gw, src):
    return gw is None or sh("ping", "-c", "2", "-W", "1", "-I", str(src), gw, timeout=6)[0] == 0


def ensure_fixed_ip():
    """Keep one fixed extra address on the LAN card (DHCP stays on, so nothing else breaks). Returns the address or None."""
    if not (S["auto_ip"] and IS_ROOT and shutil.which("nmcli") and shutil.which("ip")):
        return None
    info = net_info()
    if not info or not info[2]:
        return None
    dev, gw, addrs = info
    have = {str(a.ip) for a in addrs}
    if S["fixed_ip"] in have:
        return S["fixed_ip"]
    con = sh("nmcli", "-g", "GENERAL.CONNECTION", "device", "show", dev)[1].strip()
    if not con:
        return None
    dyn = [a for a in addrs if str(a.ip) != S["fixed_ip"]]
    primary = dyn[0]
    if S["fixed_ip"]:  # moved to another network or lost it: drop the old one
        for a in sh("nmcli", "-g", "ipv4.addresses", "connection", "show", con)[1].replace("\\", "").split(","):
            if a.strip().startswith(S["fixed_ip"] + "/"):
                sh("nmcli", "connection", "modify", con, "-ipv4.addresses", a.strip())
    gw_ok = reachable(gw, primary.ip)
    hint = S.get("fixed_ip_hint")  # the old computer's address after a move: same student link if the network allows it
    first = [ipaddress.ip_address(hint)] if hint and ipaddress.ip_address(hint) in primary.network and str(primary.ip) != hint else []
    for ip in first + list(candidates(primary, gw)):
        if not ip_free(ip, dev):
            continue
        cidr = f"{ip}/{primary.network.prefixlen}"
        sh("nmcli", "connection", "modify", con, "+ipv4.addresses", cidr, "ipv4.dad-timeout", "3000")
        sh("nmcli", "device", "reapply", dev, timeout=30)
        time.sleep(4)
        now = {str(a.ip) for a in (net_info() or (0, 0, []))[2]}
        if str(ip) in now and (not gw_ok or reachable(gw, ip)):
            S["fixed_ip"] = str(ip)
            S.pop("fixed_ip_hint", None)
            save()
            return S["fixed_ip"]
        # someone else had it, or the campus network blocks self-chosen addresses: undo and try the next one
        sh("nmcli", "connection", "modify", con, "-ipv4.addresses", cidr)
        sh("nmcli", "device", "reapply", dev, timeout=30)
        if str(ip) in now:  # assigned but blocked by the network: other addresses will be blocked too
            run["last_error"] = "The campus network does not allow a self-chosen address, so the student link uses this PC's normal address. Ask IT to reserve it (DHCP reservation)."
            break
    S["fixed_ip"] = None
    save()
    return None


def release_fixed_ip():
    """Give the fixed address back to the network (before removing this host, so a new host can take it over)."""
    ip, info = S.get("fixed_ip"), net_info() if shutil.which("ip") else None
    if ip and info and IS_ROOT and shutil.which("nmcli"):
        con = sh("nmcli", "-g", "GENERAL.CONNECTION", "device", "show", info[0])[1].strip()
        for a in sh("nmcli", "-g", "ipv4.addresses", "connection", "show", con)[1].replace("\\", "").split(","):
            if a.strip().startswith(ip + "/"):
                sh("nmcli", "connection", "modify", con, "-ipv4.addresses", a.strip())
        sh("nmcli", "device", "reapply", info[0], timeout=30)
    S["fixed_ip"] = None
    save()
    return ip


# ---------- automatic updates from GitHub ----------
def installed_version():
    try:
        return (ROOT / ".installed-version").read_text().strip()
    except OSError:
        return ""


def git(*args, timeout=120):
    """git with the optional access token for a private repo (sent as a header, never written to disk)."""
    extra = []
    if S.get("update_token"):
        import base64
        cred = base64.b64encode(f"x-access-token:{S['update_token']}".encode()).decode()
        extra = ["-c", f"http.extraHeader=Authorization: Basic {cred}"]
    return sh("git", *extra, *args, timeout=timeout)


def verify_release(repo_dir):
    """Only install commits signed by the release key. The trusted key list comes from the INSTALLED copy, so a download
    (or a hijacked GitHub account) cannot swap in its own key. Key changes ship inside a commit signed by the current key."""
    trusted = ROOT / "ubuntu" / "allowed_signers"
    if not trusted.is_file():
        return False
    code, out = sh("git", "-C", str(repo_dir), "-c", f"gpg.ssh.allowedSignersFile={trusted}", "verify-commit", "HEAD")
    return code == 0 and "Good" in out


def update_safe_now():
    """Never update during a round, or within 60 minutes of one starting."""
    c = db.connect()
    try:
        busy = db.one(c, "SELECT title FROM competitions WHERE status IN ('live','paused') OR (status='published' AND start_at BETWEEN ? AND ?) LIMIT 1",
                      db.now() - 600, db.now() + 3600)
    finally:
        c.close()
    return (False, f"waiting: '{busy['title']}' is running or starts soon") if busy else (True, "")


def check_update(apply=False):
    st = {"checked_at": int(time.time()), "installed": installed_version()[:12]}
    if not shutil.which("git"):
        return {**st, "error": "git is not installed"}
    code, out = git("ls-remote", UPDATE_REPO, f"refs/heads/{UPDATE_BRANCH}", timeout=40)
    if code or not out.strip():
        st["error"] = "Cannot reach GitHub (no internet, or a private repository needs an access token)."
        run["update"] = st
        return st
    remote = out.split()[0]
    st.update(latest=remote[:12], available=remote != installed_version())
    try:
        st["last_result"] = json.loads(UPD_STATUS.read_text())
    except (OSError, ValueError):
        pass
    bad = st.get("last_result", {})
    if apply and st["available"] and bad.get("ok") is False and bad.get("version") == remote:
        st["note"] = f"Version {remote[:12]} failed to start earlier and was rolled back; waiting for a newer version."
    elif apply and st["available"]:
        ok, why = update_safe_now()
        st["note"] = why or apply_update(remote)
    run["update"] = st
    return st


def apply_update(remote):
    if not IS_ROOT:
        return "Updates install only when ASTRA Arena runs as the installed service."
    if UPD_DIR.joinpath(".git").exists():
        code, out = git("-C", str(UPD_DIR), "fetch", "--depth", "1", UPDATE_REPO, UPDATE_BRANCH)
        code = code or git("-C", str(UPD_DIR), "reset", "--hard", "FETCH_HEAD")[0]
    else:
        shutil.rmtree(UPD_DIR, ignore_errors=True)
        code, out = git("clone", "--depth", "1", "-b", UPDATE_BRANCH, UPDATE_REPO, str(UPD_DIR), timeout=600)
    if code:
        return "Download failed: " + out[-200:]
    if sh("git", "-C", str(UPD_DIR), "rev-parse", "HEAD")[1].strip() != remote:
        return "Downloaded version does not match GitHub yet; trying again later."
    if not verify_release(UPD_DIR):
        return "Update REFUSED: it is not signed with the trusted release key. Nothing was changed. If you did not push it, secure the GitHub account."
    from app.worker import backup
    c = db.connect()
    try:
        backup(c, "pre-update")  # a verified copy of the data before anything changes
    finally:
        c.close()
    # runs outside this service: the update restarts the service (and this process)
    sh("systemd-run", "--unit", f"astra-update-{int(time.time())}", "--collect", "/bin/bash", str(UPD_DIR / "ubuntu" / "install.sh"), "--update")
    return "Installing the update now (the platform restarts for a few seconds)."


# ---------- moving to another computer ----------
EXPORTS = config.DATA_DIR / "exports"


def make_package():
    """One zip with the whole platform: database (consistent online copy), uploads and network settings."""
    EXPORTS.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    tmp = EXPORTS / f".move-{stamp}.db"
    src, dst = sqlite3.connect(config.DB_PATH), sqlite3.connect(tmp)
    src.backup(dst)
    dst.close(); src.close()
    if not db._healthy(tmp):
        tmp.unlink(missing_ok=True)
        raise RuntimeError("The database copy failed its check. Run Diagnose & fix, then try again.")
    name = f"astra-move-{stamp}.zip"
    with zipfile.ZipFile(EXPORTS / name, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(tmp, "astra.db")
        for f in config.UPLOAD_DIR.rglob("*"):
            if f.is_file():
                z.write(f, "uploads/" + f.relative_to(config.UPLOAD_DIR).as_posix())
        z.writestr("manifest.json", json.dumps({"kind": "astra-move", "created_at": int(time.time()), "from": socket.gethostname(),
                                                "fixed_ip": S.get("fixed_ip"), "port": S["port"], "auto_ip": S["auto_ip"]}))
    tmp.unlink()
    for old in sorted(EXPORTS.glob("astra-move-*.zip"))[:-3]:  # keep the newest 3
        old.unlink()
    return name


def import_package(path):
    """Replace this computer's data with a move package (a safety backup of the current data is kept first)."""
    stage = config.DATA_DIR / "import-stage"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir()
    try:
        with zipfile.ZipFile(path) as z:
            man = json.loads(z.read("manifest.json"))
            if man.get("kind") != "astra-move":
                raise ValueError("This is not an ASTRA Arena move package.")
            for n in z.namelist():  # only the database and uploads, never paths outside the folder
                parts = Path(n).parts
                if n == "astra.db" or (parts and parts[0] == "uploads" and ".." not in parts and not n.endswith("/") and not Path(n).is_absolute()):
                    z.extract(n, stage)
    except (zipfile.BadZipFile, KeyError, ValueError) as e:
        shutil.rmtree(stage, ignore_errors=True)
        raise ValueError(f"Cannot use this file: {e}") from None
    if not db._healthy(stage / "astra.db"):
        shutil.rmtree(stage, ignore_errors=True)
        raise ValueError("The database inside this package is damaged.")
    was_running = bool(procs)
    stop(remember=False)
    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if db._healthy(config.DB_PATH):
        src, dst = sqlite3.connect(config.DB_PATH), sqlite3.connect(config.BACKUP_DIR / f"astra-before-import-{time.strftime('%Y%m%d-%H%M%S')}.db")
        src.backup(dst)
        dst.close(); src.close()
    for ext in ("-wal", "-shm"):
        Path(str(config.DB_PATH) + ext).unlink(missing_ok=True)
    shutil.move(str(stage / "astra.db"), config.DB_PATH)
    shutil.rmtree(config.UPLOAD_DIR, ignore_errors=True)
    shutil.move(str(stage / "uploads"), config.UPLOAD_DIR) if (stage / "uploads").exists() else config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(stage, ignore_errors=True)
    db.init()  # runs any newer migrations on the imported data
    S.update(port=int(man.get("port") or S["port"]), auto_ip=bool(man.get("auto_ip", True)), fixed_ip_hint=man.get("fixed_ip"), first_password=None)
    save()
    if was_running:
        start()
    return man


def remove_host(purge):
    """Uninstall from this computer. A final move package is always kept (in /var/backups when the data is deleted)."""
    if not IS_ROOT or not Path("/opt/astra-arena/ubuntu/install.sh").exists():
        return "Run in a terminal: sudo /opt/astra-arena/ubuntu/install.sh --uninstall"
    name = make_package()
    keep = EXPORTS / name
    if purge:
        Path("/var/backups").mkdir(exist_ok=True)
        keep = Path(shutil.copy2(keep, "/var/backups/" + name))
    # run outside this service, because uninstalling stops the service (and this process)
    sh("systemd-run", "--unit", f"astra-remove-{int(time.time())}", "--collect", "/bin/bash", "/opt/astra-arena/ubuntu/install.sh", "--uninstall", *(["--purge"] if purge else []))
    return f"Removing ASTRA Arena from this computer. Final move package kept at {keep}"


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
    n = 0
    while True:
        time.sleep(5)
        n += 1
        if n % 360 == 24 and S["auto_update"]:  # 2 min after start, then every 30 min
            try:
                check_update(apply=True)
            except Exception as e:
                run["update"] = {"checked_at": int(time.time()), "error": f"Update check failed: {e}"}
        if n % 60 == 0:  # every 5 minutes: cable moved, new network, address lost
            try:
                ensure_fixed_ip()
            except Exception as e:  # never let the network helper stop the supervisor
                run["last_error"] = f"Fixed address check failed: {e}"
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
    ip = S["bind"] if S["bind"] not in ("0.0.0.0", "127.0.0.1") else (S["fixed_ip"] if S["fixed_ip"] in lan_ips() else (lan_ips() or ["127.0.0.1"])[0])
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
            "restarts": run["restarts"], "last_error": run["last_error"], "job": run["job"], "settings": {k: v for k, v in S.items() if k not in ("update_token", "panel_pw")}, "admins": admin_names(), "shared": S["bind"] != "127.0.0.1", "specs": hostinfo.specs(), "usage": hostinfo.usage(),
            "ips": lan_ips(), "student_url": base_url() + "/", "admin_url": f"{base_url()}/{admin_route()}/",
            "schedule": sched, "open_now": open_ or bool(live), "next_open": nxt.strftime("%a %d %b %I:%M %p") if nxt else None,
            "live": [x["title"] for x in live], "signed_in_last_hour": online, "alerts": sysst["alerts"], "autostart": autostart_on(), "is_root": IS_ROOT,
            "update": {**run["update"], "auto": S["auto_update"], "token_set": bool(S.get("update_token")), "version": installed_version()[:12]}}


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
        "" if langs.get("prompt") else "Install the AI model (about 1 GB download, uses about 1.5 GB RAM).", None if langs.get("prompt") else "install_ai")
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
    if S["auto_ip"]:
        fixed = S["fixed_ip"] in ips
        add("Fixed address", "ok" if fixed else "warn", f"The student link stays {base_url()}/ even if this PC's normal address changes." if fixed
            else "No fixed address yet: the student link may change if the network gives this PC a new address.",
            "" if fixed else "Press Set up. If the campus network blocks it, ask IT to reserve this PC's address.", None if fixed else "fixed_ip")
    yr = time.localtime().tm_year
    add("Clock", "ok" if 2025 <= yr <= 2100 else "err", time.strftime("%a %d %b %Y %H:%M"), "" if 2025 <= yr <= 2100 else "Fix the date and time: competition timings depend on it.")
    return out


# ---------- panel ----------
LOGIN_PAGE = """<!doctype html><html><head><meta charset=utf-8><title>ASTRA Code Arena · Sign in</title><link rel=icon href=/img/arena-logo.svg>
<style>body{margin:0;font:15px/1.5 system-ui,Ubuntu,sans-serif;background:radial-gradient(circle at 30% 10%,#efeaff,#f6f6fa 60%);color:#16161d;display:grid;place-items:center;min-height:100vh}
form{background:#fff;border:1px solid #e4e4ee;border-radius:18px;padding:34px;width:380px;box-shadow:0 20px 50px #2a1a6a14;text-align:center}img{width:64px}h1{font-size:20px;margin:12px 0 2px}
p{color:#666a78;margin:0 0 20px;font-size:13.5px}input{width:100%;box-sizing:border-box;font:inherit;padding:11px 12px;border:1px solid #dcdbe8;border-radius:10px;margin-bottom:10px}
input:focus{outline:2px solid #6d4aff33;border-color:#6d4aff}button{width:100%;border:0;border-radius:10px;padding:12px;font:inherit;font-weight:600;background:#6d4aff;color:#fff;cursor:pointer}
#e{color:#c0283a;font-size:13px;min-height:20px;margin-top:10px}a{color:#6d4aff;font-size:13px;cursor:pointer}#help{display:none;text-align:left;background:#f3f2fa;border-radius:10px;padding:12px;font-size:13px;margin-top:12px}
code{background:#fff;padding:1px 5px;border-radius:4px}</style></head><body>
<form id=f><img src=/img/arena-logo.svg alt=""><h1>ASTRA Code Arena</h1><p>__INTRO__</p>
<input name=p type=password placeholder="__PH__" autocomplete="__AC__" minlength=__MIN__ required autofocus>__SECOND__
<button>__BTN__</button><div id=e></div><a id=fg>Forgot the password?</a>
<div id=help><b>Reset it with your Ubuntu password:</b><br>1. Open the app menu, right-click <b>ASTRA Code Arena</b>, choose <b>Reset app password</b>.<br>2. Or in a terminal: <code>astra-arena reset-panel-password</code><br>Then open this app again and create a new password. Nothing else is changed.</div></form>
<script>const MODE='__MODE__';fg.onclick=()=>help.style.display='block';if(MODE==='create')fg.style.display='none';
f.onsubmit=async e=>{e.preventDefault();if(MODE==='create'&&f.p.value!==f.p2.value)return document.getElementById('e').textContent='The two passwords are not the same.';
const r=await fetch(MODE==='create'?'/api/setup-password':'/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:f.p.value})});
if(r.ok)return location.reload();document.getElementById('e').textContent=(await r.json()).error||'Sign-in failed'}</script></body></html>"""


def login_page():
    check_reset()
    create = not S.get("panel_pw")
    t = LOGIN_PAGE.replace("__MODE__", "create" if create else "login")
    t = t.replace("__INTRO__", "Create a password for this control panel. It is only for running the server on this computer; organisers use their own accounts on the organiser site."
                  if create else "Control panel · enter the app password")
    t = t.replace("__PH__", "New password (8 or more characters)" if create else "App password").replace("__AC__", "new-password" if create else "current-password")
    t = t.replace("__MIN__", "8" if create else "1").replace("__BTN__", "Create password and open" if create else "Open control panel")
    return t.replace("__SECOND__", '<input name=p2 type=password placeholder="Type it again" autocomplete="new-password" required>' if create else "")


def check_reset():
    """`astra-arena reset-panel-password` (needs the Ubuntu password) drops this file: forget the app password."""
    if RESET_F.exists():
        RESET_F.unlink(missing_ok=True)
        S["panel_pw"] = None
        SESSIONS.clear()
        save()


def new_session():
    sid = secrets.token_urlsafe(24)
    SESSIONS[sid] = time.time() + 12 * 3600
    return sid


def set_panel_password(pw):
    if len(pw or "") < 8:
        return "Use at least 8 characters."
    from app.security import hash_password
    S["panel_pw"] = hash_password(pw)
    save()
    return None


def panel_login(password):
    """The app's own password (not an organiser account). 5 wrong tries lock sign-in for a minute."""
    check_reset()
    if not S.get("panel_pw"):
        return None, "No password is set yet. Reload this page to create one."
    if time.time() < FAILS["until"]:
        return None, f"Too many wrong passwords. Try again in {int(FAILS['until'] - time.time()) + 1} s."
    from app.security import verify_password
    if verify_password(password or "", S["panel_pw"]):
        FAILS.update(n=0, until=0)
        return new_session(), None
    FAILS["n"] += 1
    if FAILS["n"] >= 5:
        FAILS.update(n=0, until=time.time() + 60)
    return None, "Wrong password."


def reset_admin(username):
    """Organiser forgot their password: give that admin account a temporary one (they must change it at sign-in)."""
    from app.security import hash_password
    c = db.connect()
    try:
        u = db.one(c, "SELECT id FROM users WHERE username=? AND kind='admin'", (username or "").strip())
        if not u:
            return None
        pw = secrets.token_urlsafe(9)
        c.execute("UPDATE users SET password_hash=?, must_change_pw=1, failed_logins=0, locked_until=0, status='active' WHERE id=?", (hash_password(pw), u["id"]))
        c.execute("DELETE FROM sessions WHERE user_id=?", (u["id"],))
        return pw
    finally:
        c.close()


def admin_names():
    c = db.connect()
    try:
        return [r["username"] for r in db.all_(c, "SELECT username FROM users WHERE kind='admin' ORDER BY role='super_admin' DESC, username")]
    finally:
        c.close()


# ---------- one button: students on the college network can connect, or only this computer ----------
def lan_test():
    """Does the student site answer on this PC's network addresses? (A firewall between buildings can still block it.)"""
    out = []
    for ip in lan_ips():
        try:
            urllib.request.urlopen(f"http://{ip}:{S['port']}/api/health", timeout=3).read()
            out.append({"ip": ip, "ok": True})
        except Exception:
            out.append({"ip": ip, "ok": False})
    return out


def share(on):
    S["bind"] = "0.0.0.0" if on else "127.0.0.1"
    save()
    if on:
        fw = firewall()
        try:
            ensure_fixed_ip()
        except Exception:
            pass
    elif IS_ROOT and shutil.which("ufw"):
        sh("ufw", "delete", "allow", f"{S['port']}/tcp")
    stop(remember=False)
    start()
    if not on:
        return "Closed to the network: only this computer can open the student site now."
    for _ in range(15):  # wait for the web server, then look from the network side
        time.sleep(1)
        res = lan_test()
        if res and all(r["ok"] for r in res):
            break
    good = [r["ip"] for r in lan_test() if r["ok"]]
    if not lan_ips():
        return "Sharing is on, but this computer is not connected to any network. Plug in the LAN cable."
    if not good:
        return "Sharing is on, but the student site is not answering yet. Run Diagnose & fix."
    note = "" if fw in ("ok", "No ufw firewall on this machine: nothing to open.") else f" Firewall: {fw}"
    return f"Open to the college network. Students on the LAN open {base_url()}/ . Test it once from another PC.{note}"


def install_ai():
    """One click: install the AI model in the background (outside this service, because it restarts the service at the end)."""
    if not IS_ROOT or not Path("/opt/astra-arena/ubuntu/install.sh").exists():
        return "Run in a terminal: sudo ./ubuntu/install.sh --with-ai"
    log = LOGS / "ai-install.log"
    log.write_text("")
    sh("systemd-run", "--unit", f"astra-ai-{int(time.time())}", "--collect", "/bin/bash", "-c", f"/bin/bash /opt/astra-arena/ubuntu/install.sh --ai-only > {log} 2>&1")

    def watch():
        for _ in range(1800):
            time.sleep(2)
            lines = [l for l in log.read_text(errors="replace").replace("\r", "\n").splitlines() if l.strip()]
            if not lines:
                continue
            if lines[-1].startswith("##AI-DONE"):
                run["job"] = "AI model installed. Restarting..." if "ok" in lines[-1] else "The AI model could not be installed (slow or blocked download). Everything else works. Try again later."
                return
            run["job"] = "Installing the AI model (about 1 GB, a few minutes): " + lines[-1][-110:]
    run["job"] = "Installing the AI model..."
    threading.Thread(target=watch, daemon=True).start()
    return "Installing the AI model in the background. This page shows the progress."


class Panel(http.server.BaseHTTPRequestHandler):
    def authed(self):
        if self.headers.get("X-Token") == TOKEN:  # local tools (astra-arena doctor) read the token from a root-only file
            return True
        sid = next((x.split("=", 1)[1] for x in self.headers.get("Cookie", "").split("; ") if x.startswith("astra_panel=")), "")
        return SESSIONS.get(sid, 0) > time.time()

    def same_origin(self):
        o = self.headers.get("Origin")
        return self.headers.get("Host", "").split(":")[0] in ("127.0.0.1", "localhost") and (not o or o in (f"http://127.0.0.1:{PANEL_PORT}", f"http://localhost:{PANEL_PORT}"))

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
        if path == "/api/ping":
            return self.send(200, json.dumps({"ok": True, "version": installed_version()}))
        if path.startswith("/img/"):
            f = ROOT / "static" / "img" / Path(path).name
            return self.send(200, f.read_bytes(), "image/svg+xml" if f.suffix == ".svg" else "image/png") if f.is_file() else self.send(404, "{}")
        if not self.authed():
            return self.send(200, login_page(), "text/html; charset=utf-8") if path == "/" else self.send(401, '{"error":"Sign in first."}')
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
        if path.startswith("/api/package/"):  # download a move package (token required: it holds all data)
            f = EXPORTS / Path(path).name
            if parse_qs(urlparse(self.path).query).get("t", [""])[0] != TOKEN or not f.is_file() or not f.name.startswith("astra-move-"):
                return self.send(403, "{}")
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{f.name}"')
            self.send_header("Content-Length", str(f.stat().st_size))
            self.end_headers()
            with open(f, "rb") as fh:
                shutil.copyfileobj(fh, self.wfile)
            return
        if path.startswith("/img/"):
            f = ROOT / "static" / "img" / Path(path).name
            if f.is_file():
                return self.send(200, f.read_bytes(), "image/svg+xml" if f.suffix == ".svg" else "image/png")
        self.send(404, "{}")

    def do_POST(self):
        if not self.same_origin():  # other web pages cannot drive the panel
            return self.send(403, '{"error":"forbidden"}')
        if urlparse(self.path).path == "/api/setup-password":  # first open only: nobody has a password yet
            check_reset()
            b = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 4096)) or b"{}") if "application/json" in self.headers.get("Content-Type", "") else {}
            err = "A password already exists. Sign in instead." if S.get("panel_pw") else set_panel_password(b.get("password"))
            if err:
                return self.send(400, json.dumps({"error": err}))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Set-Cookie", f"astra_panel={new_session()}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200")
            self.end_headers()
            return self.wfile.write(b'{"ok":true}')
        if urlparse(self.path).path == "/api/login":
            if "application/json" not in self.headers.get("Content-Type", ""):
                return self.send(400, "{}")
            b = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 4096)) or b"{}")
            sid, err = panel_login(b.get("password"))
            if not sid:
                return self.send(401, json.dumps({"error": err}))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Set-Cookie", f"astra_panel={sid}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200")
            self.end_headers()
            return self.wfile.write(b'{"ok":true}')
        if not self.authed() or self.headers.get("X-Token") != TOKEN:
            return self.send(401, '{"error":"Sign in first."}')
        path, msg = urlparse(self.path).path, "ok"
        if path == "/api/import":  # raw zip upload from the panel
            f = config.DATA_DIR / "upload-import.zip"
            left = int(self.headers.get("Content-Length") or 0)
            with open(f, "wb") as out:
                while left > 0:
                    chunk = self.rfile.read(min(left, 1 << 20))
                    if not chunk:
                        break
                    out.write(chunk)
                    left -= len(chunk)
            try:
                man = import_package(f)
                msg = f"Imported data from {man.get('from')}. Sign in with the admin accounts from that computer."
            except ValueError as e:
                return self.send(400, json.dumps({"error": str(e)}))
            finally:
                f.unlink(missing_ok=True)
            return self.send(200, json.dumps({"ok": True, "message": msg, "error": run["last_error"]}))
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            return self.send(400, '{"error":"bad request"}')
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
            if "auto_ip" in body:
                S["auto_ip"] = bool(body["auto_ip"])
            save()
            if S["auto_ip"] and "auto_ip" in body:
                msg = "Fixed address: " + (ensure_fixed_ip() or "not available here, the normal address is used")
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
        elif path == "/api/package":
            try:
                name = make_package()
            except (RuntimeError, sqlite3.Error) as e:
                return self.send(500, json.dumps({"error": str(e)}))
            return self.send(200, json.dumps({"ok": True, "message": "Move package ready", "name": name, "url": f"/api/package/{name}?t={TOKEN}"}))
        elif path == "/api/remove":
            if body.get("confirm") != "REMOVE":
                return self.send(400, '{"error":"Type REMOVE to confirm."}')
            msg = remove_host(bool(body.get("purge")))
        elif path == "/api/update":
            if "auto" in body:
                S["auto_update"] = bool(body["auto"])
            if "token" in body:
                S["update_token"] = str(body["token"]).strip()
            save()
            st = check_update(apply=bool(body.get("apply")))
            msg = st.get("error") or st.get("note") or ("An update is available." if st.get("available") else "You have the latest version.")
        elif path == "/api/change-password":
            from app.security import verify_password
            if not verify_password(str(body.get("current") or ""), S.get("panel_pw") or ""):
                return self.send(400, '{"error":"The current password is wrong."}')
            err = set_panel_password(body.get("new"))
            if err:
                return self.send(400, json.dumps({"error": err}))
            msg = "App password changed."
        elif path == "/api/reset-admin":
            pw = reset_admin(body.get("username"))
            if not pw:
                return self.send(400, '{"error":"No organiser account with that username."}')
            return self.send(200, json.dumps({"ok": True, "message": "Temporary password created", "username": body.get("username"), "password": pw}))
        elif path == "/api/share":
            msg = share(bool(body.get("on")))
        elif path == "/api/install_ai":
            msg = install_ai()
        elif path == "/api/logout":
            SESSIONS.clear()
        elif path == "/api/fixed_ip":
            msg = "Fixed address: " + (ensure_fixed_ip() or "not available here, the normal address is used")
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
    if "--import" in sys.argv:  # used by the installer: bring data from the old computer
        man = import_package(sys.argv[sys.argv.index("--import") + 1])
        print(f"Imported data from {man.get('from')} (package of {time.strftime('%d %b %Y %H:%M', time.localtime(man.get('created_at', 0)))})")
        return
    if "--release-ip" in sys.argv:  # used by the uninstaller
        print("Released fixed address:", release_fixed_ip())
        return
    if "--fixed-ip" in sys.argv:  # used by the installer (the service is stopped meanwhile)
        print("Fixed address:", ensure_fixed_ip() or "not set (using this PC's normal address)")
        return
    try:
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", PANEL_PORT), Panel)
    except OSError:  # already running (e.g. as the service): just show it
        if not bg:
            webbrowser.open(f"http://127.0.0.1:{PANEL_PORT}/")
        return
    first_admin()
    tf = config.DATA_DIR / "panel.token"
    tf.write_text(TOKEN)
    os.chmod(tf, 0o600)  # lets `sudo astra-arena doctor` talk to the panel
    threading.Thread(target=ensure_fixed_ip, daemon=True).start()
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
