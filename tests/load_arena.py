"""Realistic live-round load test: N students register + join (waves of 50), open the arena together, poll it every 15 s,
flush activity every 10 s (heartbeat + code snapshot every 30 s), 1/3 keep the live leaderboard open, answer MCQs, submit C
(AC / WA / CE, one infinite loop per 50 students), a few violations, everyone presses Finish near the end.
Competition: 12 seeded questions (6 MCQ + 6 C), draw=5, shuffle on. Then checks queue drain, draws and leaderboard.

    python tests/load_arena.py [students=500] [live_seconds=360] [judge_workers=6] [port=8801] [data_dir]
Server is started with a soft RLIMIT_NOFILE of 1024 (systemd's default) unless NOFILE=0.
"""
import asyncio
import json
import os
import random
import resource
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
N = int(sys.argv[1]) if len(sys.argv) > 1 else 500
DUR = int(sys.argv[2]) if len(sys.argv) > 2 else 360
WORKERS = sys.argv[3] if len(sys.argv) > 3 else "6"
PORT = int(sys.argv[4]) if len(sys.argv) > 4 else 8801
TMP = Path(sys.argv[5]) if len(sys.argv) > 5 else Path(tempfile.mkdtemp(prefix="astra-load-arena-"))
NOFILE = int(os.environ.get("NOFILE", "1024"))
shutil.rmtree(TMP, ignore_errors=True)
TMP.mkdir(parents=True)
ENV = {**os.environ, "DATA_DIR": str(TMP), "APP_PORT": str(PORT), "APP_HOST": "127.0.0.1", "ADMIN_PATH": "x-load", "JUDGE_WORKERS": WORKERS,
       "JUDGE_TMP": str(TMP / "judge"), "APP_ENV": "production", "AI_BASE_URL": "http://127.0.0.1:9/v1", "AI_MODEL": "none"}
os.environ.update(ENV)
sys.path.insert(0, str(ROOT))
import httpx  # noqa: E402

from app import db  # noqa: E402
from app.security import _th, hash_password  # noqa: E402

BASE = f"http://127.0.0.1:{PORT}"
WA_C = '#include <stdio.h>\nint main(){puts("0");return 0;}\n'
CE_C = '#include <stdio.h>\nint main(){ printf("%d" x) }\n'
LOOP_C = 'int main(){volatile long i=0;while(1)i++;}\n'
lat, status = {}, {}          # name -> [seconds], name -> {code: count}
problems = []                 # unexpected failures
seen_draws = {}               # uid -> set(tuple(question ids)) seen in arena responses
mem = []                      # (t, server_rss_mb, worker_rss_mb, server_fds)
T0 = time.time()

# 409/429 responses the platform sends on purpose
INTENTIONAL = {"POST submit": {429, 409}, "POST finish": {409}, "POST register": {429}}


def rec(name, code, dt):
    lat.setdefault(name, []).append(dt)
    status.setdefault(name, {}).setdefault(code, 0)
    status[name][code] += 1
    if code == "timeout" or code == "exc" or (isinstance(code, int) and code >= 500):
        problems.append(f"{time.time() - T0:6.1f}s {name} {code}")


async def call(c, name, method, url, **kw):
    t = time.perf_counter()
    try:
        r = await c.request(method, url, **kw)
    except httpx.TimeoutException:
        rec(name, "timeout", time.perf_counter() - t)
        return None
    except Exception as e:  # noqa: BLE001
        rec(name, "exc", time.perf_counter() - t)
        problems.append(f"{name} {type(e).__name__}: {e}")
        return None
    rec(name, r.status_code, time.perf_counter() - t)
    if r.status_code >= 400 and r.status_code not in INTENTIONAL.get(name, ()):
        problems.append(f"{time.time() - T0:6.1f}s {name} {r.status_code} {r.text[:120]}")
    return r


def setup_admin():
    db.init()
    c = db.connect()
    c.execute("INSERT INTO users(username,name,password_hash,kind,role,can_approve,created_at) VALUES('root','Root',?,'admin','super_admin',1,?)",
              (hash_password("superpass123"), db.now()))
    c.close()


def pick_questions():   # the web server seeds the built-in questions at startup
    c = db.connect()
    mcq = [r["id"] for r in db.all_(c, "SELECT q.id FROM questions q JOIN question_versions v ON v.id=q.latest_version_id WHERE v.qtype='mcq' AND q.difficulty='Easy' ORDER BY q.id LIMIT 6")]
    code = [r["id"] for r in db.all_(c, "SELECT q.id FROM questions q JOIN question_versions v ON v.id=q.latest_version_id WHERE v.qtype='standard' AND v.languages LIKE '%\"c\"%' AND q.difficulty='Easy' ORDER BY q.id LIMIT 6")]
    key = {}
    for q in mcq:
        key[q] = ("mcq", db.one(c, "SELECT t.expected FROM test_cases t JOIN questions q ON q.latest_version_id=t.version_id WHERE q.id=? AND t.visibility='hidden' LIMIT 1", q)["expected"].strip())
    for q in code:
        key[q] = ("c", db.one(c, "SELECT r.code FROM reference_solutions r JOIN questions q ON q.latest_version_id=r.version_id WHERE q.id=? AND r.language='c'", q)["code"])
    c.close()
    return mcq + code, key


def fallback_account(i):
    """Register rate limit (300 / 10 min per IP) hit: all simulated students share 127.0.0.1. Create the account directly."""
    c = db.connect()
    t = db.now()
    with db.tx(c):
        uid = c.execute("INSERT INTO users(username,name,password_hash,kind,created_at) VALUES(?,?,'x','student',?)", (f"4PM26CS{i:03d}", f"Student {i}", t)).lastrowid
        c.execute("INSERT INTO students(user_id,participant_id,usn,department,year,section,dob,semester) VALUES(?,?,?,'CSE',2,'A','2005-08-15',3)", (uid, f"FB-{i:04d}", f"4PM26CS{i:03d}"))
        tok, csrf = os.urandom(18).hex(), os.urandom(8).hex()
        c.execute("INSERT INTO sessions(token_hash,user_id,csrf,created_at,expires_at) VALUES(?,?,?,?,?)", (_th(tok), uid, csrf, t, t + 86400))
    c.close()
    return uid, tok, csrf


class Student:
    def __init__(self, i):
        self.i, self.c, self.csrf, self.uid = i, None, "", None
        self.probs, self.finished, self.pending = [], False, False

    def h(self):
        return {"X-CSRF-Token": self.csrf}

    async def register(self, comp):
        self.c = httpx.AsyncClient(base_url=BASE, timeout=30)
        r = await call(self.c, "POST register", "POST", "/api/auth/register", json={"name": f"Student {self.i}", "usn": f"4PM26CS{self.i:03d}", "department": "CSE",
                                                                                 "year": 2, "semester": 3, "section": "A", "dob": "2005-08-15"})
        if r is not None and r.status_code == 200:
            self.uid, self.csrf = r.json()["id"], r.json()["csrf"]
        elif r is not None and r.status_code == 429:
            self.uid, tok, self.csrf = await asyncio.to_thread(fallback_account, self.i)
            self.c.cookies.set("astra_s", tok)
        else:
            return
        await call(self.c, "POST join", "POST", f"/api/student/competitions/{comp}/join", headers=self.h())

    async def arena(self, comp, name="GET arena"):
        r = await call(self.c, name, "GET", f"/api/student/competitions/{comp}/arena")
        if r is not None and r.status_code == 200:
            d = r.json()
            seen_draws.setdefault(self.uid, set()).add(tuple(p["id"] for p in d["problems"]))
            if not self.probs:
                self.probs = d["problems"]
            self.finished = self.finished or bool(d["finished_at"])

    async def background(self, comp, stop):
        """What the open arena page does by itself: refresh every 15 s, flush activity every 10 s, leaderboard tab for 1 in 3."""
        n = 0
        nxt_lb = time.time() + random.uniform(0, 15)
        nxt_ar = time.time() + 15
        while not stop.is_set():
            await asyncio.sleep(10 + random.uniform(-1, 1))
            n += 1
            ev = [{"type": "keystrokes", "detail": {"n": random.randint(5, 200)}}]
            if n % 3 == 0:
                ev += [{"type": "snapshot", "detail": {"language": "c", "code": "x" * random.randint(300, 2500)}}, {"type": "heartbeat"}]
            if random.random() < 0.05:
                ev.append({"type": random.choice(["tab_hidden", "blur", "paste"])})
            await call(self.c, "POST activity", "POST", "/api/student/activity", json={"competition_id": comp, "events": ev}, headers=self.h())
            if time.time() >= nxt_ar:
                await self.arena(comp)
                nxt_ar = time.time() + 15
            if self.i % 3 == 0 and time.time() >= nxt_lb:
                await call(self.c, "GET leaderboard", "GET", f"/api/competitions/{comp}/leaderboard")
                nxt_lb = time.time() + 15

    async def submit(self, comp, qid, lang, code):
        while True:
            r = await call(self.c, "POST submit", "POST", "/api/student/submissions", headers=self.h(),
                           json={"competition_id": comp, "question_id": qid, "language": lang, "code": code, "kind": "submit"})
            if r is None:
                return
            if r.status_code == 429:  # cooldown: the page tells the student to wait
                await asyncio.sleep(3)
                continue
            if r.status_code != 200:
                return
            sid, t0, waited = r.json()["id"], time.time(), 0
            self.pending = True
            while time.time() - t0 < 600:
                await asyncio.sleep(0.7 if waited < 5 else 1.5)
                waited += 0.7
                s = await call(self.c, "GET verdict", "GET", f"/api/student/submissions/{sid}")
                if s is not None and s.status_code == 200 and s.json()["status"] in ("done", "error"):
                    break
            self.pending = False
            return

    async def solve(self, comp, key, end_solving):
        rnd = random.Random(self.i)
        for p in self.probs:
            if self.finished or time.time() > end_solving:
                return
            kind, ans = key[p["id"]]
            if kind == "mcq":
                await asyncio.sleep(rnd.uniform(5, 25))
                pick = ans if rnd.random() < 0.7 else rnd.choice([o for o in p["options"] if o.strip() != ans] or [ans])
                await self.submit(comp, p["id"], "answer", pick)
                continue
            plan = rnd.choices([["AC"], ["WA", "AC"], ["CE", "WA", "AC"], ["WA", "WA"], ["CE", "AC"]], [35, 25, 10, 20, 10])[0]
            if self.i % 50 == 0 and p is next(q for q in self.probs if key[q["id"]][0] == "c"):
                plan = ["LOOP"] + plan
            for step in plan:
                await asyncio.sleep(rnd.uniform(20, 60))   # think / type
                if self.finished or time.time() > end_solving:
                    return
                await self.submit(comp, p["id"], "c", {"AC": ans, "WA": WA_C, "CE": CE_C, "LOOP": LOOP_C}[step])

    async def violations(self, comp, start):
        k = 3 if self.i % 100 == 7 else (1 if self.i % 20 == 3 else 0)
        for _ in range(k):
            await asyncio.sleep(random.uniform(10, DUR * 0.6) if k == 1 else random.uniform(20, 40))
            r = await call(self.c, "POST activity", "POST", "/api/student/activity", headers=self.h(),
                           json={"competition_id": comp, "events": [{"type": "violation", "detail": {"why": "left the test"}}]})
            if r is not None and r.status_code == 200 and r.json().get("finished"):
                self.finished = True

    async def finish(self, comp, at):
        await asyncio.sleep(max(0, at - time.time()))
        for _ in range(40):
            while self.pending:
                await asyncio.sleep(0.5)
            r = await call(self.c, "POST finish", "POST", f"/api/student/competitions/{comp}/finish", headers=self.h())
            if r is None or r.status_code != 409:
                self.finished = True
                return
            await asyncio.sleep(2)

    async def live(self, comp, key, t_start):
        stop = asyncio.Event()
        bg = asyncio.create_task(self.background(comp, stop))
        end = t_start + DUR
        fin_at = end - random.uniform(5, 60)
        await asyncio.gather(self.solve(comp, key, fin_at - 3), self.violations(comp, t_start))
        await self.finish(comp, fin_at)
        await asyncio.sleep(max(0, end - time.time()))
        stop.set()
        bg.cancel()


def rss_tree(pid):
    out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,rss="], capture_output=True, text=True).stdout.split("\n")
    rows = [tuple(map(int, l.split())) for l in out if l.strip()]
    kids, todo, total = {}, [pid], 0
    for p, pp, rss in rows:
        kids.setdefault(pp, []).append((p, rss))
    rssof = {p: rss for p, _, rss in rows}
    while todo:
        p = todo.pop()
        total += rssof.get(p, 0)
        todo += [k for k, _ in kids.get(p, [])]
    return total / 1024


async def sample_mem(web, wk, stop):
    while not stop.is_set():
        fds = subprocess.run(["lsof", "-p", str(web.pid)], capture_output=True, text=True).stdout.count("\n")
        mem.append((round(time.time() - T0), rss_tree(web.pid) if web.poll() is None else -1, rss_tree(wk.pid), fds))
        await asyncio.sleep(5)


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else 0


def limit_fds():
    if NOFILE:
        resource.setrlimit(resource.RLIMIT_NOFILE, (NOFILE, resource.getrlimit(resource.RLIMIT_NOFILE)[1]))


async def main():
    setup_admin()
    log_web, log_wk = open(TMP / "web.log", "w"), open(TMP / "worker.log", "w")
    web = subprocess.Popen([sys.executable, "-m", "app.main"], cwd=ROOT, env=ENV, stdout=log_web, stderr=subprocess.STDOUT, preexec_fn=limit_fds)
    wk = subprocess.Popen([sys.executable, "-m", "app.worker"], cwd=ROOT, env=ENV, stdout=log_wk, stderr=subprocess.STDOUT)
    print(f"web pid {web.pid}, worker pid {wk.pid}, data {TMP}", flush=True)
    stop_mem = asyncio.Event()
    memt = asyncio.create_task(sample_mem(web, wk, stop_mem))
    try:
        for _ in range(200):
            try:
                if httpx.get(f"{BASE}/api/health").status_code == 200:
                    break
            except httpx.HTTPError:
                await asyncio.sleep(0.1)
        qids, key = pick_questions()
        assert len(qids) == 12, qids
        adm = httpx.AsyncClient(base_url=BASE, timeout=60)
        me = (await adm.post("/api/admin/login", json={"username": "root", "password": "superpass123"})).json()
        adm.headers["X-CSRF-Token"] = me["csrf"]
        comp = (await adm.post("/api/admin/competitions", json={
            "title": "Load Round", "code": "LOAD-ARENA", "start_at": int(time.time()) + 3600, "duration_min": 60, "languages": ["c"],
            "leaderboard_visibility": "live", "draw": 5, "shuffle": True, "ai_pool": False, "lockdown": {"max_violations": 3},
            "scoring": {"mode": "partial", "negative_marks": 0, "first_blood_bonus": 0, "penalty_minutes": 10, "tie_break": "penalty"}})).json()
        cid = comp["id"]
        r = await adm.put(f"/api/admin/competitions/{cid}/questions", json={"questions": [{"question_id": q, "marks": 10 if key[q][0] == "mcq" else 100} for q in qids]})
        assert r.status_code == 200, r.text
        r = await adm.post(f"/api/admin/competitions/{cid}/action", json={"action": "publish"})
        assert r.status_code == 200, r.text
        studs = [Student(i) for i in range(N)]
        t = time.time()
        for w in range(0, N, 50):
            await asyncio.gather(*(s.register(cid) for s in studs[w:w + 50]))
        studs = [s for s in studs if s.uid]
        print(f"registered+joined {len(studs)} in {time.time() - t:.1f}s; starting round", flush=True)
        r = await adm.post(f"/api/admin/competitions/{cid}/action", json={"action": "start"})
        assert r.status_code == 200, r.text
        t = time.time()
        await asyncio.gather(*(s.arena(cid, "GET arena (burst)") for s in studs))
        print(f"arena burst for {len(studs)} took {time.time() - t:.1f}s", flush=True)
        t_start = time.time()
        await asyncio.gather(*(s.live(cid, key, t_start) for s in studs))
        print(f"live phase done after {time.time() - t_start:.0f}s; draining queue", flush=True)
        t = time.time()
        c = db.connect()
        while time.time() - t < 600 and db.one(c, "SELECT COUNT(*) AS n FROM submissions WHERE status IN ('queued','running')")["n"]:
            await asyncio.sleep(1)
        drain = time.time() - t
        lb1 = (await adm.get(f"/api/competitions/{cid}/leaderboard")).json()
        lb2 = (await adm.get(f"/api/competitions/{cid}/leaderboard")).json()
        await adm.post(f"/api/admin/competitions/{cid}/action", json={"action": "end"})
    finally:
        stop_mem.set()
        await asyncio.sleep(0)
        web.terminate()
        wk.terminate()
        web.wait(10)
        wk.wait(10)
        memt.cancel()
    report(cid, qids, key, lb1, lb2, drain, studs)


def report(cid, qids, key, lb1, lb2, drain, studs):
    c = db.connect()
    print("\n== latency (s) ==")
    for k in sorted(lat):
        v = lat[k]
        print(f"  {k:20s} n={len(v):6d} p50={statistics.median(v):6.3f} p95={pct(v, .95):6.3f} p99={pct(v, .99):6.3f} max={max(v):6.3f}  {dict(sorted(status[k].items(), key=str))}")
    sub = db.all_(c, "SELECT * FROM submissions WHERE competition_id=?", cid)
    by = {}
    for s in sub:
        by.setdefault((s["status"], s["verdict"]), 0)
        by[(s["status"], s["verdict"])] += 1
    print(f"\n== judge == submissions {len(sub)}: {by}; queue drained {drain:.0f}s after the round")
    judged = [s for s in sub if s["judged_at"]]
    wait = [s["started_at"] - s["created_at"] for s in judged if s["started_at"]]
    total = [s["judged_at"] - s["created_at"] for s in judged]
    print(f"  queue wait p50={statistics.median(wait)}s p95={pct(wait, .95)}s max={max(wait)}s   submit->verdict p50={statistics.median(total)}s p95={pct(total, .95)}s max={max(total)}s")
    per_min = {}
    for s in judged:
        per_min[s["judged_at"] // 60] = per_min.get(s["judged_at"] // 60, 0) + 1
    span = (max(s["judged_at"] for s in judged) - min(s["created_at"] for s in judged)) / 60
    print(f"  judged/min: peak {max(per_min.values())}, avg {len(judged) / max(span, 1e-9):.0f} over {span:.1f} min  (by minute: {[per_min[k] for k in sorted(per_min)]})")
    by_lang = {}
    for s in judged:
        if s["started_at"]:
            by_lang.setdefault((s["language"], s["verdict"]), []).append(s["judged_at"] - s["started_at"])
    print("  judge seconds per job:", {f"{l}/{v}": round(statistics.mean(x), 2) for (l, v), x in sorted(by_lang.items())})
    stuck = [s["id"] for s in sub if s["status"] != "done"]
    print(f"  not done: {len(stuck)} {stuck[:10]}; attempts>1: {sum(1 for s in sub if s['attempts'] > 1)}")
    errs = db.all_(c, "SELECT source, message FROM error_log")
    print(f"\n== errors == error_log rows: {len(errs)}")
    for e in errs[:6]:
        print("   ", e["source"], e["message"][-300:].replace("\n", " | "))
    for f in ("web.log", "worker.log"):
        txt = (TMP / f).read_text(errors="replace")
        print(f"  {f}: 'database is locked' x{txt.count('database is locked')}, Traceback x{txt.count('Traceback')}, 'Too many open files' x{txt.count('Too many open files')}")
    print(f"  unexpected client-side failures: {len(problems)}")
    for p in problems[:12]:
        print("   ", p)
    print("\n== memory (MB) / server fds ==")
    if mem:
        print(f"  server rss max {max(m[1] for m in mem):.0f}, last {mem[-1][1]:.0f}; worker tree rss max {max(m[2] for m in mem):.0f}; server fds max {max(m[3] for m in mem)}")
    # draws
    print("\n== checks ==")
    parts = db.all_(c, "SELECT user_id, questions FROM competition_participants WHERE competition_id=?", cid)
    pool, bad, mixes = set(qids), [], {}
    for p in parts:
        ids = db.jl(p["questions"])
        if len(ids) != 5 or len(set(ids)) != 5 or not set(ids) <= pool:
            bad.append((p["user_id"], ids))
        mixes[sum(1 for q in ids if key[q][0] == "mcq")] = mixes.get(sum(1 for q in ids if key[q][0] == "mcq"), 0) + 1
        if p["user_id"] in seen_draws and seen_draws[p["user_id"]] != {tuple(ids)}:
            bad.append((p["user_id"], "arena order changed", seen_draws[p["user_id"]]))
    print(f"  draws: {len(parts)} participants, bad={len(bad)} {bad[:3]}; mcq-per-student mix {mixes}; distinct orders {len({tuple(db.jl(p['questions'])) for p in parts})}")
    fin = db.one(c, "SELECT COUNT(*) AS n FROM participant_finish WHERE competition_id=?", cid)["n"]
    auto = db.one(c, "SELECT COUNT(*) AS n FROM activity_events WHERE competition_id=? AND type='auto_finished'", cid)["n"]
    print(f"  finished: {fin}/{len(parts)} (auto-finished by violations: {auto})")
    own = {p["user_id"]: set(db.jl(p["questions"])) for p in parts}
    best = {}
    for s in sub:
        if s["kind"] == "submit" and s["status"] == "done" and s["verdict"] not in ("IE", "CE") and s["question_id"] in own.get(s["user_id"], ()):
            k = (s["user_id"], s["question_id"])
            best[k] = max(best.get(k, 0), s["score"])
    exp = {}
    for (u, q), v in best.items():
        exp[u] = exp.get(u, 0) + v
    marks = {r["question_id"]: r["marks"] for r in db.all_(c, "SELECT question_id, marks FROM competition_questions WHERE competition_id=?", cid)}
    for r in db.all_(c, "SELECT user_id, COUNT(*) AS n FROM activity_events WHERE competition_id=? AND type='violation' GROUP BY user_id", cid):
        u = r["user_id"]   # default violation_pct = 5% of the student's max marks per warning
        exp[u] = round(exp.get(u, 0) - round(min(exp.get(u, 0), r["n"] * 0.05 * sum(marks[q] for q in own[u])), 2), 2)
    got = {r["name"]: r["score"] for r in lb1["rows"]}
    names = {r["user_id"]: r["name"] for r in db.all_(c, "SELECT u.id AS user_id, u.name FROM users u")}
    mism = [(names[u], exp.get(u, 0), got.get(names[u])) for u in own if abs(exp.get(u, 0) - (got.get(names[u]) or 0)) > 1e-6]
    print(f"  leaderboard: {len(lb1['rows'])} rows, mismatches vs independent recompute: {len(mism)} {mism[:3]}; repeat identical: {lb1['rows'] == lb2['rows']}")


if __name__ == "__main__":
    asyncio.run(main())
