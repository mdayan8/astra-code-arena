#!/usr/bin/env python3
"""ASTRA remote watch: alerts (ntfy) + a tiny read-only status page for the ASTRA Code Arena server.

Standalone, Python 3.10 stdlib only. Reads the platform DB read-only (?mode=ro) and pings its HTTP API.
Never writes to the platform. No student names / USNs / emails leave the machine.

  python3 watch.py                 # alert loop only
  python3 watch.py --serve         # alert loop + dashboard on 127.0.0.1:8790
  python3 watch.py --serve --tunnel  # + Cloudflare quick tunnel, link sent to ntfy
  python3 watch.py --test-alert    # send one test notification
  python3 watch.py --selftest      # offline self-test (temp DB, fake sender)
  python3 watch.py --info          # print subscribe link + dashboard URL
"""
import argparse
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0
# As root (the systemd service) config + state live outside the app folder: platform updates rsync --delete /opt/astra-arena.
CONFIG_F = Path("/etc/astra-watch.json") if IS_ROOT else HERE / "config.json"
STATE_F = Path("/var/lib/astra-watch/state.json") if IS_ROOT else HERE / "state.json"

RATE_SEC = 300          # at most 1 alert per kind per 5 min
LIVE = ("live", "paused")
LIVE_SUMMARY_SEC, IDLE_SUMMARY_SEC = 15 * 60, 3 * 3600

USN_RE = re.compile(r"\b\d[A-Z]{2}\d{2}[A-Z]{2,4}\d{3}\b", re.I)          # e.g. 4PM23CS001
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")


def scrub(s, n=600):
    s = EMAIL_RE.sub("[email]", USN_RE.sub("[usn]", str(s or "")))
    return s if len(s) <= n else s[:n] + "..."


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---------------- config / state ----------------

def read_env(path="/etc/astra-arena.env"):
    out = {}
    try:
        for line in Path(path).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.split(" #", 1)[0].strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def load_config(path=CONFIG_F):
    try:
        cfg = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        cfg = {}
    changed = not cfg
    env = {**read_env(), **{k: v for k, v in os.environ.items() if k in ("DATA_DIR", "DB_PATH", "APP_PORT")}}
    data = Path(env.get("DATA_DIR", "/var/lib/astra-arena"))
    defaults = {
        "ntfy_url": "https://ntfy.sh",                       # self-hosted ntfy works too
        "topic": "astra-" + secrets.token_urlsafe(24).replace("_", "x").replace("-", "x"),
        "dash_key": secrets.token_urlsafe(24),
        "interval_sec": 30,
        "dash_port": 8790,
        "db_path": env.get("DB_PATH", str(data / "astra.db")),
        "data_dir": str(data),
        "app_url": "",                                       # empty = http://127.0.0.1:<port from launcher.json/APP_PORT>
    }
    for k, v in defaults.items():
        if k not in cfg:
            cfg[k], changed = v, True
    if changed:
        save_json(path, cfg)
    return cfg


def app_url(cfg):
    if cfg.get("app_url"):
        return cfg["app_url"].rstrip("/")
    port = read_env().get("APP_PORT") or os.environ.get("APP_PORT") or 8000
    try:  # the launcher passes its own port to the web server
        port = json.loads((Path(cfg["data_dir"]) / "launcher.json").read_text()).get("port") or port
    except (OSError, ValueError):
        pass
    return f"http://127.0.0.1:{port}"


def save_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def load_state(path=STATE_F):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


# ---------------- alerts ----------------

def ntfy_sender(cfg):
    def send(title, message, priority=3, tags=""):
        def hdr(s):  # HTTP headers must be latin-1; ntfy decodes RFC 2047 encoded-words
            try:
                s.encode("ascii")
                return s
            except UnicodeEncodeError:
                import base64
                return "=?UTF-8?B?" + base64.b64encode(s.encode()).decode() + "?="
        req = urllib.request.Request(f"{cfg['ntfy_url'].rstrip('/')}/{cfg['topic']}", data=message.encode(), method="POST",
                                     headers={"Title": hdr(title), "Priority": str(priority), "Tags": tags})
        urllib.request.urlopen(req, timeout=15).read()
    return send


class Alerter:
    """1 alert per kind per RATE_SEC; extra ones are counted and folded into the next alert. Failed sends retry next loop."""

    def __init__(self, send, clock=time.time):
        self.send, self.clock = send, clock
        self.last, self.held, self.outbox = {}, {}, []

    def alert(self, kind, title, message, priority=3, tags="", force=False):
        now = self.clock()
        if not force and now - self.last.get(kind, -1e18) < RATE_SEC:
            h = self.held.setdefault(kind, {"n": 0})
            h.update(n=h["n"] + 1, title=title, priority=max(priority, h.get("priority", 0)), tags=tags)
            return False
        self.last[kind] = now
        h = self.held.pop(kind, None)
        if h:
            message += f"\n(+{h['n']} more '{kind}' alert(s) collapsed in the last 5 min)"
        self.outbox.append((title, scrub(message, 3000), priority, tags))
        return True

    def flush(self):
        now = self.clock()
        for kind, h in list(self.held.items()):
            if now - self.last.get(kind, 0) >= RATE_SEC:
                self.last[kind] = now
                del self.held[kind]
                self.outbox.append((h["title"] + f" (x{h['n']})", f"{h['n']} more '{kind}' alert(s) in the last 5 min. Latest: {h['title']}",
                                    h["priority"], h["tags"]))
        while self.outbox:
            title, msg, prio, tags = self.outbox[0]
            try:
                self.send(title, msg, prio, tags)
            except Exception as e:  # network down: keep it, retry next loop
                log("send failed, will retry:", e)
                del self.outbox[:-50]  # bounded
                return
            self.outbox.pop(0)


# ---------------- reading the platform ----------------

def connect_ro(path):
    c = sqlite3.connect("file:" + urllib.parse.quote(str(path)) + "?mode=ro", uri=True, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=10000")
    return c


def q1(c, sql, *a):
    return c.execute(sql, a).fetchone()[0]


def snapshot(c, window):
    now = int(time.time())
    since = now - window
    live = [dict(r) for r in c.execute("SELECT id, code, title, status FROM competitions WHERE status IN ('live','paused') ORDER BY id")]
    for x in live:
        x["participants"] = q1(c, "SELECT COUNT(*) FROM competition_participants WHERE competition_id=?", x["id"])
        x["submissions"] = q1(c, "SELECT COUNT(*) FROM submissions WHERE competition_id=? AND kind='submit'", x["id"])
        x["finished"] = q1(c, "SELECT COUNT(*) FROM participant_finish WHERE competition_id=?", x["id"]) if has_table(c, "participant_finish") else 0
    verdicts = {r[0] or "pending": r[1] for r in c.execute(
        "SELECT verdict, COUNT(*) FROM submissions WHERE kind='submit' AND created_at>=? GROUP BY verdict ORDER BY 2 DESC", (since,))}
    per_min = dict(c.execute("SELECT (created_at-?)/60, COUNT(*) FROM submissions WHERE created_at>=? GROUP BY 1", (now - 3600, now - 3600)).fetchall())
    return {
        "at": now, "window_min": window // 60,
        "live": live,
        "participants": sum(x["participants"] for x in live),
        "submissions_15m": q1(c, "SELECT COUNT(*) FROM submissions WHERE kind='submit' AND created_at>=?", now - 900),
        "submissions_window": sum(verdicts.values()),
        "verdicts": verdicts,
        "violations": q1(c, "SELECT COUNT(*) FROM activity_events WHERE type='violation' AND created_at>=?", since),
        "errors": q1(c, "SELECT COUNT(*) FROM error_log WHERE created_at>=?", since),
        "queue": q1(c, "SELECT COUNT(*) FROM submissions WHERE status='queued'"),
        "running": q1(c, "SELECT COUNT(*) FROM submissions WHERE status='running'"),
        "oldest_queued_sec": now - (q1(c, "SELECT MIN(created_at) FROM submissions WHERE status='queued'") or now),
        "per_min": [per_min.get(i, 0) for i in range(60)],
        "last_errors": [{"at": r[0], "source": r[1], "message": scrub(r[2])}
                        for r in c.execute("SELECT created_at, source, message FROM error_log ORDER BY id DESC LIMIT 20")],
    }


def has_table(c, name):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def sys_metrics(data_dir):
    m = {}
    try:
        st = os.statvfs(data_dir if Path(data_dir).exists() else "/")
        m["disk_free_gb"] = round(st.f_bavail * st.f_frsize / 1e9, 2)
    except OSError:
        pass
    try:
        mi = {l.split(":")[0]: int(l.split()[1]) for l in Path("/proc/meminfo").read_text().splitlines()}
        m["ram_used_pct"] = round(100 * (1 - mi["MemAvailable"] / mi["MemTotal"]), 1)
    except (OSError, KeyError, ValueError):
        pass
    try:
        m["load1"], m["cores"] = round(os.getloadavg()[0], 2), os.cpu_count() or 1
    except OSError:
        pass
    return m


def summary_text(s, m):
    lines = [f"Live rounds: {len(s['live'])}"]
    lines += [f"- {x['title']} [{x['code']}] {x['status']}: {x['participants']} participants, {x['submissions']} submissions, {x['finished']} finished"
              for x in s["live"]]
    lines += [f"Submissions last 15 min: {s['submissions_15m']}",
              f"Verdicts (last {s['window_min']} min): " + (", ".join(f"{k} {v}" for k, v in s["verdicts"].items()) or "none"),
              f"Warnings (violations): {s['violations']}   Errors: {s['errors']}",
              f"Judge queue: {s['queue']} queued, {s['running']} running",
              "System: " + ", ".join(f"{k} {v}" for k, v in m.items())]
    return "\n".join(lines)


# ---------------- the checks ----------------

class Watcher:
    def __init__(self, cfg, alerter, state, state_path=STATE_F, http_get=None, systemctl=None):
        self.cfg, self.a, self.st, self.state_path = cfg, alerter, state, state_path
        self.http_get = http_get or (lambda url: urllib.request.urlopen(url, timeout=5).read())
        self.systemctl = systemctl if systemctl is not None else bool(shutil.which("systemctl"))

    def run_once(self):
        c = None
        try:
            c = connect_ro(self.cfg["db_path"])
        except Exception as e:
            self.flag("db", True, "Cannot read platform DB", f"{self.cfg['db_path']}: {e}", 4, "warning")
        checks = [self.check_server, self.check_systemd, self.check_system]
        if c:
            self.flag("db", False, "Platform DB readable again", "", 3, "white_check_mark")
            checks = [self.check_errors, self.check_ie, self.check_queue, self.check_competitions, self.check_summary] + checks
        for chk in checks:
            try:
                chk(c) if chk.__code__.co_argcount > 1 else chk()
            except Exception:
                log("check failed:", chk.__name__, traceback.format_exc(limit=2))
        if c:
            c.close()
        try:
            self.a.flush()
        except Exception:
            log("flush failed", traceback.format_exc(limit=2))
        try:
            save_json(self.state_path, self.st)
        except Exception as e:
            log("state save failed:", e)

    def flag(self, name, bad, title, msg, prio, tags):
        """Transition alerts: one when a condition turns bad, one when it recovers."""
        was = self.st.setdefault("flags", {}).get(name, False)
        if bad != was:
            self.st["flags"][name] = bad
            self.a.alert(name, title, msg or title, prio if bad else 3, tags if bad else "white_check_mark", force=not bad)

    def check_errors(self, c):
        top = q1(c, "SELECT COALESCE(MAX(id),0) FROM error_log")
        last = self.st.get("last_error_id")
        if last is None or top < last:  # first run (no history spam) or log was reset
            self.st["last_error_id"] = top
            return
        rows = c.execute("SELECT id, source, message FROM error_log WHERE id>? ORDER BY id LIMIT 200", (last,)).fetchall()
        if not rows:
            return
        self.st["last_error_id"] = rows[-1]["id"]
        groups = {}
        for r in rows:
            groups.setdefault(r["source"] or "?", []).append(r["message"])
        body = "\n\n".join(f"[{src}] x{len(ms)}: {scrub(ms[-1])}" for src, ms in groups.items())
        crash = any(s in groups for s in ("web", "worker", "database"))
        self.a.alert("error", f"{len(rows)} new platform error(s): " + ", ".join(groups), body, 5 if crash else 4, "rotating_light" if crash else "warning")

    def check_ie(self, c):
        # IE is set when judging ends, so an old submission id can turn IE later: look at new ids AND recently judged rows.
        last = self.st.get("last_ie_id")
        rows = c.execute("SELECT s.id, s.compile_output, c.code FROM submissions s LEFT JOIN competitions c ON c.id=s.competition_id "
                         "WHERE s.verdict='IE' AND (s.id>? OR s.judged_at>=?) ORDER BY s.id", (last or 0, int(time.time()) - 600)).fetchall()
        seen = set(self.st.get("ie_seen", []))
        new = [r for r in rows if r["id"] not in seen]
        self.st["ie_seen"] = sorted(seen | {r["id"] for r in rows})[-500:]
        self.st["last_ie_id"] = max([last or 0] + [r["id"] for r in rows])
        if last is None or not new:  # first run: baseline only
            return
        comps = sorted({r["code"] or "practice" for r in new})
        self.a.alert("ie", f"Judge internal error (IE) on {len(new)} submission(s)",
                     f"Competitions: {', '.join(comps)}\nLatest: {scrub(new[-1]['compile_output'])}", 4, "warning")

    def check_queue(self, c):
        now = int(time.time())
        n = q1(c, "SELECT COUNT(*) FROM submissions WHERE status='queued'")
        stuck = q1(c, "SELECT COUNT(*) FROM submissions WHERE status='queued' AND created_at<?", now - 60)
        oldest = now - (q1(c, "SELECT MIN(created_at) FROM submissions WHERE status='queued'") or now)
        self.flag("backlog", stuck > 0 or n > 50, "Judge backlog" if stuck or n > 50 else "Judge backlog cleared",
                  f"{n} queued, {stuck} waiting > 60 s (oldest {oldest} s). Is the worker running?", 4, "hourglass")

    def check_competitions(self, c):
        prev = self.st.get("comp_status")
        cur = {str(r["id"]): r["status"] for r in c.execute("SELECT id, status FROM competitions")}
        self.st["comp_status"] = cur
        if prev is None:
            return
        for cid, s in cur.items():
            old = prev.get(cid)
            if old == s or (old is None and s not in LIVE):
                continue
            r = c.execute("SELECT code, title FROM competitions WHERE id=?", (int(cid),)).fetchone()
            parts = q1(c, "SELECT COUNT(*) FROM competition_participants WHERE competition_id=?", int(cid))
            subs = q1(c, "SELECT COUNT(*) FROM submissions WHERE competition_id=? AND kind='submit'", int(cid))
            word = {"live": "started" if old != "paused" else "resumed", "paused": "paused", "ended": "ended"}.get(s, f"is now {s}")
            if s in LIVE or old in LIVE or s == "ended":
                self.a.alert(f"comp:{cid}:{s}", f"{r['title']} [{r['code']}] {word}",
                             f"{old or 'new'} -> {s}. {parts} participants, {subs} submissions.", 3, "checkered_flag" if s == "ended" else "rocket")

    def check_summary(self, c):
        live = q1(c, "SELECT COUNT(*) FROM competitions WHERE status IN ('live','paused')")
        every = LIVE_SUMMARY_SEC if live else IDLE_SUMMARY_SEC
        now = time.time()
        if now - self.st.get("last_summary", 0) < every:
            return
        self.st["last_summary"] = now
        s = snapshot(c, every)
        self.a.alert("summary", f"ASTRA summary: {live} live round(s)", summary_text(s, sys_metrics(self.cfg["data_dir"])), 2, "bar_chart", force=True)

    def check_server(self):
        url = app_url(self.cfg) + "/api/public/info"
        try:
            self.http_get(url)
            self.st["http_fail"] = 0
        except Exception as e:
            self.st["http_fail"] = self.st.get("http_fail", 0) + 1
            self.st["http_err"] = str(e)[:200]
        down = self.st["http_fail"] >= 2
        self.flag("server", down, "Web server DOWN" if down else "Web server back up",
                  f"{url} not answering ({self.st.get('http_err', '')})", 5, "rotating_light")

    def check_systemd(self):
        if not self.systemctl:
            return
        r = subprocess.run(["systemctl", "is-active", "astra-arena"], capture_output=True, text=True, timeout=10)
        state = r.stdout.strip() or "unknown"
        self.flag("systemd", state != "active", f"astra-arena service is {state}" if state != "active" else "astra-arena service active again",
                  f"systemctl is-active astra-arena -> {state}", 5, "rotating_light")

    def check_system(self):
        m = sys_metrics(self.cfg["data_dir"])
        if "disk_free_gb" in m:
            self.flag("disk", m["disk_free_gb"] < 2, "Disk almost full", f"{m['disk_free_gb']} GB free on the data disk", 4, "floppy_disk")
        if "ram_used_pct" in m:
            self.flag("ram", m["ram_used_pct"] > 90, "RAM above 90%", f"RAM used {m['ram_used_pct']}%", 4, "warning")
        if "load1" in m:
            self.flag("load", m["load1"] > m["cores"] * 2, "CPU overloaded", f"load {m['load1']} on {m['cores']} cores", 4, "warning")


# ---------------- dashboard ----------------

PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>ASTRA Watch</title><style>
:root{--bg:#f6f7f9;--card:#fff;--fg:#1b1f24;--mute:#667085;--line:#e4e7ec;--acc:#2563eb;--bad:#d92d20;--ok:#079455}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#181b21;--fg:#e6e8eb;--mute:#98a2b3;--line:#2a2f37;--acc:#60a5fa;--bad:#f97066;--ok:#47cd89}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:900px;margin:0 auto;padding:16px}h1{font-size:20px;margin:4px 0 12px}h2{font-size:14px;color:var(--mute);margin:0 0 8px;font-weight:600}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-bottom:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;margin-bottom:12px}
.grid .card{margin:0}.n{font-size:24px;font-weight:700}.l{color:var(--mute);font-size:12px}
.bad{color:var(--bad)}.ok{color:var(--ok)}ul{margin:0;padding-left:18px}li{margin:4px 0;overflow-wrap:anywhere}
.err{font:12px/1.4 ui-monospace,Menlo,monospace;white-space:pre-wrap;overflow-wrap:anywhere;border-top:1px solid var(--line);padding:6px 0}
svg{width:100%;height:60px;display:block}small{color:var(--mute)}
</style></head><body><main><h1>ASTRA Watch <small id=t></small></h1><div id=app>Loading...</div></main><script>
const k=new URLSearchParams(location.search).get('k')||'';const e=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function spark(a){const m=Math.max(1,...a),w=300,h=60;const p=a.map((v,i)=>`${(i*w/(a.length-1)).toFixed(1)},${(h-2-v*(h-6)/m).toFixed(1)}`).join(' ');
return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio=none role=img aria-label="submissions per minute, last hour"><polyline points="${p}" fill=none stroke="var(--acc)" stroke-width=2 vector-effect=non-scaling-stroke /></svg><small>last 60 min, peak ${m}/min</small>`}
async function load(){try{const r=await fetch('status.json?k='+encodeURIComponent(k),{cache:'no-store'});if(!r.ok)throw r.status;const s=await r.json();
const c=(n,l,b)=>`<div class=card><div class="n ${b?'bad':''}">${e(n)}</div><div class=l>${l}</div></div>`;const f=s.flags||{};
const bad=Object.keys(f).filter(x=>f[x]);
document.getElementById('app').innerHTML=`<div class="card ${bad.length?'bad':'ok'}"><b>${bad.length?'Problems: '+e(bad.join(', ')):'All checks OK'}</b></div><div class=grid>`+
c(s.live.length,'live rounds')+c(s.participants,'participants')+c(s.submissions_15m,'submissions 15 min')+c(s.queue,'queued',s.oldest_queued_sec>60)+
c(s.violations,'warnings ('+s.window_min+' min)')+c(s.errors,'errors ('+s.window_min+' min)',s.errors>0)+
(s.system.disk_free_gb!=null?c(s.system.disk_free_gb+' GB','disk free',s.system.disk_free_gb<2):'')+(s.system.ram_used_pct!=null?c(s.system.ram_used_pct+'%','RAM used',s.system.ram_used_pct>90):'')+
(s.system.load1!=null?c(s.system.load1,'load / '+s.system.cores+' cores',s.system.load1>s.system.cores*2):'')+`</div>`+
`<div class=card><h2>Submissions per minute</h2>${spark(s.per_min)}</div>`+
`<div class=card><h2>Live rounds</h2>${s.live.length?'<ul>'+s.live.map(x=>`<li><b>${e(x.title)}</b> [${e(x.code)}] ${e(x.status)}: ${x.participants} participants, ${x.submissions} submissions, ${x.finished} finished</li>`).join('')+'</ul>':'<small>none</small>'}</div>`+
`<div class=card><h2>Verdicts (last ${s.window_min} min)</h2>${Object.entries(s.verdicts).map(([a,b])=>e(a)+' '+b).join(' &middot; ')||'<small>none</small>'}</div>`+
`<div class=card><h2>Last 20 errors</h2>${s.last_errors.map(x=>`<div class=err><small>${new Date(x.at*1000).toLocaleString()} &middot; ${e(x.source)}</small>\\n${e(x.message)}</div>`).join('')||'<small>none</small>'}</div>`;
document.getElementById('t').textContent='updated '+new Date().toLocaleTimeString()}catch(x){document.getElementById('t').textContent='cannot load ('+x+')'}}
load();setInterval(load,15000);</script></body></html>"""


def make_server(cfg, state, port=None):
    key = cfg["dash_key"].encode()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urllib.parse.urlsplit(self.path)
            k = urllib.parse.parse_qs(u.query).get("k", [""])[0].encode()
            if not hmac.compare_digest(k, key):
                return self.reply(403, b"forbidden", "text/plain")
            if u.path == "/status.json":
                try:
                    c = connect_ro(cfg["db_path"])
                    try:
                        live = q1(c, "SELECT COUNT(*) FROM competitions WHERE status IN ('live','paused')")
                        s = snapshot(c, LIVE_SUMMARY_SEC if live else IDLE_SUMMARY_SEC)
                    finally:
                        c.close()
                except Exception as e:
                    s = {"error": scrub(e), "live": [], "participants": 0, "submissions_15m": 0, "queue": 0, "oldest_queued_sec": 0,
                         "violations": 0, "errors": 0, "window_min": 0, "verdicts": {}, "per_min": [0] * 60, "last_errors": []}
                s["system"], s["flags"] = sys_metrics(cfg["data_dir"]), dict(state.get("flags", {}))
                return self.reply(200, json.dumps(s).encode(), "application/json")
            if u.path == "/":
                return self.reply(200, PAGE.encode(), "text/html; charset=utf-8")
            self.reply(404, b"not found", "text/plain")

        def reply(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Robots-Tag", "noindex")
            if ctype == "application/json":   # the laptop dashboard (watch/laptop.html) reads this from another origin; the key still guards it
                self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer(("127.0.0.1", cfg["dash_port"] if port is None else port), H)


# ---------------- tunnel ----------------

CLOUDFLARED_HELP = ("cloudflared not found. Install it on Ubuntu with:\n"
                    "  curl -L -o /tmp/cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb"
                    " && sudo dpkg -i /tmp/cloudflared.deb\nContinuing without the tunnel.")


def run_tunnel(cfg, alerter):
    exe = shutil.which("cloudflared")
    if not exe:
        log(CLOUDFLARED_HELP)
        return
    url_re = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

    def loop():
        while True:
            try:
                p = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{cfg['dash_port']}"],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                for line in p.stdout:
                    m = url_re.search(line)
                    if m:
                        link = f"{m.group(0)}/?k={cfg['dash_key']}"
                        log("tunnel up:", m.group(0))
                        alerter.alert("tunnel:" + m.group(0), "ASTRA dashboard link", link, 3, "link", force=True)
                p.wait()
                log("cloudflared exited", p.returncode, "- restarting in 15 s")
            except Exception as e:
                log("tunnel error:", e)
            time.sleep(15)

    threading.Thread(target=loop, daemon=True).start()


# ---------------- self-test ----------------

TEST_SCHEMA = """
CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE competitions(id INTEGER PRIMARY KEY, code TEXT, title TEXT, status TEXT NOT NULL DEFAULT 'draft');
CREATE TABLE competition_participants(competition_id INTEGER, user_id INTEGER, joined_at INTEGER, PRIMARY KEY(competition_id, user_id));
CREATE TABLE submissions(id INTEGER PRIMARY KEY, competition_id INTEGER, user_id INTEGER, question_id INTEGER, kind TEXT, language TEXT,
  status TEXT NOT NULL DEFAULT 'queued', verdict TEXT, compile_output TEXT, created_at INTEGER NOT NULL, started_at INTEGER, judged_at INTEGER);
CREATE TABLE activity_events(id INTEGER PRIMARY KEY, competition_id INTEGER, user_id INTEGER, type TEXT, created_at INTEGER NOT NULL);
CREATE TABLE error_log(id INTEGER PRIMARY KEY, source TEXT, message TEXT, created_at INTEGER NOT NULL);
CREATE TABLE participant_finish(competition_id INTEGER, user_id INTEGER, finished_at INTEGER, PRIMARY KEY(competition_id, user_id));
CREATE TABLE platform_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def selftest():
    import tempfile
    d = Path(tempfile.mkdtemp(prefix="astra-watch-test-"))
    dbp = d / "astra.db"
    w = sqlite3.connect(dbp, isolation_level=None)
    w.execute("PRAGMA journal_mode=WAL")
    w.executescript(TEST_SCHEMA)
    w.execute("INSERT INTO competitions(id,code,title,status) VALUES(1,'R1','Round One','published')")
    w.execute("INSERT INTO error_log(source,message,created_at) VALUES('web','old error',?)", (int(time.time()),))
    cfg = load_config(d / "config.json")
    cfg.update(db_path=str(dbp), data_dir=str(d), app_url="http://127.0.0.1:1")
    assert (os.stat(d / "config.json").st_mode & 0o777) == 0o600, "config must be mode 600"
    assert len(cfg["topic"]) > 30

    sent, clock = [], [1_000_000.0]
    a = Alerter(lambda *x: sent.append(x), clock=lambda: clock[0])
    ok_http = lambda url: b"{}"
    wt = Watcher(cfg, a, {}, d / "state.json", http_get=ok_http, systemctl=False)
    wt.run_once()  # baseline: old error must NOT alert
    assert not [s for s in sent if "error" in s[0].lower()], sent
    sent.clear()

    now = int(time.time())
    w.execute("INSERT INTO error_log(source,message,created_at) VALUES('judge','submission 7: crash for 4PM23CS001 mail ravi.k@pesitm.edu.in',?)", (now,))
    w.execute("INSERT INTO submissions(id,competition_id,user_id,question_id,kind,language,status,verdict,compile_output,created_at,judged_at) "
              "VALUES(1,1,1,1,'submit','c','error','IE','sandbox missing',?,?)", (now, now))
    w.execute("INSERT INTO submissions(id,competition_id,user_id,question_id,kind,language,status,created_at) VALUES(2,1,1,1,'submit','c','queued',?)", (now - 120,))
    w.execute("UPDATE competitions SET status='live' WHERE id=1")
    w.execute("INSERT INTO competition_participants VALUES(1,1,?)", (now,))
    clock[0] += 1
    wt.st["last_summary"] = 0  # pretend the last summary is old
    wt.run_once()
    titles = [s[0] for s in sent]
    body = "\n".join(s[1] for s in sent)
    assert any("new platform error" in t for t in titles), titles
    assert any("IE" in t for t in titles), titles
    assert any("backlog" in t.lower() for t in titles), titles
    assert any("started" in t for t in titles), titles
    assert any(t.startswith("ASTRA summary") for t in titles), titles
    assert "4PM23CS001" not in body and "ravi.k@" not in body and "[usn]" in body and "[email]" in body, body
    assert all(isinstance(s[2], int) for s in sent)

    # repeated error within 5 min -> collapsed, then folded into one later alert
    sent.clear()
    for i in range(3):
        w.execute("INSERT INTO error_log(source,message,created_at) VALUES('web','again',?)", (now,))
        clock[0] += 30
        wt.run_once()
    assert not [s for s in sent if "platform error" in s[0]], sent
    assert a.held["error"]["n"] == 3
    clock[0] += RATE_SEC
    wt.run_once()
    assert [s for s in sent if "(x3)" in s[0]], sent

    # server down needs 2 consecutive failures, then recovers
    sent.clear()
    wt.http_get = lambda url: (_ for _ in ()).throw(OSError("refused"))
    wt.run_once()
    assert not [s for s in sent if "DOWN" in s[0]]
    wt.run_once()
    assert [s for s in sent if "DOWN" in s[0] and s[2] == 5], sent
    wt.http_get = ok_http
    wt.run_once()
    assert [s for s in sent if "back up" in s[0]], sent

    # network failure while sending: kept and retried
    a2 = Alerter(lambda *x: (_ for _ in ()).throw(OSError("offline")))
    a2.alert("x", "t", "m")
    a2.flush()
    assert len(a2.outbox) == 1
    a2.send = lambda *x: None
    a2.flush()
    assert not a2.outbox

    # broken DB path must not crash the loop
    bad = Watcher({**cfg, "db_path": str(d / "nope.db")}, Alerter(lambda *x: None), {}, d / "s2.json", http_get=ok_http, systemctl=False)
    bad.run_once()

    # dashboard
    srv = make_server(cfg, wt.st, port=0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    s = json.loads(urllib.request.urlopen(f"{base}/status.json?k={urllib.parse.quote(cfg['dash_key'])}", timeout=5).read())
    assert s["live"][0]["code"] == "R1" and s["queue"] == 1 and len(s["per_min"]) == 60 and len(s["last_errors"]) <= 20
    assert "4PM23CS001" not in json.dumps(s)
    assert b"ASTRA Watch" in urllib.request.urlopen(f"{base}/?k={urllib.parse.quote(cfg['dash_key'])}", timeout=5).read()
    for path in ("/status.json?k=wrong", "/status.json", "/?k=wrong"):
        try:
            urllib.request.urlopen(base + path, timeout=5)
            raise AssertionError("wrong key accepted: " + path)
        except urllib.error.HTTPError as e:
            assert e.code == 403
    srv.shutdown()

    # the watcher never wrote to the platform DB
    try:
        connect_ro(dbp).execute("INSERT INTO error_log(source,message,created_at) VALUES('x','y',1)")
        raise AssertionError("read-only connection accepted a write")
    except sqlite3.OperationalError:
        pass
    assert scrub("x" * 700).endswith("...") and len(scrub("x" * 700)) == 603
    shutil.rmtree(d, ignore_errors=True)
    print("selftest OK")


# ---------------- main ----------------

def main():
    ap = argparse.ArgumentParser(description="ASTRA remote watch")
    ap.add_argument("--serve", action="store_true", help="also serve the dashboard on 127.0.0.1:<dash_port>")
    ap.add_argument("--tunnel", action="store_true", help="also open a Cloudflare quick tunnel to the dashboard (implies --serve)")
    ap.add_argument("--interval", type=int, help="seconds between checks (default from config, 30)")
    ap.add_argument("--test-alert", action="store_true", help="send one test notification and exit")
    ap.add_argument("--selftest", action="store_true", help="run the offline self-test")
    ap.add_argument("--info", action="store_true", help="print the subscribe link and dashboard URL")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    cfg = load_config()
    dash = f"http://127.0.0.1:{cfg['dash_port']}/?k={cfg['dash_key']}"
    sub = f"{cfg['ntfy_url'].rstrip('/')}/{cfg['topic']}"
    if args.info:
        print(f"Config:     {CONFIG_F}\nSubscribe:  {sub}\nDashboard:  {dash}\nDB:         {cfg['db_path']}\nPlatform:   {app_url(cfg)}")
        return
    send = ntfy_sender(cfg)
    if args.test_alert:
        send("ASTRA watch test", "If you can read this on your phone, alerts work.", 3, "white_check_mark")
        print("Sent to", sub)
        return
    alerter = Alerter(send)
    state = load_state()
    if args.serve or args.tunnel:
        srv = make_server(cfg, state)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        log("dashboard:", dash)
    if args.tunnel:
        run_tunnel(cfg, alerter)
    log("watching", cfg["db_path"], "->", sub)
    w = Watcher(cfg, alerter, state)
    interval = args.interval or int(cfg.get("interval_sec", 30))
    while True:
        try:
            w.run_once()
        except Exception:
            log("loop error:", traceback.format_exc(limit=3))
        time.sleep(interval)


if __name__ == "__main__":
    main()
