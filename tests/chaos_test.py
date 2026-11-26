"""Crash test during a live round: the judge and the web server are killed (kill -9) and restarted while students submit.
Checks: no submission lost or judged twice, students stay signed in, the round clock keeps going, the database stays intact.

    python tests/chaos_test.py [students=40]
"""
import os
import random
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
N = int(sys.argv[1]) if len(sys.argv) > 1 else 40
TMP = Path(tempfile.mkdtemp(prefix="astra-chaos-"))
PORT = 8811
ENV = {**os.environ, "DATA_DIR": str(TMP), "APP_PORT": str(PORT), "APP_HOST": "127.0.0.1", "ADMIN_PATH": "x", "JUDGE_WORKERS": "3",
       "JUDGE_TMP": str(TMP / "judge"), "AI_BASE_URL": "http://127.0.0.1:9/v1", "AI_MODEL": "none"}
os.environ.update(ENV)
sys.path.insert(0, str(ROOT))
import httpx  # noqa: E402

from app import db  # noqa: E402
from app.security import hash_password  # noqa: E402

B = f"http://127.0.0.1:{PORT}"
OK_C = '#include <stdio.h>\nint main(void){long a,b;if(scanf("%ld %ld",&a,&b)!=2)return 0;printf("%ld\\n",a+b);return 0;}\n'
SLOW_C = '#include <stdio.h>\nint main(void){volatile long s=0;for(long i=0;i<300000000L;i++)s+=i;long a,b;scanf("%ld %ld",&a,&b);printf("%ld\\n",a+b);return 0;}\n'


def start(mod):
    return subprocess.Popen([sys.executable, "-m", mod], cwd=ROOT, env=ENV, stdout=subprocess.DEVNULL, stderr=open(TMP / f"{mod}.log", "a"), start_new_session=True)


def kill(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)   # the worker's judge processes die with it, like a power cut for that process tree
    except ProcessLookupError:
        pass
    p.wait()


def up(timeout=30):
    t = time.time()
    while time.time() - t < timeout:
        try:
            if httpx.get(f"{B}/api/public/info", timeout=2).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    return False


db.init()
c = db.connect()
c.execute("INSERT INTO users(username,name,password_hash,kind,role,can_approve,created_at) VALUES('root','Root',?,'admin','super_admin',1,?)", (hash_password("superpass123"), db.now()))
web, worker = start("app.main"), start("app.worker")
fails = []
try:
    assert up(), "server did not start"
    adm = httpx.Client(base_url=B, timeout=30)
    H = {"X-CSRF-Token": adm.post("/api/admin/login", json={"username": "root", "password": "superpass123"}).json()["csrf"]}
    qs = [q for q in adm.get("/api/admin/questions").json() if q["title"] in ("Sum of Two Numbers", "Even or Odd")]
    sum_q = next(q["id"] for q in qs if q["title"] == "Sum of Two Numbers")
    comp = adm.post("/api/admin/competitions", json={"title": "Chaos", "code": "CHAOS", "start_at": int(time.time()) + 3600, "duration_min": 30,
                                                     "leaderboard_visibility": "live", "allow_late_join": True}, headers=H).json()
    adm.put(f"/api/admin/competitions/{comp['id']}/questions", json={"questions": [{"question_id": q["id"], "marks": 100} for q in qs]}, headers=H)
    adm.post(f"/api/admin/competitions/{comp['id']}/action", json={"action": "publish"}, headers=H)
    students = []
    for k in range(N):
        s = httpx.Client(base_url=B, timeout=30)
        csrf = s.post("/api/auth/register", json={"name": f"Chaos {k}", "usn": f"4PM23CS{300 + k}", "department": "CSE", "year": 2, "semester": 3, "section": "A",
                                                 "dob": "2005-08-15"}).json()["csrf"]
        s.post(f"/api/student/competitions/{comp['id']}/join", headers={"X-CSRF-Token": csrf})
        students.append((s, csrf))
    adm.post(f"/api/admin/competitions/{comp['id']}/action", json={"action": "start"}, headers=H)
    before = adm.get(f"/api/admin/competitions/{comp['id']}").json()["remaining_sec"]

    sent, lock = [], threading.Lock()

    def student_loop(s, csrf, rounds):
        for _ in range(rounds):
            code = random.choice([OK_C, OK_C, SLOW_C])
            for attempt in range(20):   # the page retries when the server is briefly down
                try:
                    r = s.post("/api/student/submissions", json={"competition_id": comp["id"], "question_id": sum_q, "language": "c", "code": code, "kind": "submit"},
                               headers={"X-CSRF-Token": csrf})
                    if r.status_code == 200:
                        with lock:
                            sent.append(r.json()["id"])
                        break
                    if r.status_code == 429:
                        time.sleep(1.2)
                        continue
                    fails.append(f"submit {r.status_code}: {r.text[:120]}")
                    break
                except httpx.HTTPError:
                    time.sleep(0.5)
            time.sleep(random.uniform(5.2, 7))

    threads = [threading.Thread(target=student_loop, args=(s, csrf, 3)) for s, csrf in students]
    for t in threads:
        t.start()
    time.sleep(6)
    print("kill -9 judge worker while it is judging")
    kill(worker)
    time.sleep(4)
    worker = start("app.worker")
    time.sleep(5)
    print("kill -9 web server while students submit")
    kill(web)
    time.sleep(3)
    web = start("app.main")
    assert up(), "server did not come back"
    for t in threads:
        t.join()
    t0 = time.time()
    while time.time() - t0 < 240:
        left = c.execute("SELECT COUNT(*) FROM submissions WHERE competition_id=? AND status!='done'", (comp["id"],)).fetchone()[0]
        if not left:
            break
        time.sleep(2)

    rows = c.execute("SELECT id, status, verdict, attempts FROM submissions WHERE competition_id=? AND kind='submit'", (comp["id"],)).fetchall()
    stuck = [r for r in rows if r[1] != "done"]
    ie = [r for r in rows if r[2] == "IE"]
    dup = c.execute("SELECT submission_id, COUNT(DISTINCT ordinal), COUNT(*) FROM judging_results WHERE submission_id IN "
                    "(SELECT id FROM submissions WHERE competition_id=?) GROUP BY submission_id HAVING COUNT(*) > COUNT(DISTINCT ordinal)", (comp["id"],)).fetchall()
    lost = set(sent) - {r[0] for r in rows}
    verdicts = {}
    for r in rows:
        verdicts[r[2]] = verdicts.get(r[2], 0) + 1
    print(f"submissions: sent {len(sent)}, stored {len(rows)}, verdicts {verdicts}, retried after crash {sum(1 for r in rows if r[3] > 1)}")
    s, csrf = students[0]
    me = s.get("/api/me").json()
    arena = s.get(f"/api/student/competitions/{comp['id']}/arena")
    after = adm.get(f"/api/admin/competitions/{comp['id']}").json()
    lb = s.get(f"/api/competitions/{comp['id']}/leaderboard").json()
    integrity = sqlite3.connect(TMP / "astra.db").execute("PRAGMA integrity_check").fetchone()[0]
    checks = {
        "no submission lost": not lost,
        "no submission stuck": not stuck,
        "no judge errors": not ie,
        "no test judged twice": not dup,
        "student still signed in after the server restart": bool(me.get("user")),
        "arena still opens": arena.status_code == 200,
        "round still live, clock kept running": after["status"] == "live" and after["remaining_sec"] < before,
        "leaderboard has every student": len(lb["rows"]) == N,
        "database intact": integrity == "ok",
        "no request failed for another reason": not fails,
    }
    for k, v in checks.items():
        print(("  PASS  " if v else "  FAIL  ") + k)
    if fails:
        print("  first failures:", fails[:3])
    errs = c.execute("SELECT source, substr(message,1,160) FROM error_log ORDER BY id DESC LIMIT 5").fetchall()
    print("error_log:", errs or "empty")
    sys.exit(0 if all(checks.values()) else 1)
finally:
    kill(web)
    kill(worker)
