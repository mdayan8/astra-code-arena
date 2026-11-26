"""Judge worker: `python -m app.worker` (starts JUDGE_WORKERS processes).
The submissions table is the queue, so nothing is lost on restart: jobs left 'running' by a dead worker are re-queued."""
import json
from pathlib import Path
import multiprocessing as mp
import os
import shutil
import signal
import socket
import sqlite3
import sys
import time
import traceback

from . import config, contest, db, judge

HEARTBEAT_STALE = 60
MAX_ATTEMPTS = 3


def heartbeat(c, name, state):
    db.set_setting(c, f"worker:{name}", json.dumps({"at": db.now(), "state": state, "pid": os.getpid(),
                                                       "sandbox": judge.sandbox_mode()}))


def recover(c):
    """Re-queue jobs whose worker stopped heart-beating."""
    for r in db.all_(c, "SELECT id, worker FROM submissions WHERE status='running'"):
        hb = db.jl(db.setting(c, f"worker:{r['worker']}", ""), {})
        if not hb or db.now() - hb.get("at", 0) > HEARTBEAT_STALE:
            c.execute("UPDATE submissions SET status='queued', worker=NULL WHERE id=? AND status='running'", (r["id"],))


def claim(c, name):
    r = c.execute("UPDATE submissions SET status='running', worker=?, started_at=?, attempts=attempts+1 "
                  "WHERE id=(SELECT id FROM submissions WHERE status='queued' ORDER BY CASE kind WHEN 'run' THEN 0 ELSE 1 END, id LIMIT 1) "
                  "AND status='queued' RETURNING id", (name, db.now())).fetchone()
    return r[0] if r else None


def load_tests(c, sub):
    is_admin = (db.one(c, "SELECT kind FROM users WHERE id=?", sub["user_id"]) or {}).get("kind") == "admin"
    if sub["kind"] == "run" and not (is_admin and sub["custom_input"] is None):  # admins' "try" runs see every test
        tests = db.all_(c, "SELECT id, ordinal, input, expected, visibility, weight FROM test_cases WHERE version_id=? AND visibility='sample' ORDER BY ordinal, id", sub["version_id"])
        if sub["custom_input"] is not None:
            tests = [{"id": None, "ordinal": 0, "input": sub["custom_input"], "expected": None, "visibility": "custom", "weight": 0}]
        return tests
    tests = db.all_(c, "SELECT id, ordinal, input, expected, visibility, weight FROM test_cases WHERE version_id=? ORDER BY CASE visibility WHEN 'sample' THEN 0 ELSE 1 END, ordinal, id", sub["version_id"])
    if sub["kind"] == "validate" and sub["fill_expected"]:
        for t in tests:
            if not (t["expected"] or "").strip():
                t["expected"] = None
    return tests


def store(c, sub, res, comp, keep_full=False):
    tests_w = sum(r["weight"] for r in res["results"] if r["visibility"] != "custom") or 0
    pass_w = sum(r["weight"] for r in res["results"] if r["verdict"] in ("AC", "OK") and r["visibility"] != "custom")
    passed = sum(1 for r in res["results"] if r["verdict"] in ("AC", "OK"))
    score = 0.0
    if sub["kind"] == "submit" and comp:
        cq = db.one(c, "SELECT marks FROM competition_questions WHERE competition_id=? AND question_id=?", sub["competition_id"], sub["question_id"])
        marks = cq["marks"] if cq else 0
        mode = contest.scoring_cfg(comp)["mode"]
        if res["verdict"] == "AC":
            score = float(marks)
        elif mode == "partial" and tests_w and res["verdict"] != "CE":
            score = round(marks * pass_w / tests_w, 2)
    with db.tx(c):
        c.execute("DELETE FROM judging_results WHERE submission_id=?", (sub["id"],))
        for r in res["results"]:
            c.execute("INSERT INTO judging_results(submission_id,test_case_id,ordinal,visibility,verdict,weight,time_ms,memory_kb,output,stderr) VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (sub["id"], r["test_case_id"], r["ordinal"], r["visibility"], r["verdict"], r["weight"], r["time_ms"], r["memory_kb"],
                       r["output"] if (sub["kind"] == "validate" or keep_full) else r["output"][:4000], r["stderr"]))
        run_output = None
        if sub["kind"] == "run" and res["results"]:
            run_output = json.dumps([{"verdict": r["verdict"], "output": r["output"][:4000], "stderr": r["stderr"][:1000]} for r in res["results"]])
        c.execute("UPDATE submissions SET status='done', verdict=?, score=?, passed=?, total=?, time_ms=?, memory_kb=?, compile_output=?, run_output=?, judged_at=? WHERE id=?",
                  (res["verdict"], score, passed, len(res["results"]), res["time_ms"], res["memory_kb"], res["compile_output"], run_output, db.now(), sub["id"]))
        if sub["kind"] == "validate":
            apply_validation(c, sub, res)


def apply_validation(c, sub, res):
    v = db.one(c, "SELECT * FROM question_versions WHERE id=?", sub["version_id"])
    if not v:
        return
    ok = res["verdict"] in ("AC", "OK") and bool(res["results"])
    for r in res["results"]:
        if r["test_case_id"] is None:
            continue
        if r["verdict"] == "OK":  # filled expected output from the reference
            c.execute("UPDATE test_cases SET expected=?, validated=1 WHERE id=? AND TRIM(expected)=''", (r["output"], r["test_case_id"]))
        else:
            c.execute("UPDATE test_cases SET validated=? WHERE id=?", (1 if r["verdict"] == "AC" else 0, r["test_case_id"]))
    val = db.jl(v["validation"], {}) or {}
    if any(r["verdict"] == "OK" for r in res["results"]):
        val = {}  # expected outputs changed: other reference languages must re-validate against them
    val[sub["language"]] = {"verdict": res["verdict"], "passed": sum(1 for r in res["results"] if r["verdict"] in ("AC", "OK")),
                            "total": len(res["results"]), "at": db.now(), "compile_output": (res["compile_output"] or "")[:2000],
                            "failed": [r["ordinal"] for r in res["results"] if r["verdict"] not in ("AC", "OK")][:20]}
    refs = [r["language"] for r in db.all_(c, "SELECT language FROM reference_solutions WHERE version_id=?", v["id"])]
    hidden = db.one(c, "SELECT COUNT(*) AS n FROM test_cases WHERE version_id=? AND visibility='hidden'", v["id"])["n"]
    empty = db.one(c, "SELECT COUNT(*) AS n FROM test_cases WHERE version_id=? AND TRIM(expected)=''", v["id"])["n"]
    all_ok = ok and refs and hidden > 0 and empty == 0 and all(val.get(l, {}).get("verdict") in ("AC", "OK") for l in refs)
    c.execute("UPDATE question_versions SET validation=?, validated_at=? WHERE id=?", (json.dumps(val), db.now() if all_ok else None, v["id"]))
    if sub["fill_expected"] and ok:  # cross-check the freshly filled outputs with every other reference solution
        for r in db.all_(c, "SELECT language, code FROM reference_solutions WHERE version_id=? AND language!=?", v["id"], sub["language"]):
            c.execute("INSERT INTO submissions(competition_id,user_id,question_id,version_id,kind,language,code,fill_expected,created_at) VALUES(NULL,?,?,?,'validate',?,?,0,?)",
                      (sub["user_id"], sub["question_id"], v["id"], r["language"], r["code"], db.now()))


def process(c, sid, name, slot):
    sub = db.one(c, "SELECT * FROM submissions WHERE id=?", sid)
    v = db.one(c, "SELECT time_limit_ms, memory_mb FROM question_versions WHERE id=?", sub["version_id"])
    comp = db.one(c, "SELECT * FROM competitions WHERE id=?", sub["competition_id"]) if sub["competition_id"] else None
    tests = load_tests(c, sub)
    if not tests:
        res = {"verdict": "IE", "compile_output": "No test cases configured for this problem.", "results": [], "time_ms": 0, "memory_kb": 0}
        store(c, sub, res, comp)
        return
    uid = config.JUDGE_UID_BASE + slot if judge.IS_ROOT else None
    keep_full = sub["kind"] == "run" and sub["competition_id"] is None and \
        (db.one(c, "SELECT kind FROM users WHERE id=?", sub["user_id"]) or {}).get("kind") == "admin"  # test generators
    res = judge.run_job({"language": sub["language"], "code": sub["code"], "time_limit_ms": v["time_limit_ms"], "memory_mb": v["memory_mb"]},
                        tests, config.JUDGE_TMP / f"w{slot}", uid)
    store(c, sub, res, comp, keep_full)


def fail_or_retry(c, sid, err):
    db.log_error(c, "judge", f"submission {sid}: {err}")
    sub = db.one(c, "SELECT attempts FROM submissions WHERE id=?", sid)
    if sub and sub["attempts"] < MAX_ATTEMPTS:
        c.execute("UPDATE submissions SET status='queued', worker=NULL WHERE id=?", (sid,))
    else:
        c.execute("UPDATE submissions SET status='error', verdict='IE', compile_output=?, judged_at=? WHERE id=?",
                  ("The judge hit an internal problem. This is not counted against you; organisers can re-judge it.", db.now(), sid))


def maintenance(c, state):
    """Housekeeping done by worker slot 0: competition auto start/end, expired sessions, daily backup."""
    t = db.now()
    if t - state.get("stats", 0) >= 30:
        state["stats"] = t
        from . import hostinfo
        db.set_setting(c, "host:stats", json.dumps(hostinfo.usage()))
    if t - state.get("tick", 0) >= 5:
        state["tick"] = t
        for comp in db.all_(c, "SELECT * FROM competitions WHERE status IN ('published','live')"):
            contest.tick(c, comp)
        recover(c)
    if t - state.get("cleanup", 0) >= 900:  # every 15 min (backup itself is hourly/6-hourly, see below)
        state["cleanup"] = t
        c.execute("DELETE FROM sessions WHERE expires_at < ?", (t,))
        db_maintenance(c, t)
        last = int(db.setting(c, "backup:last_at", "0") or 0)
        live = db.one(c, "SELECT 1 AS x FROM competitions WHERE status IN ('live','paused') LIMIT 1")
        if t - last >= (3600 if live else config.BACKUP_EVERY_H * 3600):
            backup(c)


def integrity_check(c):
    r = c.execute("PRAGMA quick_check").fetchall()
    ok = len(r) == 1 and r[0][0] == "ok"
    db.set_setting(c, "db:integrity", json.dumps({"at": db.now(), "ok": ok, "detail": "ok" if ok else "; ".join(x[0] for x in r[:5])}))
    if not ok:
        db.log_error(c, "database", "integrity check failed: " + "; ".join(x[0] for x in r[:5]))
    return ok


def db_maintenance(c, t):
    """Hourly: fold the WAL back into the main file; daily: refresh query-planner stats; weekly: integrity check."""
    try:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        if t - int(db.setting(c, "db:optimized_at", "0") or 0) >= 24 * 3600:
            c.execute("PRAGMA optimize")
            db.set_setting(c, "db:optimized_at", t)
        if t - (db.jl(db.setting(c, "db:integrity", ""), {}) or {}).get("at", 0) >= 7 * 24 * 3600:
            integrity_check(c)
    except sqlite3.Error as e:
        db.log_error(c, "database", e)


def backup(c, label="auto"):
    name = time.strftime(f"astra-%Y%m%d-%H%M%S-{label}.db")
    dest = config.BACKUP_DIR / name
    b = sqlite3.connect(dest)
    try:
        c.backup(b)
    finally:
        b.close()
    os.chmod(dest, 0o600)
    if not db._healthy(dest):  # never keep (or trust) a broken backup
        dest.unlink(missing_ok=True)
        db.set_setting(c, "backup:failed_at", db.now())
        db.log_error(c, "backup", "backup failed its integrity check; previous backups kept")
        raise RuntimeError("backup verification failed")
    if config.BACKUP_MIRROR:
        try:
            m = Path(config.BACKUP_MIRROR)
            m.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, m / name)
            for f in sorted(m.glob("astra-*.db"))[:-config.BACKUP_KEEP]:
                f.unlink(missing_ok=True)
        except OSError as e:
            db.log_error(c, "backup", f"mirror copy failed ({config.BACKUP_MIRROR}): {e}")
            db.set_setting(c, "backup:mirror_failed_at", db.now())
    files = sorted(config.BACKUP_DIR.glob("astra-*.db"))
    for f in files[:-config.BACKUP_KEEP]:
        f.unlink(missing_ok=True)
    db.set_setting(c, "backup:last_at", db.now())
    db.set_setting(c, "backup:last_file", name)
    return name


def loop(slot):
    name = f"{socket.gethostname()}-{slot}"
    c = db.connect()
    state = {}
    stop = {"v": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(v=True))
    if judge.IS_ROOT:
        config.JUDGE_TMP.mkdir(parents=True, exist_ok=True)
        os.chmod(config.JUDGE_TMP, 0o711)
    # A slot that just started cannot be judging anything yet: jobs still marked as ours belong to a previous run of this
    # slot that was killed (power cut, crash). Re-queue them now; the heartbeat check alone would never fire, because the
    # restarted slot keeps the same name and heart-beats again at once.
    c.execute("UPDATE submissions SET status='queued', worker=NULL WHERE status='running' AND worker=?", (name,))
    last_hb = 0
    while not stop["v"]:
        try:
            if time.time() - last_hb > 10:
                heartbeat(c, name, "idle")
                last_hb = time.time()
            if slot == 0:
                maintenance(c, state)
            sid = claim(c, name)
            if not sid:
                time.sleep(0.25)
                continue
            heartbeat(c, name, f"judging #{sid}")
            try:
                process(c, sid, name, slot)
            except Exception as e:  # infra failure: retry, never mark as WA
                fail_or_retry(c, sid, f"{e}\n{traceback.format_exc()[-1500:]}")
        except sqlite3.OperationalError as e:
            db.log_error(c, "worker", e)
            time.sleep(1)
        except Exception as e:
            db.log_error(c, "worker", traceback.format_exc()[-2000:])
            time.sleep(1)


def main():
    db.init()
    config.JUDGE_TMP.mkdir(parents=True, exist_ok=True)
    print(f"[judge] sandbox: {judge.sandbox_mode()}")
    print(f"[judge] languages: {judge.available_languages()}")
    if judge.IS_ROOT:  # sandbox uids must be able to traverse every parent of JUDGE_TMP
        for d in [config.JUDGE_TMP, *config.JUDGE_TMP.parents]:
            if not os.stat(d).st_mode & 0o001:
                sys.exit(f"[judge] {d} is not traversable by sandbox users (needs o+x). Choose another JUDGE_TMP.")
    print(f"[judge] starting {config.JUDGE_WORKERS} worker(s)")
    procs = []
    for i in range(config.JUDGE_WORKERS):
        p = mp.Process(target=loop, args=(i,), daemon=False)
        p.start()
        procs.append(p)

    def shutdown(*_):
        for p in procs:
            p.terminate()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    # supervise: restart any worker that dies
    while procs:
        for i, p in enumerate(procs):
            p.join(timeout=1)
            if p.exitcode is not None and p.exitcode not in (0, -signal.SIGTERM):
                print(f"[judge] worker {i} died ({p.exitcode}); restarting")
                procs[i] = mp.Process(target=loop, args=(i,))
                procs[i].start()
            elif p.exitcode is not None:
                procs = [q for q in procs if q.exitcode is None]
                break


if __name__ == "__main__":
    shutil.rmtree(config.JUDGE_TMP, ignore_errors=True)
    main()
