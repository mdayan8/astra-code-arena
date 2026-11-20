"""ASTRA Code Arena setup app: one button installs (or updates/repairs) everything, with a live progress bar.
Started by ../start.sh. Uses only what a fresh Ubuntu already has (python3 + pkexec for the password prompt)."""
import http.server
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from app.hostinfo import physical_cores, workers_for  # noqa: E402  (stdlib only: same sizing the platform uses)
INSTALL = HERE / "install.sh"
PORT, PANEL = 8764, "http://127.0.0.1:8765/"
TOKEN = secrets.token_urlsafe(16)
STEPS = ["Checking this computer", "Installing compilers", "Installing Node.js", "Copying the app", "Installing Python packages",
         "Installing the local AI model", "Bringing in data from the old computer", "Creating the background service", "Keeping the computer awake", "Setting up a fixed network address", "Done"]
UPLOAD = Path(f"/tmp/astra-move-upload-{os.getuid()}.zip")
job = {"state": "idle", "ai": False, "imp": False, "log": [], "step": "", "done_steps": 0, "total": 9, "error": "", "password": "", "students": "", "notes": []}


def panel_up():
    try:
        urllib.request.urlopen(PANEL + "api/status", timeout=2).read()
        return True
    except Exception:
        return False


def installed_version():
    try:
        return Path("/opt/astra-arena/.installed-version").read_text().strip()
    except OSError:
        return None


def repo_version():
    r = subprocess.run(["git", "-C", str(HERE.parent), "rev-parse", "HEAD"], capture_output=True, text=True) if shutil.which("git") else None
    return r.stdout.strip() if r and r.returncode == 0 else None


def machine():
    try:
        mem = int(next(l for l in open("/proc/meminfo") if l.startswith("MemTotal")).split()[1]) / 1048576
    except (OSError, StopIteration):
        mem = 0
    phys, threads = physical_cores(), os.cpu_count() or 1
    cap = lambda ai: workers_for(mem * 1024, phys, threads, ai) * 100
    return {"ram_gb": round(mem, 1), "threads": threads, "cores": phys, "students_ai": cap(True), "students": cap(False), "free_gb": round(shutil.disk_usage("/var" if Path("/var").exists() else "/").free / 1e9),
            "os": (Path("/etc/os-release").read_text().split('PRETTY_NAME="')[1].split('"')[0] if Path("/etc/os-release").exists() else sys.platform),
            "installed": installed_version() is not None, "running": panel_up(),
            "up_to_date": installed_version() is not None and installed_version() == repo_version()}


def run_install(ai, imp):
    job.update(state="running", ai=ai, imp=imp, log=[], step="Waiting for your password...", done_steps=0, total=9 + ai + imp, error="", password="", notes=[])
    cmd = ["pkexec", "/bin/bash", str(INSTALL)] + (["--with-ai"] if ai else []) + (["--import", str(UPLOAD)] if imp else [])
    if os.geteuid() == 0:
        cmd = cmd[1:]
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", bufsize=1)
    except FileNotFoundError:
        job.update(state="failed", error="pkexec is missing. Open a terminal here and run: sudo ./ubuntu/install.sh" + (" --with-ai" if ai else ""))
        return
    for raw in p.stdout:
        for line in raw.replace("\x1b[1;35m", "").replace("\x1b[0m", "").split("\r"):
            line = line.rstrip()
            if not line:
                continue
            job["log"] = (job["log"] + [line])[-400:]
            if line.startswith("==> "):
                job["done_steps"] += 1 if job["step"] and not job["step"].startswith("Waiting") else 0
                job["step"] = line[4:]
            elif line.startswith("##FAILED"):
                job["error"] = line[len("##FAILED"):].lstrip(": ").strip()
            elif line.startswith("NOTE: "):
                job["notes"] = job["notes"] + [line[6:]]
            elif "First admin login:" in line:
                job["password"] = line.split("admin /")[-1].split("(")[0].strip()
            elif line.strip().startswith("Students:"):
                job["students"] = line.split("Students:")[-1].strip()
    code = p.wait()
    if code == 0:
        job.update(state="done", done_steps=job["total"], step="Done")
    elif code in (126, 127) and not job["log"]:
        job.update(state="failed", error="The password window was closed. Press the button again and enter your Ubuntu password.")
    else:
        job.update(state="failed", error=job["error"] or f"Stopped during: {job['step']}. See the details below, fix it, then press Try again.")


PAGE = r"""<!doctype html><html><head><meta charset=utf-8><title>ASTRA Code Arena · Setup</title>
<style>body{margin:0;font:15px/1.5 system-ui,Ubuntu,sans-serif;background:#f6f6fa;color:#16161d;display:grid;place-items:center;min-height:100vh}
.card{background:#fff;border:1px solid #e4e4ee;border-radius:16px;padding:32px;width:640px;box-shadow:0 10px 40px #0000000d}h1{margin:0 0 4px;font-size:22px}
.muted{color:#666a78}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:20px 0}.grid div{background:#f3f2fa;border-radius:10px;padding:10px;text-align:center}.grid b{display:block;font-size:18px}
.btn{border:0;border-radius:10px;padding:13px 26px;font:inherit;font-weight:600;cursor:pointer;background:#6d4aff;color:#fff}.btn.ghost{background:#fff;color:#6d4aff;border:1px solid #d8d2ff}.btn:disabled{opacity:.5}
.bar{height:14px;background:#ececf3;border-radius:99px;overflow:hidden;margin:18px 0 8px}.bar i{display:block;height:100%;width:0;background:linear-gradient(90deg,#6d4aff,#a68bff);transition:width .5s}
.steps{list-style:none;padding:0;margin:14px 0}.steps li{padding:3px 0;color:#9a9db0}.steps li.done{color:#12a150}.steps li.now{color:#16161d;font-weight:600}
pre{background:#111;color:#ddd;border-radius:10px;padding:10px;max-height:220px;overflow:auto;font-size:12px;white-space:pre-wrap;display:none}
.cap{background:#efeaff;color:#3b2a8f;padding:14px;border-radius:10px;margin-bottom:10px}.ok{background:#e3f7ec;color:#0b6b37;padding:14px;border-radius:10px}.err{background:#fde8ea;color:#8f1d27;padding:14px;border-radius:10px}code{background:#fff;padding:2px 6px;border-radius:5px}
label{display:block;margin:6px 0 18px}.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}</style></head><body><div class=card>
<h1>ASTRA Code Arena</h1><div class=muted id=sub>Setup for this computer</div><div class=grid id=mach></div><div id=view></div></div>
<script>
const T='__TOKEN__',$=s=>document.querySelector(s),STEPS=__STEPS__;let M,open=false;
const esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
async function go(){const ai=$('#ai')?.checked,f=$('#pkg')?.files[0];
 if(f){$('#go').disabled=true;$('#go').textContent='Reading the move package...';const r=await (await fetch('/upload',{method:'POST',headers:{'X-Token':T},body:f})).json();if(!r.ok){$('#go').disabled=false;$('#go').textContent='Try again';return alert(r.error)}}
 await fetch('/install',{method:'POST',headers:{'X-Token':T,'Content-Type':'application/json'},body:JSON.stringify({ai,imp:!!f})});tick()}
function idle(){const label=!M.installed?'Install ASTRA Code Arena':M.up_to_date?'Repair / reinstall':'Update to this version';
 $('#view').innerHTML=`<div class=cap>With ${M.ram_gb} GB RAM and ${M.cores} cores, this PC handles <b>about ${M.students} students at once</b>, or <b>about ${M.students_ai}</b> with the AI model on.</div>${M.free_gb<20?`<div class=err>Installation needs 20 GB of free disk; this PC has ${M.free_gb} GB. Free some space, then open this again.</div>`:''}
 <p class=muted>${M.installed?'Already installed. Updating keeps every student, question and result.':'One click installs everything it needs: compilers (C, C++, Java, Python, JavaScript), the platform, the background service and the control panel. It needs internet this one time and takes about 5-15 minutes. Ubuntu will ask for your password.'}</p>
 <label><input type=checkbox id=ai ${M.ram_gb>=7?'checked':''}> Also install the AI model (Prompt challenges, AI Studio): about 2 GB download, uses about 1.5 GB RAM</label>
 <label>Moving from another computer? Choose its move package (astra-move-....zip) to bring every competition, question, student and result:<br><input type=file id=pkg accept=".zip" style="margin-top:6px"></label>
 <div class=row><button class=btn id=go ${M.free_gb<20?'disabled':''}>${label}</button>${M.running?'<a class="btn ghost" href="http://127.0.0.1:8765/">Open control panel</a>':''}</div>`;$('#go').onclick=go}
function progress(j){const pct=Math.round(100*j.done_steps/j.total),list=STEPS.filter(s=>(j.ai||!s.includes('AI'))&&(j.imp||!s.includes('old computer')));
 const cur=list.findIndex(s=>j.step.startsWith(s.split(' ').slice(0,2).join(' ')));
 let h=`<div class=row style="justify-content:space-between"><b>${esc(j.step||'Starting...')}</b><span>${pct}%</span></div><div class=bar><i style="width:${Math.max(pct,3)}%"></i></div>
 <div class="muted" style="font-size:13px;height:20px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis">${esc(j.log.at(-1)||'')}</div>
 <ul class=steps>${list.map((s,i)=>`<li class="${j.state==='done'||i<cur?'done':i===cur?'now':''}">${j.state==='done'||i<cur?'&#10003;':i===cur?'&#9654;':'&#9675;'} ${s}</li>`).join('')}</ul>`;
 if(j.state==='done')h+=`<div class=ok><b>Installed and running.</b> It starts by itself after every reboot.${j.imp?'<br>All data from the old computer is here. Sign in with the same admin accounts as before.':''}${j.password?`<br>Organiser site login: user <code>admin</code>, password <code>${esc(j.password)}</code>. Write it down: you must change it at first sign-in.`:''}${j.students?`<br>Students open: <code>${esc(j.students)}</code>`:''}</div>${(j.notes||[]).length?`<div class=cap style="margin-top:10px"><b>The platform works. ${j.notes.length} part(s) can be finished later</b> by running this setup again:<ul style="margin:6px 0 0;padding-left:18px">${j.notes.map(n=>`<li>${esc(n)}</li>`).join('')}</ul></div>`:''}<p><a class=btn href="http://127.0.0.1:8765/">Open control panel</a></p><p class=muted>The control panel asks you to create its own password the first time it opens.</p><p class=muted>Next time, open <b>ASTRA Code Arena</b> from the Ubuntu app menu.</p>`;
 if(j.state==='failed')h+=`<div class=err><b>Setup stopped.</b> ${esc(j.error)}</div><p class=row><button class=btn id=go>Try again</button></p>`;
 h+=`<p><a href="#" id=det>${open?'Hide':'Show'} details</a></p><pre id=log style="display:${open?'block':'none'}">${esc(j.log.join('\n'))}</pre>`;
 $('#view').innerHTML=h;if($('#go'))$('#go').onclick=()=>{M.installed=true;idle()};$('#det').onclick=e=>{e.preventDefault();open=!open;progress(j)};if(open)$('#log').scrollTop=1e9}
async function tick(){const j=await (await fetch('/job')).json();if(j.state==='idle')return idle();progress(j);if(j.state==='running')setTimeout(tick,1000)}
(async()=>{M=await (await fetch('/machine')).json();$('#mach').innerHTML=`<div><b>${M.cores}</b>CPU cores</div><div><b>${M.ram_gb} GB</b>RAM</div><div><b>${M.free_gb} GB</b>free disk</div><div><b>${M.students}</b>students at once</div>`;$('#sub').textContent='Setup for this computer · '+M.os;tick()})();
</script></body></html>"""


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body.encode())

    def do_GET(self):
        if self.headers.get("Host", "").split(":")[0] not in ("127.0.0.1", "localhost"):
            return self.send(403, "{}")
        if self.path == "/":
            return self.send(200, PAGE.replace("__TOKEN__", TOKEN).replace("__STEPS__", json.dumps(STEPS)), "text/html; charset=utf-8")
        if self.path == "/machine":
            return self.send(200, json.dumps(machine()))
        if self.path == "/job":
            return self.send(200, json.dumps(job))
        self.send(404, "{}")

    def do_POST(self):
        if self.headers.get("X-Token") != TOKEN or self.path not in ("/install", "/upload"):
            return self.send(403, "{}")
        if self.path == "/upload":  # the move package from the old computer, streamed to a private temp file
            left = int(self.headers.get("Content-Length") or 0)
            with open(os.open(UPLOAD, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as out:
                while left > 0:
                    chunk = self.rfile.read(min(left, 1 << 20))
                    if not chunk:
                        break
                    out.write(chunk)
                    left -= len(chunk)
            import zipfile
            ok = zipfile.is_zipfile(UPLOAD) and "manifest.json" in zipfile.ZipFile(UPLOAD).namelist()
            return self.send(200 if ok else 400, json.dumps({"ok": ok, "error": None if ok else "That file is not an ASTRA Arena move package (astra-move-....zip)."}))
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if job["state"] != "running":
            threading.Thread(target=run_install, args=(bool(body.get("ai")), bool(body.get("imp")) and UPLOAD.exists()), daemon=True).start()
            time.sleep(0.2)
        self.send(200, "{}")


def main():
    if installed_version() and installed_version() == repo_version() and panel_up() and "--setup" not in sys.argv:
        webbrowser.open(PANEL)  # already installed and current: go straight to the control panel
        return
    try:
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), H)
    except OSError:  # setup window already open
        webbrowser.open(f"http://127.0.0.1:{PORT}/")
        return
    threading.Timer(0.6, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}/")).start()
    threading.Thread(target=lambda: (time.sleep(6 * 3600), os._exit(0)), daemon=True).start()  # never linger forever
    srv.serve_forever()


if __name__ == "__main__":
    main()
