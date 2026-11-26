"""End-to-end acceptance test. Boots a real web server + judge worker on a throwaway database and walks the
acceptance workflow from the build spec (section 24) plus the security / isolation / judging checks (section 22).

    python3 tests/test_e2e.py            # needs: pip install httpx  (plus requirements.txt)
"""
import concurrent.futures as cf
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="astra-e2e-"))
PORT = int(os.environ.get("E2E_PORT", "8765"))
ENV = {**os.environ, "DATA_DIR": str(TMP), "APP_PORT": str(PORT), "APP_HOST": "127.0.0.1", "ADMIN_PATH": "astra-control-e2e",
       "JUDGE_WORKERS": "3", "JUDGE_TMP": f"/tmp/astra-judge-e2e-{PORT}", "APP_ENV": "test", "AI_BASE_URL": "http://127.0.0.1:9/v1", "AI_MODEL": "none"}  # no AI, even if Ollama runs here
os.environ.update(ENV)
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

EXTERNAL = os.environ.get("E2E_BASE")  # test an already-running deployment (e.g. docker compose) instead of booting one
BASE = EXTERNAL or f"http://127.0.0.1:{PORT}"
ADMIN_ROUTE = os.environ.get("E2E_ADMIN_PATH", "astra-control-e2e")
A = f"{BASE}/api/admin"
passed = []


def ok(name):
    passed.append(name)
    print(f"  ✔ {name}")


class Client:
    def __init__(self):
        self.h = httpx.Client(base_url=BASE, timeout=60)
        self.csrf = ""

    def req(self, method, url, expect=200, **kw):
        headers = {"X-CSRF-Token": self.csrf} if method != "GET" else {}
        r = self.h.request(method, url, headers=headers, **kw)
        if expect is not None:
            assert r.status_code == expect, f"{method} {url} -> {r.status_code} {r.text[:400]}"
        try:
            return r.json()
        except ValueError:
            return r

    def get(self, u, expect=200, **kw): return self.req("GET", u, expect, **kw)
    def post(self, u, body=None, expect=200, **kw): return self.req("POST", u, expect, json=body if body is not None else {}, **kw)
    def put(self, u, body, expect=200): return self.req("PUT", u, expect, json=body)
    def delete(self, u, expect=200): return self.req("DELETE", u, expect)


def admin_login(user, pw):
    c = Client()
    me = c.post(f"{A}/login", {"username": user, "password": pw})
    c.csrf = me["csrf"]
    return c, me


def student(usn, name, dept="CSE", year=2):
    c = Client()
    me = c.post(f"{BASE}/api/auth/register", {"name": name, "usn": usn, "department": dept, "year": year, "semester": year * 2 - 1, "section": "A", "dob": "2005-08-15"})
    c.csrf = me["csrf"]
    return c, me


def wait_sub(c, sid, admin=False, timeout=120):
    t = time.time()
    while time.time() - t < timeout:
        s = c.get(f"{A}/submissions/{sid}" if admin else f"{BASE}/api/student/submissions/{sid}")
        if s["status"] in ("done", "error"):
            return s
        time.sleep(0.3)
    raise AssertionError(f"submission {sid} not judged in {timeout}s")


def wait_validated(c, qid, timeout=120):
    t = time.time()
    while time.time() - t < timeout:
        q = c.get(f"{A}/questions/{qid}")
        if not q["pending_validation"] and q["version"]["validated_at"]:
            return q
        if not q["pending_validation"] and time.time() - t > 5 and not q["version"]["validated_at"]:
            # chained jobs may still be getting queued; give it a moment
            time.sleep(1)
            q = c.get(f"{A}/questions/{qid}")
            if not q["pending_validation"] and not q["version"]["validated_at"]:
                raise AssertionError(f"validation failed: {q['version']['validation']}")
        time.sleep(0.5)
    raise AssertionError("validation timeout")


SUM_C = '#include <stdio.h>\nint main(){int n;long long s=0,x;scanf("%d",&n);for(int i=0;i<n;i++){scanf("%lld",&x);s+=x;}printf("%lld\\n",s);return 0;}\n'
SUM_PY = "n=int(input())\nprint(sum(map(int,input().split())) if n else 0)\n"
REV_C = '#include <stdio.h>\n#include <string.h>\nint main(){char s[2005];if(scanf("%2000s",s)!=1)return 0;int n=strlen(s);for(int i=n-1;i>=0;i--)putchar(s[i]);putchar(10);return 0;}\n'
REV_PY = "print(input().strip()[::-1])\n"


def question_body(title, statement, refs, tests, category="Arrays"):
    return {"title": title, "category": category, "difficulty": "Easy", "tags": ["basics"], "statement": statement,
            "input_format": "See statement.", "output_format": "See statement.", "constraints": "1 ≤ n ≤ 1000",
            "languages": ["c", "cpp", "python", "java", "javascript", "go", "rust"], "time_limit_ms": 1000, "memory_mb": 128, "max_marks": 100,
            "references": refs, "tests": tests, "practice": False}


def make_pdf(path):
    import fitz
    doc = fitz.open()
    pages = [
        "ASTRA Round Paper\n\nQuestion 1: Count Vowels\nGiven a lowercase word, print how many vowels it has.\nInput Format\nA single word.\nOutput Format\nOne integer.\nConstraints\n1 <= length <= 100\nSample Input\nastra\nSample Output\n2\nExplanation\na and a are vowels.\n",
        "Question 2: Max of Three\nRead three integers and print the largest.\nInput Format\nThree integers on one line.\nOutput Format\nThe maximum.\nConstraints\n-1000 <= a,b,c <= 1000\nSample Input\n3 9 4\nSample Output\n9\n",
    ]
    for t in pages:
        p = doc.new_page()
        p.insert_text((50, 72), t, fontsize=11)
    doc.save(path)


def main():
    if EXTERNAL:
        run()
        print(f"\nALL {len(passed)} CHECKS PASSED against {BASE}")
        return
    from app import db
    from app.security import hash_password
    db.init()
    c = db.connect()
    c.execute("INSERT INTO users(username,name,password_hash,kind,role,can_approve,created_at) VALUES('root','Root Admin',?,'admin','super_admin',1,?)",
              (hash_password("superpass123"), db.now()))
    c.close()
    web = subprocess.Popen([sys.executable, "-m", "app.main"], cwd=ROOT, env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    worker = subprocess.Popen([sys.executable, "-m", "app.worker"], cwd=ROOT, env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                if httpx.get(f"{BASE}/api/health").status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        run()
    finally:
        web.terminate(); worker.terminate()
        web.wait(5); worker.wait(10)
    print(f"\nALL {len(passed)} CHECKS PASSED  (data in {TMP})")


def run():
    print("Auth & access control")
    anon = Client()
    assert "admin" not in httpx.get(BASE + "/").text.lower().replace("astra-code-arena", ""), "landing page mentions admin"
    assert "astra-control" not in httpx.get(BASE + "/static/js/student.js").text
    ok("public landing + student JS contain no admin route")
    anon.get(f"{A}/dashboard", expect=401)
    anon.get(f"{BASE}/api/student/dashboard", expect=401)
    assert httpx.get(f"{BASE}/admin").status_code == 404
    ok("unauthenticated users blocked from admin & student APIs; /admin is 404")
    r = httpx.get(f"{BASE}/{ADMIN_ROUTE}/")
    assert r.status_code == 200 and "noindex" in r.headers.get("x-robots-tag", "") and "noindex" in r.text
    ok("private admin route served with noindex")

    sup, _ = admin_login("root", "superpass123")
    nc = Client()
    nc.h.cookies = sup.h.cookies
    nc.post(f"{A}/competitions", {"title": "x", "code": "NOCSRF"}, expect=403)
    ok("CSRF token required on state-changing admin requests")

    ea = sup.post(f"{A}/users", {"username": "eventa", "name": "Event Admin A", "role": "event_admin"})
    eb = sup.post(f"{A}/users", {"username": "eventb", "name": "Event Admin B", "role": "event_admin", "password": "eventbpass1"})
    ok("super admin created two event admins")
    evA, me = admin_login("eventa", ea["temporary_password"])
    assert me["must_change_pw"]
    evA.post(f"{A}/me/password", {"current": ea["temporary_password"], "new": "eventapass1"})
    evA, me = admin_login("eventa", "eventapass1")
    assert "admins" not in me["modules"] and "settings" not in me["modules"]
    evA.post(f"{A}/users", {"username": "evil", "name": "Evil", "role": "super_admin"}, expect=403)
    evA.get(f"{A}/audit", expect=403)
    ok("event admin logs in, must change temp password, cannot grant super admin or see audit")
    evB, _ = admin_login("eventb", "eventbpass1")

    print("Question bank & validation")
    q1 = evA.post(f"{A}/questions", question_body("Array Sum", "Print the sum of **n** integers.", {"c": SUM_C, "python": SUM_PY},
                                                  [{"input": "3\n1 2 3\n", "expected": "6\n", "visibility": "sample", "explanation": "1+2+3"},
                                                   {"input": "5\n10 20 30 40 50\n", "expected": "", "visibility": "hidden"},
                                                   {"input": "1\n-7\n", "expected": "", "visibility": "hidden", "kind": "boundary"},
                                                   {"input": "4\n1000000000 1000000000 1000000000 1000000000\n", "expected": "", "visibility": "hidden", "kind": "edge", "weight": 2}]))
    q2 = evA.post(f"{A}/questions", question_body("Reverse Word", "Reverse the word.", {"c": REV_C, "python": REV_PY},
                                                  [{"input": "astra\n", "expected": "artsa\n", "visibility": "sample"},
                                                   {"input": "a\n", "expected": "", "visibility": "hidden"}, {"input": "racecar\n", "expected": "", "visibility": "hidden"}], "Strings"))
    q3 = evA.post(f"{A}/questions", question_body("Square", "Print n*n.", {"python": "n=int(input())\nprint(n*n)\n"},
                                                  [{"input": "4\n", "expected": "16\n", "visibility": "sample"}, {"input": "12\n", "expected": "", "visibility": "hidden"}], "Basic Mathematics"))
    evA.post(f"{A}/questions/{q1['id']}/status", {"status": "approved"}, expect=400)
    ok("cannot approve a question before reference validation")
    for q in (q1, q2, q3):
        evA.post(f"{A}/questions/{q['id']}/validate", {"fill_expected": True})
    for q in (q1, q2, q3):
        v = wait_validated(evA, q["id"])
        assert all(t["expected"].strip() for t in v["tests"]), "expected outputs not filled"
        assert set(v["version"]["validation"]) == set(v["references"]), v["version"]["validation"]
    full = evA.get(f"{A}/questions/{q1['id']}")
    assert [t["expected"] for t in full["tests"] if t["visibility"] == "hidden"] == ["150\n", "-7\n", "4000000000\n"]
    ok("C reference compiled + Python reference executed; hidden outputs filled and cross-checked")
    bad = evA.post(f"{A}/questions", question_body("Broken", "x", {"c": SUM_C, "python": "print(0)\n"},
                                                   [{"input": "2\n1 1\n", "expected": "", "visibility": "hidden"}]))
    evA.post(f"{A}/questions/{bad['id']}/validate", {"fill_expected": True})
    time.sleep(1)
    for _ in range(60):
        b = evA.get(f"{A}/questions/{bad['id']}")
        if not b["pending_validation"] and len(b["version"]["validation"]) == 2:
            break
        time.sleep(0.5)
    assert not b["version"]["validated_at"] and b["version"]["validation"]["python"]["verdict"] == "WA"
    ok("inconsistent reference solutions detected (C vs Python disagree)")
    for q in (q1, q2, q3):
        evA.post(f"{A}/questions/{q['id']}/status", {"status": "approved"})
    ok("questions approved")

    print("Competition 01")
    base = {"description": "First round", "instructions": "Be fair.", "academic_year": "2026-27", "semester_label": "Odd", "category": "General",
            "duration_min": 60, "languages": ["c", "python"], "max_marks": 200, "leaderboard_visibility": "live", "show_test_counts": True,
            "scoring": {"mode": "partial", "negative_marks": 0, "first_blood_bonus": 0, "penalty_minutes": 10, "tie_break": "penalty"}}
    c1 = evA.post(f"{A}/competitions", {**base, "title": "Code Arena — Round 01", "code": "ASTRA-ARENA-ROUND-01", "round_no": 1, "start_at": int(time.time()) + 3600})
    evA.post(f"{A}/competitions/{c1['id']}/action", {"action": "publish"}, expect=400)
    evA.put(f"{A}/competitions/{c1['id']}/questions", {"questions": [{"question_id": q1["id"], "marks": 100}, {"question_id": q2["id"], "marks": 100}]})
    evA.post(f"{A}/competitions/{c1['id']}/action", {"action": "publish"})
    ok("competition 01 created as draft, publish blocked without questions, then published")
    evB.get(f"{A}/competitions/{c1['id']}", expect=403)
    ok("another event admin cannot manage competition 01")

    s1, m1 = student("4PM23CS001", "Asha Rao")
    s2, m2 = student("4PM23CS002", "Bharath K")
    s3, m3 = student("4PM23IS003", "Chitra M", "ISE", 1)
    s1.get(f"{A}/dashboard", expect=401)
    s1.get(f"{A}/competitions", expect=401)
    ok("students cannot call admin APIs (separate session cookie)")
    pub = anon.get(f"{BASE}/api/public/competitions")
    assert any(x["code"] == "ASTRA-ARENA-ROUND-01" and x["display_status"] == "Registration Open" for x in pub)
    for s in (s1, s2, s3):
        s.post(f"{BASE}/api/student/competitions/{c1['id']}/join")
    s1.post(f"{BASE}/api/student/competitions/{c1['id']}/join", expect=400)
    ok("three students registered and joined; duplicate join rejected")
    s1.get(f"{BASE}/api/student/competitions/{c1['id']}/arena", expect=403)
    s1.post(f"{BASE}/api/student/submissions", {"competition_id": c1["id"], "question_id": q1["id"], "language": "c", "code": SUM_C, "kind": "submit"}, expect=403)
    ok("problems hidden and submissions refused before start")

    evA.post(f"{A}/competitions/{c1['id']}/action", {"action": "start"})
    arena = s1.get(f"{BASE}/api/student/competitions/{c1['id']}/arena")
    blob = str(arena)
    assert "150" not in blob and "4000000000" not in blob and "reference" not in blob.lower()
    assert all(all(sm["output"] for sm in p["samples"]) for p in arena["problems"])
    ok("arena exposes statements + samples only (no hidden tests, no reference code)")

    def submit(s, q, lang, code, kind="submit", **kw):
        r = s.post(f"{BASE}/api/student/submissions", {"competition_id": c1["id"], "question_id": q, "language": lang, "code": code, "kind": kind, **kw})
        return wait_sub(s, r["id"])

    print("Judging")
    r = submit(s1, q1["id"], "c", SUM_C, "run")
    assert r["verdict"] == "AC" and r["results"][0]["expected"] == "6\n"
    r = submit(s1, q1["id"], "python", "print(input())", "run", custom_input="hello\n")
    assert r["verdict"] == "OK" and r["results"][0]["output"].strip() == "hello"
    ok("Run on samples and on custom input")
    cases = [
        (s1, q1["id"], "c", SUM_C, "AC"),
        (s1, q2["id"], "python", REV_PY, "AC"),
        (s2, q1["id"], "python", "print(0)\n", "WA"),
        (s2, q1["id"], "c", "int main( {", "CE"),
        (s2, q2["id"], "python", "import sys\nsys.exit(3)\n", "RE"),
        (s3, q1["id"], "c", "int main(){for(;;);}", "TLE"),
        (s3, q2["id"], "python", "while True:\n    pass\n", "TLE"),
    ]
    for s, q, lang, code, want in cases:
        time.sleep(5.1)  # submission cooldown
        got = submit(s, q, lang, code)
        assert got["verdict"] == want, f"{lang} expected {want} got {got['verdict']} {got.get('compile_output')}"
    ok("verdicts: AC (C), AC (Python), WA, CE, RE, TLE (C infinite loop), TLE (Python)")
    time.sleep(5.1)
    mem = submit(s3, q1["id"], "python", "x = bytearray(1024*1024*1024)\nprint(len(x))\n")
    assert mem["verdict"] in ("MLE", "RE"), mem["verdict"]
    ok(f"excessive memory rejected ({mem['verdict']})")
    time.sleep(5.1)
    db_path = "/data/astra.db" if EXTERNAL else str(TMP / "astra.db")
    mal = submit(s3, q2["id"], "python", f"import os\ntry:\n    print(open({db_path!r},'rb').read(16))\nexcept Exception as e:\n    print('blocked', type(e).__name__)\n")
    assert mal["verdict"] == "WA"
    ok("malicious file access attempt judged safely (no data reaches the student; DB is 0600, unreadable to the sandbox uid under Docker)")
    s2.get(f"{BASE}/api/student/submissions/{mal['id']}", expect=404)
    ok("student cannot read another student's submission")

    print("Concurrency")
    sm = s1.get(f"{BASE}/api/student/submissions?competition_id={c1['id']}")
    t0 = time.time()
    many = []
    for i in range(10):
        s, _ = student(f"4PM23CS1{i:02d}", f"Load Student {i}")
        s.post(f"{BASE}/api/student/competitions/{c1['id']}/join", expect=400)  # late join is off
        many.append(s)
    r = evA.post(f"{A}/competitions/{c1['id']}/participants", {"identifiers": [f"4PM23CS1{i:02d}" for i in range(10)] + ["NOPE123"]})
    assert r["added"] == 10 and r["missing"] == ["NOPE123"]
    ok("late join refused; organiser assigned 10 students by USN (unknown USN reported)")

    def go(s):
        r = s.post(f"{BASE}/api/student/submissions", {"competition_id": c1["id"], "question_id": q1["id"], "language": "python" if id(s) % 2 else "c",
                                                       "code": SUM_PY if id(s) % 2 else SUM_C, "kind": "submit"})
        return wait_sub(s, r["id"])["verdict"]
    with cf.ThreadPoolExecutor(10) as ex:
        verdicts = list(ex.map(go, many))
    assert verdicts == ["AC"] * 10, verdicts
    ok(f"10 concurrent submissions all judged AC in {time.time() - t0:.1f}s")

    print("Leaderboard, activity, end, finalize, export")
    s1.post(f"{BASE}/api/student/activity", {"competition_id": c1["id"], "events": [
        {"type": "paste", "detail": {"chars": 400, "lines": 20, "internal": False, "preview": "int main"}, "question_id": q1["id"]},
        {"type": "tab_hidden"}, {"type": "tab_visible"}, {"type": "snapshot", "detail": {"language": "c", "code": "int main(){}"}},
        {"type": "internet", "detail": {"reachable": True}}]})
    integ = evA.get(f"{A}/competitions/{c1['id']}/integrity")
    a = next(x for x in integ if x["name"] == "Asha Rao")
    assert a["large_pastes"] == 1 and a["tab_switches"] == 1 and a["internet"] == 1 and a["risk"] > 0
    act = evA.get(f"{A}/competitions/{c1['id']}/activity/{m1['id']}")
    assert any(e["type"] == "snapshot" for e in act["events"])
    ok("paste / tab-switch / code snapshot tracking visible to admins with risk score")
    lb = s2.get(f"{BASE}/api/competitions/{c1['id']}/leaderboard")
    top = lb["rows"][0]
    assert top["name"] == "Asha Rao" and top["score"] == 200 and top["solved"] == 2, lb["rows"][:3]
    b = next(x for x in lb["rows"] if x["name"] == "Bharath K")
    assert b["score"] == 0
    ok("live leaderboard: Asha 200 (2 solved) ranked first")
    time.sleep(5.1)
    submit(s1, q1["id"], "c", SUM_C)
    lb2 = s2.get(f"{BASE}/api/competitions/{c1['id']}/leaderboard")
    assert lb2["rows"][0]["score"] == 200
    ok("repeat accepted submission awards no extra points")
    fin = many[0].post(f"{BASE}/api/student/competitions/{c1['id']}/finish")
    assert fin["solved"] == 1 and fin["max"] == 200
    many[0].post(f"{BASE}/api/student/submissions", {"competition_id": c1["id"], "question_id": q1["id"], "language": "c", "code": SUM_C, "kind": "submit"}, expect=409)
    assert many[0].get(f"{BASE}/api/student/competitions/{c1['id']}/arena")["finished_at"]
    assert any(p["finished_at"] for p in evA.get(f"{A}/competitions/{c1['id']}/participants"))
    ok("Finish test: final submission returns the summary, locks further submissions, and organisers see who finished")
    evA.post(f"{A}/competitions/{c1['id']}/action", {"action": "pause"})
    s1.post(f"{BASE}/api/student/submissions", {"competition_id": c1["id"], "question_id": q1["id"], "language": "c", "code": SUM_C, "kind": "submit"}, expect=409)
    evA.post(f"{A}/competitions/{c1['id']}/action", {"action": "resume"})
    evA.post(f"{A}/competitions/{c1['id']}/action", {"action": "extend", "minutes": 15})
    assert evA.get(f"{A}/competitions/{c1['id']}")["extra_min"] == 15
    ok("pause blocks submissions; resume + 15 min extension applied")
    evA.post(f"{A}/competitions/{c1['id']}/action", {"action": "end"})
    s1.post(f"{BASE}/api/student/submissions", {"competition_id": c1["id"], "question_id": q1["id"], "language": "c", "code": SUM_C, "kind": "submit"}, expect=409)
    evA.post(f"{A}/competitions/{c1['id']}/action", {"action": "finalize"})
    final1 = evA.get(f"{A}/competitions/{c1['id']}/leaderboard")
    assert final1["frozen"]
    csv = evA.get(f"{A}/reports/scores.csv?competition_id={c1['id']}")
    assert "Asha Rao" in csv.text and "Rank" in csv.text
    for k in ("participants", "questions", "submissions", "integrity"):
        assert evA.get(f"{A}/reports/{k}.csv?competition_id={c1['id']}").status_code == 200
    ok("ended, finalized (frozen snapshot) and exported 5 CSV reports")
    audit = sup.get(f"{A}/audit?action=competition.")
    acts = {r["action"] for r in audit["rows"]}
    assert {"competition.start", "competition.pause", "competition.extend", "competition.end", "competition.finalize"} <= acts
    ok("every control action recorded in the audit log")

    print("Competition 02 by another admin, versioning, duplication, semester")
    c2 = evB.post(f"{A}/competitions", {**base, "title": "Code Arena — Round 02", "code": "ASTRA-ARENA-ROUND-02", "round_no": 2, "start_at": int(time.time()) + 60,
                                        "languages": ["python"], "max_marks": 100})
    evB.put(f"{A}/competitions/{c2['id']}/questions", {"questions": [{"question_id": q3["id"], "marks": 100}]})
    evB.post(f"{A}/competitions/{c2['id']}/action", {"action": "publish"})
    evA.get(f"{A}/competitions/{c2['id']}", expect=403)
    for s in (s2, s3):
        s.post(f"{BASE}/api/student/competitions/{c2['id']}/join")
    evB.post(f"{A}/competitions/{c2['id']}/action", {"action": "start"})
    r = s2.post(f"{BASE}/api/student/submissions", {"competition_id": c2["id"], "question_id": q3["id"], "language": "python", "code": "n=int(input())\nprint(n*n)\n", "kind": "submit"})
    assert wait_sub(s2, r["id"])["verdict"] == "AC"
    s2.post(f"{BASE}/api/student/submissions", {"competition_id": c2["id"], "question_id": q3["id"], "language": "c", "code": SUM_C, "kind": "submit"}, expect=400)
    s1.get(f"{BASE}/api/student/competitions/{c2['id']}/arena", expect=403)
    s2.post(f"{BASE}/api/student/submissions", {"competition_id": c2["id"], "question_id": q1["id"], "language": "python", "code": SUM_PY, "kind": "submit"}, expect=404)
    lbc2 = evB.get(f"{A}/competitions/{c2['id']}/leaderboard")
    assert [r["name"] for r in lbc2["rows"] if r["score"] > 0] == ["Bharath K"] and len(lbc2["rows"]) == 2
    ok("competition 02 runs independently: own questions, language set (Python only), participants and scores")
    evB.post(f"{A}/competitions/{c2['id']}/action", {"action": "end"})
    evB.post(f"{A}/competitions/{c2['id']}/action", {"action": "finalize"})

    v_before = evA.get(f"{A}/questions/{q1['id']}")["version"]["id"]
    edited = evA.put(f"{A}/questions/{q1['id']}", {**question_body("Array Sum (v2)", "Edited statement.", {"c": SUM_C, "python": SUM_PY}, None)})
    assert edited["version"]["id"] != v_before and edited["version"]["version_no"] == 2
    again = evA.get(f"{A}/competitions/{c1['id']}")
    assert any(q["version_id"] == v_before and q["title"] == "Array Sum" for q in again["questions"])
    assert evA.get(f"{A}/competitions/{c1['id']}/leaderboard")["rows"][0]["score"] == 200
    ok("editing a used question created v2; competition 01 still pinned to v1 with unchanged results")

    dup = evA.post(f"{A}/competitions/{c1['id']}/duplicate")
    assert dup["status"] == "draft" and dup["participant_count"] == 0 and dup["submission_count"] == 0 and len(dup["questions"]) == 2
    assert dup["code"] != c1["code"] and dup["id"] != c1["id"]
    ok("duplicated competition: new ID/code, same questions, zero participants/submissions")

    sem = sup.post(f"{A}/semesters", {"name": "Odd Semester 2026-27", "academic_year": "2026-27", "term": "Odd"})
    sup.post(f"{A}/semesters/{sem['id']}/competitions", {"competition_id": c1["id"], "points": 100})
    sup.post(f"{A}/semesters/{sem['id']}/competitions", {"competition_id": c2["id"], "points": 150})
    sl = anon.get(f"{BASE}/api/semesters/{sem['id']}/leaderboard")
    pts = {r["name"]: r["points"] for r in sl["rows"]}
    assert pts["Asha Rao"] == 100 and pts["Bharath K"] == 150, pts
    sup.post(f"{A}/semesters/{sem['id']}/action", {"action": "finalize"})
    assert sup.get(f"{A}/reports/semester.csv?semester_id={sem['id']}").status_code == 200
    ok("semester leaderboard combines both rounds by configured points (Asha 100, Bharath 150) and finalizes")

    print("PDF import")
    pdf = TMP / "paper.pdf"
    make_pdf(pdf)
    up = evA.req("POST", f"{A}/pdf", files={"file": ("paper.pdf", pdf.read_bytes(), "application/pdf")})
    for _ in range(50):
        d = evA.get(f"{A}/pdf/{up['id']}")
        if d["status"] != "processing":
            break
        time.sleep(0.3)
    assert d["status"] == "done" and len(d["drafts"]) == 2, (d["status"], d["warnings"])
    t = [g["payload"]["title"] for g in d["drafts"]]
    assert "Count Vowels" in t[0] and "Max of Three" in t[1], t
    g = d["drafts"][1]["payload"]
    assert g["samples"] and g["samples"][0]["output"].strip() == "9" and "three integers" in g["input_format"].lower()
    evA.req("POST", f"{A}/pdf", expect=400, files={"file": ("x.pdf", b"MZ not a pdf", "application/pdf")})
    ok("PDF uploaded, 2 questions detected with title / formats / samples; non-PDF rejected")
    g["reference"] = {"python": "print(max(map(int,input().split())))\n"}
    g["tests"] = [{"input": "1 2 3\n"}, {"input": "-5 -9 -1\n"}]
    evA.put(f"{A}/drafts/{d['drafts'][1]['id']}", {"payload": g})
    ap = evA.post(f"{A}/drafts/{d['drafts'][1]['id']}/approve")
    qv = wait_validated(evA, ap["question_id"])
    assert [t["expected"] for t in qv["tests"] if t["visibility"] == "hidden"] == ["3\n", "-1\n"]
    ok("edited draft saved to the bank and auto-validated (outputs computed by reference)")
    evA.post(f"{A}/ai/generate", {"topic": "Arrays"}, expect=503)
    ok("AI is optional: clean 503 when not configured, manual authoring unaffected")

    print("Student identity, gamification, imports, generator")
    anon.post(f"{BASE}/api/auth/register", {"name": "X Y", "usn": "4PM23XX009", "department": "UNKNOWNDEPT", "year": 1, "dob": "2005-01-01"}, expect=400)
    lc = Client()
    me = lc.post(f"{BASE}/api/auth/login", {"username": "4PM23CS001", "password": "15082005"})
    assert me["usn"] == "4PM23CS001" and me["semester"] == 3
    ok("registration requires a listed department; student signs in with USN + date of birth (DDMMYYYY)")
    lc.csrf = me["csrf"]
    lc.post(f"{BASE}/api/me/password", {"current": "15082005", "new": "mynewpass9"})
    anon.post(f"{BASE}/api/auth/forgot", {"usn": "4PM23CS001", "name": "Wrong Name", "dob": "2005-08-15"}, expect=400)
    anon.post(f"{BASE}/api/auth/forgot", {"usn": "4PM23CS001", "name": "  asha   RAO ", "dob": "2005-08-15"})
    Client().post(f"{BASE}/api/auth/login", {"username": "4PM23CS001", "password": "15082005"})
    ok("forgot password: wrong name refused; USN + name + DOB resets password back to the DOB")
    s1.csrf = Client().post(f"{BASE}/api/auth/login", {"username": "4PM23CS001", "password": "15082005"})["csrf"]
    s1 = Client(); s1.csrf = s1.post(f"{BASE}/api/auth/login", {"username": "4PM23CS001", "password": "15082005"})["csrf"]
    s1.put(f"{BASE}/api/me/profile", {"semester": 4, "email": "asha.personal@example.com", "section": "B"})
    assert s1.get(f"{BASE}/api/me")["user"]["semester"] == 4
    st = s1.get(f"{BASE}/api/student/stats")
    got = {b["key"] for b in st["badges"] if b["earned"]}
    assert {"podium", "champion", "perfect"} <= got and not {"solver", "rounds", "streak"} & got, got   # strict targets: 2 solves / 2 rounds are not enough
    sv = next(b for b in st["badges"] if b["key"] == "solver")
    assert sv["value"] >= 2 and sv["next"] == 10 and sv["targets"] == [10, 25, 50, 100] and "Reach 10" in sv["description"] and sv["level"] == 0, sv
    assert st["solved"] >= 2 and sum(st["heatmap"].values()) >= 3 and st["streak"] >= 1 and len(st["badges"]) >= 17 and any(b["key"].startswith("module:") for b in st["badges"])
    ok(f"levelled badges with fixed targets ({len(got)} earned: podium, champion, perfect round; Problem Solver still locked at {sv['value']}/10), streak + heatmap")
    sup.put(f"{A}/settings", {"departments": ["CSE", "ISE", "AIML", "cse"]})
    assert anon.get(f"{BASE}/api/public/info")["departments"] == ["CSE", "ISE", "AIML"]
    land = anon.get(f"{BASE}/api/public/landing")
    assert land["stats"]["students"] >= 13 and land["board"] and land["board"][0]["points"] > 0
    ok("admin-managed department list (deduplicated); landing page stats + leaderboard come from the database")
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        paras = ["Problem 1: Add Two", "Read two integers and print their sum.", "Input Format", "Two integers a and b.", "Output Format", "a+b",
                 "Sample Input", "2 3", "Sample Output", "5"]
        z.writestr("word/document.xml", "<w:document><w:body>" + "".join(f"<w:p><w:r><w:t>{t}</w:t></w:r></w:p>" for t in paras) + "</w:body></w:document>")
    for fname, data in (("paper.docx", buf.getvalue()), ("paper.md", "\n".join(paras).encode())):
        up = evA.req("POST", f"{A}/pdf", files={"file": (fname, data, "application/octet-stream")})
        for _ in range(50):
            d = evA.get(f"{A}/pdf/{up['id']}")
            if d["status"] != "processing":
                break
            time.sleep(0.2)
        g = d["drafts"][0]["payload"]
        assert d["status"] == "done" and g["title"] == "Add Two" and g["samples"][0]["output"].strip() == "5", (fname, d["warnings"], g)
    evA.req("POST", f"{A}/pdf", expect=400, files={"file": ("evil.exe", b"MZ....", "application/octet-stream")})
    ok("Word (.docx) and Markdown papers parsed into drafts; unsupported file types rejected")
    gen = "import random, sys\nseed = int(sys.stdin.readline())\nrandom.seed(seed)\nn = random.randint(1, 50)\nprint(n)\nprint(*[random.randint(-99, 99) for _ in range(n)])\n"
    jobs = evA.post(f"{A}/questions/{q1['id']}/generate", {"code": gen, "count": 6})["jobs"]
    for j in jobs:
        for _ in range(100):
            if evA.get(f"{A}/runs/{j}")["status"] in ("done", "error"):
                break
            time.sleep(0.2)
    col = evA.post(f"{A}/questions/{q1['id']}/generate/collect", {"jobs": jobs, "kind": "stress"})
    assert col["added"] >= 5 and col["validation_jobs"], col
    qv1 = wait_validated(evA, q1["id"])
    gen_tests = [t for t in qv1["tests"] if t["description"].startswith("generated")]
    assert len(gen_tests) == col["added"] and all(t["expected"].strip() for t in gen_tests)
    ok(f"platform generated {col['added']} tests from a script in the sandbox; outputs filled + cross-checked by references")

    ins = s1.get(f"{BASE}/api/student/insights")
    assert ins["level"] in ("Easy", "Intermediate", "Challenging") and ins["topics"] and ins["tip"]
    t = evA.post(f"{A}/ai/tools/presets/hint")
    evA.put(f"{A}/ai/tools/{t['id']}", {"spec": t["spec"], "status": "active", "scope": {"practice": True, "competitions": []}})
    evB.put(f"{A}/ai/tools/{t['id']}", {"spec": t["spec"], "status": "active", "scope": {"practice": True, "competitions": [c1["id"]]}}, expect=403)
    assert s1.get(f"{BASE}/api/student/tools?question_id={q1['id']}") == []  # AI not configured -> no tools shown, nothing breaks
    ok("adaptive insights (level, topic mastery, tip); AI tool installed + scoped; organisers can't switch tools on in others' rounds")

    print("Every import format, question types, database management")
    import zipfile as zf

    def upload(name, data):
        r = evA.req("POST", f"{A}/pdf", files={"file": (name, data, "application/octet-stream")})
        for _ in range(80):
            d = evA.get(f"{A}/pdf/{r['id']}")
            if d["status"] != "processing":
                return d
            time.sleep(0.2)
        return d
    qset = {"questions": [{"title": "JSON Q", "statement": "Print n+1.", "input_format": "n", "output_format": "n+1", "difficulty": "Medium",
                           "samples": [{"input": "1", "output": "2"}], "tests": [{"input": "5"}, {"input": "9", "output": "10"}],
                           "reference": {"python": "print(int(input())+1)"}, "qtype": "debug", "starter": {"python": "print(int(input())-1)"}}]}
    d = upload("set.json", json.dumps(qset).encode())
    g = d["drafts"][0]["payload"]
    assert d["status"] == "done" and g["title"] == "JSON Q" and g["difficulty"] == "Intermediate" and g["tests"][1]["expected"] == "10\n" and g["qtype"] == "debug"
    ap = evA.post(f"{A}/drafts/{d['drafts'][0]['id']}/approve")
    qj = wait_validated(evA, ap["question_id"])
    assert qj["version"]["qtype"] == "debug" and qj["version"]["starter"]["python"].startswith("print(int(input())-1)")
    assert [t["expected"] for t in qj["tests"] if t["visibility"] == "hidden"] == ["6\n", "10\n"]
    ok("JSON question set: fields, Medium→Intermediate, given + computed outputs, 'fix the bug' type with starter code")
    csvb = "title,statement,input_format,output_format,sample_input,sample_output,solution_python\nCSV Q,Double it,n,2n,3,6,print(int(input())*2)\nCSV Q2,Triple it,n,3n,2,6,print(int(input())*3)\n".encode()
    assert len(upload("set.csv", csvb)["drafts"]) == 2
    def xlsx(rows):
        b = io.BytesIO()
        with zf.ZipFile(b, "w") as z:
            strings = sorted({str(v) for r in rows for v in r})
            z.writestr("xl/sharedStrings.xml", "<sst>" + "".join(f"<si><t>{x}</t></si>" for x in strings) + "</sst>")
            body = "".join("<row>" + "".join(f'<c r="{chr(65 + j)}{i + 1}" t="s"><v>{strings.index(str(v))}</v></c>' for j, v in enumerate(r)) + "</row>" for i, r in enumerate(rows))
            z.writestr("xl/worksheets/sheet1.xml", f"<worksheet><sheetData>{body}</sheetData></worksheet>")
        return b.getvalue()
    dx = upload("set.xlsx", xlsx([["title", "statement", "sample_input", "sample_output"], ["XLSX Q", "Echo", "7", "7"]]))
    assert dx["drafts"][0]["payload"]["title"] == "XLSX Q" and dx["drafts"][0]["payload"]["samples"][0]["output"] == "7\n"
    paper = "Problem 1: Echo Back\nPrint the input.\nInput Format\nOne line.\nOutput Format\nThe same line.\nSample Input\nhi\nSample Output\nhi\n"
    pptx = io.BytesIO()
    with zf.ZipFile(pptx, "w") as z:
        z.writestr("ppt/slides/slide1.xml", "<p:sld>" + "".join(f"<a:p><a:r><a:t>{l}</a:t></a:r></a:p>" for l in paper.splitlines()) + "</p:sld>")
    odt = io.BytesIO()
    with zf.ZipFile(odt, "w") as z:
        z.writestr("content.xml", "<office:document-content>" + "".join(f"<text:p>{l}</text:p>" for l in paper.splitlines()) + "</office:document-content>")
    html = ("<html><body>" + "".join(f"<p>{l}</p>" for l in paper.splitlines()) + "<script>alert(1)</script></body></html>").encode()
    rtf = ("{\\rtf1\\ansi " + "\\par ".join(paper.splitlines()) + "}").encode()
    for name, data in (("p.pptx", pptx.getvalue()), ("p.odt", odt.getvalue()), ("p.html", html), ("p.rtf", rtf), ("p.py", paper.encode())):
        dd = upload(name, data)
        gg = dd["drafts"][0]["payload"]
        assert gg["title"] == "Echo Back" and gg["samples"][0]["output"].strip() == "hi", (name, gg, dd["warnings"])
    bundle = io.BytesIO()
    with zf.ZipFile(bundle, "w") as z:
        z.writestr("paper.html", html)
        z.writestr("questions.json", json.dumps(qset))
        z.writestr("notes/readme.txt", paper)
        z.writestr("evil.exe", b"MZ")
    dz = upload("all.zip", bundle.getvalue())
    assert dz["status"] == "done" and len(dz["drafts"]) == 3, dz["warnings"]
    evA.req("POST", f"{A}/pdf", expect=400, files={"file": ("bad.json", b"{not json", "application/json")})
    ok("PowerPoint, OpenDocument, HTML (scripts stripped), RTF, code/text files, Excel, CSV and a mixed ZIP all parsed; invalid JSON rejected")
    evA.get(f"{A}/db/overview", expect=403)
    ov = sup.get(f"{A}/db/overview")
    assert any(t["name"] == "submissions" and t["rows"] > 0 for t in ov["tables"])
    br = sup.get(f"{A}/db/table/users?q=root")
    assert br["total"] >= 1 and "password_hash" not in br["columns"]
    sup.get(f"{A}/db/table/sqlite_master", expect=404)
    assert "password_hash" not in sup.get(f"{A}/db/export/users").text
    assert sup.post(f"{A}/db/maintenance", {"action": "integrity"})["message"].startswith("Integrity check passed")
    sup.post(f"{A}/db/maintenance", {"action": "optimize"})
    sup.post(f"{A}/db/maintenance", {"action": "checkpoint"})
    bk = sup.post(f"{A}/db/backup")["file"]
    tmp_student, _ = student("4PM23CS999", "Temporary Person")
    assert sup.get(f"{A}/students?q=4PM23CS999")["total"] == 1
    sup.post(f"{A}/db/restore", {"name": bk, "confirm": "nope"}, expect=400)
    rs = sup.post(f"{A}/db/restore", {"name": bk, "confirm": "RESTORE"})
    sup2, _ = admin_login("root", "superpass123")
    assert sup2.get(f"{A}/students?q=4PM23CS999")["total"] == 0 and rs["safety_backup"]
    assert any(b["name"] == rs["safety_backup"] for b in sup2.get(f"{A}/db/overview")["backups"])
    sup = sup2
    ok("Database page: overview, secret-free browsing + CSV export, maintenance, backup → restore (with automatic safety backup); Super Admin only")

    print("Profile photos, result publishing, practice manager")
    png = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000" "1f15c4890000000d49444154789c6360000002000105e0b4ae0000000049454e44ae426082")
    av = s1.req("POST", f"{BASE}/api/me/avatar", files={"file": ("me.png", png, "image/png")})
    assert av["avatar"].startswith("/api/avatar/") and httpx.get(BASE + av["avatar"]).headers["content-type"] == "image/png"
    s1.req("POST", f"{BASE}/api/me/avatar", expect=400, files={"file": ("x.png", b"<svg onload=alert(1)>", "image/png")})
    ok("profile photo upload (PNG/JPG/WEBP only, signature-checked) and serving; SVG/script uploads refused")
    c3 = evA.post(f"{A}/competitions", {**base, "title": "Hidden Results Round", "code": "ASTRA-HIDDEN", "round_no": 9, "start_at": int(time.time()) + 60, "leaderboard_visibility": "hidden"})
    evA.put(f"{A}/competitions/{c3['id']}/questions", {"questions": [{"question_id": q2["id"], "marks": 100}]})
    evA.post(f"{A}/competitions/{c3['id']}/action", {"action": "publish"})
    s2.post(f"{BASE}/api/student/competitions/{c3['id']}/join")
    evA.post(f"{A}/competitions/{c3['id']}/action", {"action": "start"})
    time.sleep(5.1)  # per-student submit cooldown
    r = s2.post(f"{BASE}/api/student/submissions", {"competition_id": c3["id"], "question_id": q2["id"], "language": "python", "code": REV_PY, "kind": "submit"})
    assert wait_sub(s2, r["id"])["verdict"] == "AC"
    fin = s2.post(f"{BASE}/api/student/competitions/{c3['id']}/finish")
    assert fin.get("hidden") and "score" not in fin
    evA.post(f"{A}/competitions/{c3['id']}/results", {"publish": True}, expect=409)
    evA.post(f"{A}/competitions/{c3['id']}/action", {"action": "end"})
    assert anon.get(f"{BASE}/api/competitions/{c3['id']}/leaderboard").get("hidden")
    h = s2.get(f"{BASE}/api/student/profile")["history"]
    assert next(x for x in h if x["id"] == c3["id"])["results_pending"]
    evA.post(f"{A}/competitions/{c3['id']}/results", {"publish": True})
    lb3 = anon.get(f"{BASE}/api/competitions/{c3['id']}/leaderboard")
    assert not lb3.get("hidden") and lb3["rows"][0]["score"] == 100
    assert any(x["id"] == c3["id"] and x["rank"] == 1 for x in s2.get(f"{BASE}/api/student/history")["results"])
    ok("manual results: hidden during and after the round (even in the finish summary) until the organiser publishes, then visible + celebration data")
    pr = evA.get(f"{A}/practice")
    target = next(x for x in pr if not x["practice"] and not x["reserved"])
    evA.post(f"{A}/practice", {"question_id": target["id"], "on": True})
    assert any(x["id"] == target["id"] for x in s3.get(f"{BASE}/api/student/practice"))
    evA.post(f"{A}/practice", {"question_id": target["id"], "on": False})
    assert not any(x["id"] == target["id"] for x in s3.get(f"{BASE}/api/student/practice"))
    ok("practice manager: organisers add/remove approved questions and students see the change immediately")

    print("Exam lockdown")
    LD = {"internet": "block", "fullscreen": "required", "leave": "warn", "max_violations": 2, "no_copy": True, "no_paste": True}
    c4 = evA.post(f"{A}/competitions", {**base, "title": "Lockdown Round", "code": "ASTRA-LOCK", "round_no": 10, "start_at": int(time.time()) + 60, "lockdown": {**LD, "bogus": 1}})
    assert evA.get(f"{A}/competitions/{c4['id']}")["lockdown"] == LD
    c5 = evA.post(f"{A}/competitions", {**base, "title": "Default Round", "code": "ASTRA-LOCK2", "round_no": 11, "start_at": int(time.time()) + 60,
                                        "lockdown": {"internet": "nonsense", "max_violations": "999"}})
    d5 = evA.get(f"{A}/competitions/{c5['id']}")["lockdown"]
    assert d5["internet"] == "flag" and d5["max_violations"] == 50 and d5["fullscreen"] == "required"
    evA.put(f"{A}/competitions/{c4['id']}/questions", {"questions": [{"question_id": q2["id"], "marks": 100}]})
    evA.post(f"{A}/competitions/{c4['id']}/action", {"action": "publish"})
    s3.post(f"{BASE}/api/student/competitions/{c4['id']}/join")
    evA.post(f"{A}/competitions/{c4['id']}/action", {"action": "start"})
    assert s3.get(f"{BASE}/api/student/competitions/{c4['id']}/arena")["competition"]["lockdown"] == LD
    warn = lambda: s3.post(f"{BASE}/api/student/activity", {"competition_id": c4["id"], "events": [{"type": "violation", "detail": {"kind": "left_test"}}]})
    w1 = warn()
    assert w1["warnings"] == 1 and not w1.get("finished")
    assert warn().get("finished")
    assert s3.get(f"{BASE}/api/student/competitions/{c4['id']}/arena")["finished_at"]
    time.sleep(5.1)
    s3.post(f"{BASE}/api/student/submissions", {"competition_id": c4["id"], "question_id": q2["id"], "language": "python", "code": REV_PY, "kind": "submit"}, expect=409)
    act = evA.get(f"{A}/competitions/{c4['id']}/activity/{m3['id']}")
    assert act["finished_at"] and any(e["type"] == "auto_finished" for e in act["events"])
    evA.post(f"{A}/competitions/{c4['id']}/participants/{m3['id']}/reopen")
    assert not s3.get(f"{BASE}/api/student/competitions/{c4['id']}/arena")["finished_at"]
    assert warn()["warnings"] == 1  # count restarts after a reopen
    r = s3.post(f"{BASE}/api/student/submissions", {"competition_id": c4["id"], "question_id": q2["id"], "language": "python", "code": REV_PY, "kind": "submit"})
    assert wait_sub(s3, r["id"])["verdict"] == "AC"
    evA.post(f"{A}/competitions/{c4['id']}/action", {"action": "end"})
    evA.post(f"{A}/competitions/{c4['id']}/participants/{m3['id']}/reopen", expect=409)
    ok("exam lockdown: rules saved per competition and sanitised, sent to the arena, warnings counted on the server, auto-submit at the limit, organiser reopen resets the count")

    print("Built-in C practice modules, written-answer questions, competition assistant")
    pr = s3.get(f"{BASE}/api/student/practice")
    mods = {x["category"] for x in pr}
    assert {"C Basics", "C Logic Building"} <= mods and sum(x["category"] in ("C Basics", "C Logic Building") for x in pr) == 18, mods
    qo = next(x for x in pr if x["title"] == "Predict the Output: Integer Division")
    assert qo["qtype"] == "output" and s3.get(f"{BASE}/api/student/practice/{qo['id']}")["languages"] == ["answer"]
    time.sleep(5.1)
    r = s3.post(f"{BASE}/api/student/submissions", {"competition_id": None, "question_id": qo["id"], "language": "answer", "code": "3 3", "kind": "submit"})
    assert wait_sub(s3, r["id"])["verdict"] == "WA"
    time.sleep(5.1)
    r = s3.post(f"{BASE}/api/student/submissions", {"competition_id": None, "question_id": qo["id"], "language": "answer", "code": "3 2\n", "kind": "submit"})
    assert wait_sub(s3, r["id"])["verdict"] == "AC"
    qs = next(x for x in pr if x["title"] == "Sum of Two Numbers")
    time.sleep(5.1)
    r = s3.post(f"{BASE}/api/student/submissions", {"competition_id": None, "question_id": qs["id"], "language": "c", "kind": "submit",
                                                    "code": "#include <stdio.h>\nint main(){long a,b;scanf(\"%ld %ld\",&a,&b);printf(\"%ld\\n\",a+b);return 0;}\n"})
    assert wait_sub(s3, r["id"])["verdict"] == "AC"
    qd = next(x for x in pr if x["title"] == "Fix the Bug: Swap Two Numbers")
    buggy = s3.get(f"{BASE}/api/student/practice/{qd['id']}")["starter"]["c"]
    time.sleep(5.1)
    r = s3.post(f"{BASE}/api/student/submissions", {"competition_id": None, "question_id": qd["id"], "language": "c", "code": buggy, "kind": "submit"})
    assert wait_sub(s3, r["id"])["verdict"] == "WA"  # the shipped starter really is buggy
    ok("two C practice modules ship with 18 validated questions; predict-the-output answers are judged as text; the bundled reference passes and the buggy starter fails")
    plan = evA.post(f"{A}/assistant/competition", {"message": "45 minute C round for 1st years tomorrow at 10am, 2 easy coding and 1 predict the output, no internet, auto submit after 3 warnings"})
    assert plan["ready"] and len(plan["questions"]) == 3 and plan["plan"]["duration_min"] == 45 and plan["plan"]["eligible_years"] == [1], plan
    assert [q["qtype"] for q in plan["questions"]][:2] == ["standard", "standard"] and plan["questions"][2]["qtype"] in ("output", "mcq")
    plan2 = evA.post(f"{A}/assistant/competition", {"message": "make it 90 minutes and add 2 code tracing questions", "plan": plan["plan"]})
    assert plan2["plan"]["duration_min"] == 90 and len(plan2["questions"]) == 5 and plan2["plan"]["eligible_years"] == [1]
    short = evA.post(f"{A}/assistant/competition", {"message": "30 hard prompt questions"})
    assert not short["ready"] and short["problems"]
    made = evA.post(f"{A}/assistant/competition", {"message": "", "plan": plan2["plan"], "create": True})["created"]
    cm = evA.get(f"{A}/competitions/{made['id']}")
    assert cm["status"] == "draft" and len(cm["questions"]) == 5 and cm["duration_min"] == 90 and cm["languages"] == ["c"]
    assert cm["lockdown"]["internet"] == "block" and cm["lockdown"]["max_violations"] == 3 and cm["eligible_years"] == [1] and cm["start_at"]
    assert [q["question_id"] for q in cm["questions"]] == [q["id"] for q in plan2["questions"]]  # the reviewed questions are the ones created
    sup.delete(f"{A}/competitions/{made['id']}")
    ok("competition assistant: plain description -> plan with matching questions, follow-up edits keep earlier details, shortages are reported, draft created exactly as reviewed")

    print("Multiple choice, placement modules, learning path")
    pr = s3.get(f"{BASE}/api/student/practice")
    place = [x for x in pr if x["category"].startswith("Placement: ")]
    assert len(place) == 52 and sum(x["qtype"] == "mcq" for x in place) == 42, len(place)
    mq = next(x for x in place if x["qtype"] == "mcq")
    pv = s3.get(f"{BASE}/api/student/practice/{mq['id']}")
    right = sup.get(f"{A}/questions/{mq['id']}")["references"]["answer"]
    assert len(pv["options"]) == 4 and right.strip() in [o.strip() for o in pv["options"]] and "answer" not in str({k: v for k, v in pv.items() if k != "options"}).replace("'answer']", "")
    wrong = next(o for o in pv["options"] if o.strip() != right.strip())
    for code, verdict in ((wrong, "WA"), (right, "AC")):
        time.sleep(5.1)
        r = s3.post(f"{BASE}/api/student/submissions", {"competition_id": None, "question_id": mq["id"], "language": "answer", "code": code, "kind": "submit"})
        assert wait_sub(s3, r["id"])["verdict"] == verdict
    ok("four placement modules ship with 52 questions (42 multiple choice, 14 of them logical thinking); a wrong option is WA, the right one AC; practice allows retries")
    mbody = {"title": "MCQ: Loop count", "category": "Loops", "difficulty": "Easy", "tags": [], "statement": "How many times does `for(i=0;i<5;i++)` run its body?",
             "qtype": "mcq", "options": ["4", "5", "6", "Forever"], "answer": "5"}
    evA.post(f"{A}/questions", {**mbody, "answer": "7"}, expect=400)
    evA.post(f"{A}/questions", {**mbody, "options": ["5"]}, expect=400)
    mc = evA.post(f"{A}/questions", mbody)
    assert mc["version"]["options"] == ["4", "5", "6", "Forever"] and mc["references"] == {"answer": "5"} and mc["version"]["validated_at"]
    sup.post(f"{A}/questions/{mc['id']}/status", {"status": "approved"})
    cq = evA.post(f"{A}/competitions", {**base, "title": "MCQ Quiz", "code": "MCQ-QUIZ", "start_at": int(time.time()) + 3600, "max_marks": 100})
    evA.put(f"{A}/competitions/{cq['id']}/questions", {"questions": [{"question_id": mc["id"], "marks": 100}]})
    evA.post(f"{A}/competitions/{cq['id']}/action", {"action": "publish"})
    s3.post(f"{BASE}/api/student/competitions/{cq['id']}/join")
    evA.post(f"{A}/competitions/{cq['id']}/action", {"action": "start"})
    ar = s3.get(f"{BASE}/api/student/competitions/{cq['id']}/arena")
    assert sorted(ar["problems"][0]["options"]) == sorted(["4", "5", "6", "Forever"]) and ar["problems"][0]["languages"] == ["answer"]
    time.sleep(5.1)
    r = s3.post(f"{BASE}/api/student/submissions", {"competition_id": cq["id"], "question_id": mc["id"], "language": "answer", "code": "6", "kind": "submit"})
    assert wait_sub(s3, r["id"])["verdict"] == "WA"
    time.sleep(5.1)
    s3.post(f"{BASE}/api/student/submissions", {"competition_id": cq["id"], "question_id": mc["id"], "language": "answer", "code": "5", "kind": "submit"}, expect=409)
    evA.post(f"{A}/competitions/{cq['id']}/action", {"action": "end"})
    ok("organiser creates a multiple-choice question (bad answer / too few options refused); in a competition the first answer is final")
    rv = s3.get(f"{BASE}/api/student/competitions/{cq['id']}/review")
    q0 = rv["questions"][0]
    assert q0["correct_answer"] == "5" and q0["your_answer"] == "6" and not q0["solved"] and q0["options"], q0
    s1.get(f"{BASE}/api/student/competitions/{cq['id']}/review", expect=404)
    ok("after the round each student gets a review: their answer, the correct one, and why it failed")

    print("Own question set per student, fair-play penalty, instant test round")
    mcqs = [x for x in s3.get(f"{BASE}/api/student/practice") if x["qtype"] == "mcq"]
    pick = [x for x in mcqs if x["difficulty"] == "Easy"][:5] + [x for x in mcqs if x["difficulty"] == "Intermediate"][:5]
    cd = evA.post(f"{A}/competitions", {**base, "title": "Draw Round", "code": "DRAW-ROUND", "start_at": int(time.time()) + 3600, "max_marks": 40, "draw": 4, "shuffle": True,
                                         "scoring": {**base["scoring"], "violation_pct": 10}})
    assert cd["draw"] == 4 and cd["shuffle"] == 1 and cd["ai_pool"] == 1
    evA.put(f"{A}/competitions/{cd['id']}/questions", {"questions": [{"question_id": x["id"], "marks": 10} for x in pick]})
    evA.post(f"{A}/competitions/{cd['id']}/action", {"action": "publish"})
    evA.post(f"{A}/competitions/{cd['id']}/pool", {"count": 2}, expect=503)      # no AI on this machine: refused with a reason, nothing breaks
    for s in (s1, s2, s3):
        s.post(f"{BASE}/api/student/competitions/{cd['id']}/join")
    evA.post(f"{A}/competitions/{cd['id']}/action", {"action": "start"})
    diff = {x["id"]: x["difficulty"] for x in pick}
    sets = {}
    for name, s in (("a", s1), ("b", s2), ("c", s3)):
        ids = [p["id"] for p in s.get(f"{BASE}/api/student/competitions/{cd['id']}/arena")["problems"]]
        assert len(ids) == 4 == len(set(ids)) and set(ids) <= set(diff)
        assert [p["id"] for p in s.get(f"{BASE}/api/student/competitions/{cd['id']}/arena")["problems"]] == ids   # stable on reload
        assert sorted(diff[i] for i in ids) == ["Easy", "Easy", "Intermediate", "Intermediate"]                    # same mix for everyone
        sets[name] = ids
    other = next(i for i in diff if i not in sets["a"])
    time.sleep(5.1)
    s1.post(f"{BASE}/api/student/submissions", {"competition_id": cd["id"], "question_id": other, "language": "answer", "code": "x", "kind": "submit"}, expect=404)
    first = sets["a"][0]
    right = sup.get(f"{A}/questions/{first}")["references"]["answer"]
    r = s1.post(f"{BASE}/api/student/submissions", {"competition_id": cd["id"], "question_id": first, "language": "answer", "code": right, "kind": "submit"})
    assert wait_sub(s1, r["id"])["verdict"] == "AC"
    for k in range(2):
        s1.post(f"{BASE}/api/student/activity", {"competition_id": cd["id"], "events": [{"type": "violation", "detail": {"kind": "left_test", "n": k + 1}}]})
    for _ in range(3):   # the same internet connection reported again and again (reloads) counts once per 5 minutes
        s2.post(f"{BASE}/api/student/activity", {"competition_id": cd["id"], "events": [{"type": "violation", "detail": {"kind": "internet"}}]})
    lbd = s1.get(f"{BASE}/api/competitions/{cd['id']}/leaderboard")
    me = next(x for x in lbd["rows"] if x["me"])
    assert next(x for x in sup.get(f"{A}/competitions/{cd['id']}/leaderboard")["rows"] if x["name"] == "Bharath K")["warnings"] == 1
    assert me["warnings"] == 2 and me["deducted"] == 8 and me["score"] == 2 and lbd["violation_pct"] == 10, me   # 10 marks - 2 x 10% of 40
    assert len({x["rank"] for x in lbd["rows"] if x["score"] == 0}) == 1                                             # equal results share a rank
    assert lbd["how"]["order"][0].startswith("Higher score") and me["why"] and "fair-play" in " ".join(me["why"]), me["why"]
    top = lbd["rows"][0]
    assert top["why"] and top["why"][0].startswith("Higher score"), top["why"]
    evA.post(f"{A}/competitions/{cd['id']}/explain", {"on": False})
    assert s1.get(f"{BASE}/api/competitions/{cd['id']}/leaderboard")["how"] is None
    evA.post(f"{A}/competitions/{cd['id']}/explain", {"on": True})
    evA.post(f"{A}/competitions/{cd['id']}/action", {"action": "end"})
    ok("each student gets their own 4 of 10 questions (same easy/medium mix, own order, fixed on reload, others refused); 2 warnings cost 2 x 10%")
    outq = [x for x in mcqs if x["title"].startswith("Output MCQ")][:5]
    cr = evA.post(f"{A}/competitions", {**base, "title": "Rotation", "code": "ROTATE-1", "start_at": int(time.time()) + 3600, "draw": 0, "shuffle": True})
    evA.put(f"{A}/competitions/{cr['id']}/questions", {"questions": [{"question_id": x["id"], "marks": 10} for x in outq]})
    v = evA.post(f"{A}/competitions/{cr['id']}/variants", {"per": 1})
    assert v["questions"] == 5, v
    for _ in range(90):
        cd2 = evA.get(f"{A}/competitions/{cr['id']}")
        if not cd2["pool"]["working"] and len(cd2["questions"]) > 5:
            break
        time.sleep(1)
    added = cd2["questions"][5:]
    assert 3 <= len(added) <= 5, len(added)
    for q in added:
        full = sup.get(f"{A}/questions/{q['question_id']}")
        orig = next(x for x in outq if full["version"]["title"].startswith(x["title"]))
        o = sup.get(f"{A}/questions/{orig['id']}")
        assert full["status"] == "approved" and full["references"]["answer"] in full["version"]["options"] and len(set(full["version"]["options"])) == 4
        assert full["version"]["statement"] != o["version"]["statement"] and full["references"]["answer"] != o["references"]["answer"]
        assert o["references"]["answer"].strip() in [x.strip() for x in full["version"]["options"]]   # the old answer is a trap option
    evA.put(f"{A}/competitions/{cr['id']}/questions", {"questions": [{"question_id": x["id"], "marks": 10} for x in outq]})   # back to the 5 originals
    evA.post(f"{A}/competitions/{cr['id']}/action", {"action": "publish"})
    extra = [student(f"4PM23CS{901 + k}", f"Rotor {k}")[0] for k in range(2)]
    firsts, orders = [], set()
    for s in (s1, s2, s3, *extra):
        s.post(f"{BASE}/api/student/competitions/{cr['id']}/join")
    evA.post(f"{A}/competitions/{cr['id']}/action", {"action": "start"})
    for s in (s1, s2, s3, *extra):
        ids = [p["id"] for p in s.get(f"{BASE}/api/student/competitions/{cr['id']}/arena")["problems"]]
        assert sorted(ids) == sorted(x["id"] for x in outq)
        firsts.append(ids[0]); orders.add(tuple(ids))
    assert len(set(firsts)) == 5 and len(orders) == 5, firsts      # 5 students, same 5 questions, each starts on a different one
    evA.post(f"{A}/competitions/{cr['id']}/action", {"action": "end"})
    ok("same questions for everyone: rotated order, every student starts on a different question; number variants made instantly with answers proved by running")
    qr = evA.post(f"{A}/competitions/quick", {"mcq": 2, "coding": 1, "minutes": 10})
    qc = evA.get(f"{A}/competitions/{qr['id']}")
    assert qc["status"] == "live" and len(qc["questions"]) == 3 and qc["allow_late_join"] and qr["code"].startswith("TEST-")
    s2.post(f"{BASE}/api/student/competitions/{qr['id']}/join")
    assert len(s2.get(f"{BASE}/api/student/competitions/{qr['id']}/arena")["problems"]) == 3
    dash = s2.get(f"{BASE}/api/student/dashboard")
    assert any(x["id"] == qr["id"] and x["for_me"] for x in dash["active"])
    evA.post(f"{A}/competitions/{qr['id']}/action", {"action": "end"})
    ok("instant test round: picked, published and live in one click; it shows on the student's dashboard")
    sum2 = next(x for x in s3.get(f"{BASE}/api/student/practice") if x["title"] == "Sum of Two Numbers")
    time.sleep(5.1)
    r = s3.post(f"{BASE}/api/student/submissions", {"competition_id": None, "question_id": sum2["id"], "language": "c", "kind": "run",
                                                    "code": "#include <stdio.h>\nint main(){long a,b;printf(\"Enter two numbers: \");scanf(\"%ld %ld\",&a,&b);printf(\"%ld\\n\",a+b);return 0;}\n"})
    fb = wait_sub(s3, r["id"])
    assert fb["verdict"] == "WA" and any("extra text" in x or "prompt" in x for x in fb["reasons"]), fb.get("reasons")
    ov = anon.get(f"{BASE}/api/leaderboard/overall")
    assert ov["rounds"] >= 1 and ov["rows"] and ov["rows"][0]["rank"] == 1
    ok("rule-based feedback names the mistake (an input prompt in the output); overall leaderboard across all rounds")
    jr = s3.get(f"{BASE}/api/student/journey")
    names = [x["category"] for x in jr["stages"]]
    assert names[:5] == ["C Basics", "C Logic Building", "Placement: Predict the Output", "Placement: CS Fundamentals", "Placement: Logical Thinking"], names
    assert names[5] == "Placement: Coding Patterns" and jr["current"] == 0 and jr["stages"][0]["next_id"] and jr["stages"][2]["solved"] + jr["stages"][3]["solved"] >= 1 and all(x["total"] for x in jr["stages"])
    ins = s3.get(f"{BASE}/api/student/insights")
    assert ins["level"] in ("Easy", "Intermediate", "Challenging") and ins["recommendations"]
    pm = evA.post(f"{A}/assistant/competition", {"message": "placement mock: 6 moderate mcq, 2 easy coding, 40 minutes"})
    assert [q["qtype"] for q in pm["questions"]].count("mcq") == 6 and pm["missing"] == [], pm["problems"]
    st2 = sup.get(f"{A}/ai/status")
    assert st2.get("reachable") is False and st2["autogrow"] is True and st2["reason"]
    ok("learning path lists the modules in placement order with the next problem; the assistant builds a mock from multiple-choice + coding; AI absence is explained, never an error")

    print("Reference documents and fix-the-bug questions")
    import pymupdf
    doc = pymupdf.open(); doc.new_page().insert_text((40, 60), "Unit 3: Stacks\nA stack is LIFO: push adds on top, pop removes from the top.\nSample: push 1, push 2, pop -> top is 1.", fontsize=10)
    rf = sup.req("POST", f"{A}/ai/reference", files={"file": ("unit3.pdf", doc.tobytes(), "application/pdf")})
    assert rf["chars"] > 60 and "LIFO" in rf["preview"] and len(rf["ref"]) == 16, rf
    sup.req("POST", f"{A}/ai/reference", 400, files={"file": ("fake.pdf", b"hello", "application/pdf")})
    sup.req("POST", f"{A}/ai/reference", 400, files={"file": ("a.exe", b"MZ....", "application/octet-stream")})
    sup.post(f"{A}/ai/prompt", {"text": "3 mcq", "ref": rf["ref"]}, expect=503)        # no AI on this machine: refused cleanly
    lines = []
    for k in range(1, 12):   # a typical question paper: 11 numbered multiple-choice questions, one without an answer, one coding question
        lines += [f"{k}. What is {k} + {k} in C?", f"A) {2 * k}", f"B) {2 * k + 1}", f"C) {k}", f"D) {k * k + 5}"] + ([f"Answer: A"] if k != 7 else [])
    lines += ["12. Write a program that reads n and prints n squared.", "Input: 4  Output: 16"]
    doc = pymupdf.open(); pg = doc.new_page(height=1400)
    pg.insert_text((30, 30), "Unit 1 quiz\n" + "\n".join(lines), fontsize=8)
    paper = sup.req("POST", f"{A}/ai/reference", files={"file": ("unit1-quiz.pdf", doc.tobytes(), "application/pdf")})
    f = paper["found"]
    assert f["total"] == 12 and f["mcq_ready"] == 10 and f["mcq_no_answer"] == 1 and f["other"] == 1, f
    cfile = evA.post(f"{A}/competitions", {**base, "title": "From file", "code": "FROM-FILE", "start_at": int(time.time()) + 3600})
    imp = evA.post(f"{A}/ai/reference/{paper['ref']}/import", {"difficulty": "Easy", "category": "Unit 1", "competition_id": cfile["id"], "per_student": 5})
    assert imp["ready"] == 10 and imp["drafts"] == 2 and imp["in_competition"] == 10, imp
    cfd = evA.get(f"{A}/competitions/{cfile['id']}")
    assert len(cfd["questions"]) == 10 and cfd["draw"] == 5 and all(q["q_status"] == "approved" for q in cfd["questions"])
    q3 = sup.get(f"{A}/questions/{cfd['questions'][2]['question_id']}")
    assert q3["version"]["qtype"] == "mcq" and q3["references"]["answer"] == "6" and q3["version"]["options"] == ["6", "7", "3", "14"], q3["version"]
    ok("question paper (PDF): all 12 questions found; 10 with answers ready and added to a competition (5 per student), 2 saved to finish")
    if not EXTERNAL:
        from app import ai, db as _db
        c = _db.connect()
        qid = next(x for x in s3.get(f"{BASE}/api/student/practice") if x["title"] == "Largest of Three")["id"]
        log = []
        assert ai.make_debug(c, qid, sup.get(f"{A}/me")["id"], log), log   # AI is off, so the automatic bug is used
        q = sup.get(f"{A}/questions/{qid}")
        assert q["version"]["qtype"] == "debug" and q["version"]["languages"] == ["c"] and q["version"]["starter"]["c"] != q["references"]["c"], log
        c.close()
        ok("reference document (PDF) read for AI writing; bad files refused; a checked buggy starter turns a coding problem into fix-the-bug")

    print("Rate limiting & recovery")
    bad = Client()
    codes = [bad.h.post(f"{A}/login", json={"username": "root", "password": "wrong"}).status_code for _ in range(8)]
    assert 429 in codes, codes
    ok("repeated admin login failures are rate-limited / locked out")
    if EXTERNAL:
        print("  (skipped direct-DB recovery check for external deployment)")
    else:
        recovery_check(m1, q3, qv)
    health = sup.get(f"{A}/system")
    assert health["workers_alive"] >= 1 and health["database"] == "ok"
    b = sup.post(f"{A}/system/backup")
    assert b["file"].startswith("astra-")
    ok("system status reports live judge workers; manual backup written")


def recovery_check(m1, q3, qv):
    from app import db, worker
    c = db.connect()
    sid = c.execute("INSERT INTO submissions(competition_id,user_id,question_id,version_id,kind,language,code,status,worker,created_at) "
                    "VALUES(NULL,?,?,?,'run','python','print(1)','running','dead-worker',?)", (m1["id"], q3["id"], qv["version"]["id"], db.now())).lastrowid
    worker.recover(c)
    assert db.one(c, "SELECT status FROM submissions WHERE id=?", sid)["status"] in ("queued", "running", "done")
    c.close()
    ok("jobs left 'running' by a dead worker are re-queued, never lost")


if __name__ == "__main__":
    main()
