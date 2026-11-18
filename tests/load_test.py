"""Load test: N simulated students in one live competition for D seconds.
Each student keeps the arena open (polls every 15 s), streams activity events every 10 s, checks the leaderboard,
and submits C/Python solutions at random moments, polling each verdict until judged.

    python3 tests/load_test.py [students=300] [seconds=120] [judge_workers=4]
"""
import asyncio
import os
import random
import secrets
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
N = int(sys.argv[1]) if len(sys.argv) > 1 else 300
DUR = int(sys.argv[2]) if len(sys.argv) > 2 else 120
WORKERS = sys.argv[3] if len(sys.argv) > 3 else "4"
PORT = 8799
TMP = Path(tempfile.mkdtemp(prefix="astra-load-"))
ENV = {**os.environ, "DATA_DIR": str(TMP), "APP_PORT": str(PORT), "APP_HOST": "127.0.0.1", "ADMIN_PATH": "x-load", "JUDGE_WORKERS": WORKERS,
       "JUDGE_TMP": f"/tmp/astra-judge-load-{PORT}", "APP_ENV": "production"}
os.environ.update(ENV)
sys.path.insert(0, str(ROOT))
import httpx  # noqa: E402

from app import db  # noqa: E402
from app.security import _th, hash_password  # noqa: E402

BASE = f"http://127.0.0.1:{PORT}"
AC = {"c": '#include <stdio.h>\nint main(){int n;long long s=0,x;scanf("%d",&n);while(n--){scanf("%lld",&x);s+=x;}printf("%lld\\n",s);}',
      "python": "n=int(input())\nprint(sum(map(int,input().split())))\n"}
WA = {"c": '#include <stdio.h>\nint main(){puts("0");}', "python": "print(0)\n"}
lat, errors, judge_wait = {}, [], []


def seed():
    db.init()
    c = db.connect()
    t = db.now()
    admin = c.execute("INSERT INTO users(username,name,password_hash,kind,role,created_at) VALUES('root','Root','x','admin','super_admin',?)", (t,)).lastrowid
    q = c.execute("INSERT INTO questions(slug,status,created_by,created_at,updated_at) VALUES('sum','approved',?,?,?)", (admin, t, t)).lastrowid
    v = c.execute("INSERT INTO question_versions(question_id,version_no,title,statement,time_limit_ms,memory_mb,created_at,validated_at) VALUES(?,1,'Sum','Sum n numbers',1000,128,?,?)", (q, t, t)).lastrowid
    c.execute("UPDATE questions SET latest_version_id=? WHERE id=?", (v, q))
    rnd = random.Random(1)
    for i in range(12):
        n = 1 if i == 0 else rnd.randint(1, 2000)
        arr = [rnd.randint(-10**6, 10**6) for _ in range(n)]
        c.execute("INSERT INTO test_cases(version_id,ordinal,input,expected,visibility,weight) VALUES(?,?,?,?,?,1)",
                  (v, i, f"{n}\n{' '.join(map(str, arr))}\n", f"{sum(arr)}\n", "sample" if i == 0 else "hidden"))
    comp = c.execute("INSERT INTO competitions(code,title,start_at,started_at,duration_min,languages,status,created_by,created_at,updated_at) "
                     "VALUES('LOAD','Load test',?,?,600,'[\"c\",\"python\"]','live',?,?,?)", (t, t, admin, t, t)).lastrowid
    c.execute("INSERT INTO competition_questions(competition_id,question_id,version_id,ordinal,marks) VALUES(?,?,?,0,100)", (comp, q, v))
    c.execute("UPDATE platform_settings SET value='0' WHERE key='submit_cooldown_sec'")
    pw = hash_password("x" * 8)
    toks = []
    c.execute("BEGIN")
    for i in range(N):
        uid = c.execute("INSERT INTO users(username,name,password_hash,kind,created_at) VALUES(?,?,?,'student',?)", (f"S{i:04d}", f"Student {i}", pw, t)).lastrowid
        c.execute("INSERT INTO students(user_id,participant_id,usn,department,year) VALUES(?,?,?,'CSE',2)", (uid, f"L-{i:04d}", f"S{i:04d}"))
        c.execute("INSERT INTO competition_participants(competition_id,user_id,joined_at) VALUES(?,?,?)", (comp, uid, t))
        tok, csrf = secrets.token_urlsafe(24), secrets.token_urlsafe(16)
        c.execute("INSERT INTO sessions(token_hash,user_id,csrf,created_at,expires_at) VALUES(?,?,?,?,?)", (_th(tok), uid, csrf, t, t + 86400))
        toks.append((tok, csrf))
    c.execute("COMMIT")
    c.close()
    return comp, q, toks


async def timed(name, coro):
    t = time.perf_counter()
    try:
        r = await coro
        if r.status_code >= 400:
            errors.append(f"{name} {r.status_code} {r.text[:80]}")
        return r
    except Exception as e:  # noqa: BLE001
        errors.append(f"{name} {type(e).__name__}")
    finally:
        lat.setdefault(name, []).append(time.perf_counter() - t)


async def student(i, comp, q, tok, csrf, end):
    await asyncio.sleep(random.random() * 5)  # students arrive over 5 s
    h = {"X-CSRF-Token": csrf}
    async with httpx.AsyncClient(base_url=BASE, cookies={"astra_s": tok}, timeout=60) as c:
        submits = sorted(random.uniform(5, DUR - 20) for _ in range(2))
        start = time.time()
        next_arena = next_act = next_lb = 0
        while time.time() < end:
            now = time.time() - start
            if now >= next_arena:
                await timed("GET arena", c.get(f"/api/student/competitions/{comp}/arena"))
                next_arena = now + 15
            if now >= next_act:
                ev = [{"type": random.choice(["keystrokes", "heartbeat", "tab_hidden", "tab_visible", "paste"]), "detail": {"n": 40}} for _ in range(5)]
                await timed("POST activity", c.post("/api/student/activity", json={"competition_id": comp, "events": ev}, headers=h))
                next_act = now + 10
            if i % 3 == 0 and now >= next_lb:
                await timed("GET leaderboard", c.get(f"/api/competitions/{comp}/leaderboard"))
                next_lb = now + 15
            if submits and now >= submits[0]:
                submits.pop(0)
                lang = random.choice(["c", "python"])
                code = (AC if random.random() < 0.7 else WA)[lang]
                t0 = time.time()
                r = await timed("POST submit", c.post("/api/student/submissions", json={"competition_id": comp, "question_id": q, "language": lang, "code": code, "kind": "submit"}, headers=h))
                if r is not None and r.status_code == 200:
                    sid = r.json()["id"]
                    while time.time() - t0 < 300:
                        await asyncio.sleep(1.5)
                        s = await timed("GET verdict", c.get(f"/api/student/submissions/{sid}"))
                        if s is not None and s.status_code == 200 and s.json()["status"] in ("done", "error"):
                            judge_wait.append(time.time() - t0)
                            if s.json()["verdict"] == "IE":
                                errors.append("judge IE")
                            break
            await asyncio.sleep(0.5)


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else 0


async def main():
    comp, q, toks = seed()
    web = subprocess.Popen([sys.executable, "-m", "app.main"], cwd=ROOT, env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wk = subprocess.Popen([sys.executable, "-m", "app.worker"], cwd=ROOT, env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                if httpx.get(f"{BASE}/api/health").status_code == 200:
                    break
            except httpx.HTTPError:
                await asyncio.sleep(0.1)
        print(f"{N} students, {DUR}s, {WORKERS} judge workers ...")
        end = time.time() + DUR
        await asyncio.gather(*(student(i, comp, q, t, cs, end) for i, (t, cs) in enumerate(toks)))
        # let the queue drain
        t0 = time.time()
        while time.time() - t0 < 300:
            c = db.connect()
            left = db.one(c, "SELECT COUNT(*) AS n FROM submissions WHERE status IN ('queued','running')")["n"]
            c.close()
            if not left:
                break
            await asyncio.sleep(1)
    finally:
        web.terminate(); wk.terminate()
    c = db.connect()
    done = db.one(c, "SELECT COUNT(*) AS n, SUM(verdict='AC') AS ac, SUM(verdict='WA') AS wa, SUM(status='error') AS err FROM submissions")
    total_req = sum(len(v) for v in lat.values())
    print(f"\nrequests: {total_req} ({total_req / DUR:.0f}/s)   HTTP errors: {len(errors)}")
    for k, v in sorted(lat.items()):
        print(f"  {k:16s} n={len(v):6d}  p50={statistics.median(v) * 1000:7.1f} ms  p95={pct(v, .95) * 1000:7.1f} ms  max={max(v) * 1000:7.1f} ms")
    print(f"submissions judged: {done['n']} (AC {done['ac']}, WA {done['wa']}, judge errors {done['err'] or 0})")
    if judge_wait:
        print(f"submit -> verdict: p50={statistics.median(judge_wait):.1f}s  p95={pct(judge_wait, .95):.1f}s  max={max(judge_wait):.1f}s")
    if errors:
        print("first errors:", errors[:8])
    ok = not errors and not done["err"]
    print("RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
