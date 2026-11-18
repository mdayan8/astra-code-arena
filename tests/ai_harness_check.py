"""AI harness check (needs a model, e.g. Ollama running locally):
    AI_MODEL=qwen2.5-coder:1.5b python3 tests/ai_harness_check.py "Arrays: second largest" "Strings: count vowels"
Boots a throwaway server + judge, asks the AI assistant for questions, and reports whether the self-repair harness
produced a question whose C and Python references agree on every test."""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="astra-ai-"))
PORT = 8788
ENV = {**os.environ, "DATA_DIR": str(TMP), "APP_PORT": str(PORT), "APP_HOST": "127.0.0.1", "ADMIN_PATH": "x-ai", "JUDGE_WORKERS": "2",
       "JUDGE_TMP": f"/tmp/astra-judge-ai-{PORT}", "AI_BASE_URL": os.environ.get("AI_BASE_URL", "http://127.0.0.1:11434/v1"),
       "AI_MODEL": os.environ.get("AI_MODEL", "qwen2.5-coder:1.5b"), "AI_TIMEOUT": "300"}
os.environ.update(ENV)
sys.path.insert(0, str(ROOT))
import httpx  # noqa: E402

from app import db  # noqa: E402
from app.security import hash_password  # noqa: E402

B = f"http://127.0.0.1:{PORT}/api/admin"
topics = sys.argv[1:] or ["Arrays: second largest distinct value", "Strings: count vowels in a word", "Basic Mathematics: sum of digits"]
db.init()
c = db.connect()
c.execute("INSERT INTO users(username,name,password_hash,kind,role,can_approve,created_at) VALUES('root','Root',?,'admin','super_admin',1,?)", (hash_password("superpass123"), db.now()))
c.close()
procs = [subprocess.Popen([sys.executable, "-m", m], cwd=ROOT, env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for m in ("app.main", "app.worker")]
try:
    time.sleep(2)
    h = httpx.Client(timeout=60)
    csrf = h.post(f"{B}/login", json={"username": "root", "password": "superpass123"}).json()["csrf"]
    print("model:", ENV["AI_MODEL"], "|", h.get(f"{B}/ai/status").json())
    ok = 0
    for t in topics:
        t0 = time.time()
        gid = h.post(f"{B}/ai/generate", json={"topic": t, "difficulty": "Easy", "samples": 2}, headers={"X-CSRF-Token": csrf}).json()["draft_id"]
        while True:
            g = next(x for x in h.get(f"{B}/drafts?source=ai").json() if x["id"] == gid)
            if g["status"] != "generating":
                break
            time.sleep(3)
        q = h.get(f"{B}/questions/{g['question_id']}").json() if g["question_id"] else None
        good = bool(q and q["version"]["validated_at"])
        ok += good
        print(f"\n[{'PASS' if good else 'FAIL'}] {t}  ({time.time() - t0:.0f}s)  -> {q['version']['title'] if q else '-'}")
        for w in g["warnings"]:
            print("   ·", w[:220])
    print(f"\n{ok}/{len(topics)} questions came out validated (C and Python agree on every test).")
    H = {"X-CSRF-Token": csrf}
    # --- document -> AI questions ---
    paper = b"Q1. Write a program to read N numbers and print how many of them are even.\nExample: input 5 numbers 1 2 3 4 6 -> output 3\n"
    did = h.post(f"{B}/pdf", files={"file": ("messy.txt", paper, "text/plain")}, headers=H).json()["id"]
    time.sleep(2)
    t0 = time.time()
    h.post(f"{B}/pdf/{did}/ai", headers=H)
    while any(g["status"] == "generating" for g in h.get(f"{B}/pdf/{did}").json()["drafts"]):
        time.sleep(3)
    g = h.get(f"{B}/pdf/{did}").json()["drafts"][0]
    q = h.get(f"{B}/questions/{g['question_id']}").json() if g["question_id"] else None
    print(f"\n[{'PASS' if q and q['version']['validated_at'] else 'FAIL'}] messy document -> question ({time.time() - t0:.0f}s): {q['version']['title'] if q else ''}")
    for w in g["warnings"]:
        print("   ·", w[:260])
    # --- student tool use ---
    if q and q["version"]["validated_at"]:
        h.put(f"{B}/questions/{q['id']}", json={**{k: q["version"][k] for k in ("title", "statement", "input_format", "output_format", "constraints", "languages", "time_limit_ms", "memory_mb", "max_marks")},
                                          "category": q["category"], "difficulty": q["difficulty"], "tags": q["tags"], "practice": True, "references": q["references"]}, headers=H)
        h.post(f"{B}/questions/{q['id']}/status", json={"status": "approved"}, headers=H)
        tool = h.post(f"{B}/ai/tools/presets/hint", headers=H).json()
        h.put(f"{B}/ai/tools/{tool['id']}", json={"spec": tool["spec"], "status": "active", "scope": {"practice": True, "competitions": []}}, headers=H)
        st = httpx.Client(base_url=f"http://127.0.0.1:{PORT}", timeout=200)
        rr = st.post("/api/auth/register", json={"name": "Test Student", "usn": "4PM23CS777", "department": "CSE", "year": 1, "dob": "2006-01-02"})
        me = rr.json()
        if "csrf" not in me:
            print("register failed:", rr.status_code, rr.text[:300])
        t0 = time.time()
        r = st.post(f"/api/student/tools/{tool['id']}/run", json={"question_id": q["id"], "code": "int main(){return 0;}", "language": "c", "inputs": {"stuck": "I don't know how to start"}},
                    headers={"X-CSRF-Token": me["csrf"]})
        print(f"[{'PASS' if r.status_code == 200 else 'FAIL'}] student used the Hint tool in practice ({time.time() - t0:.0f}s):", (r.json().get("output") or r.text)[:300].replace("\n", " "))
    # --- one-box: describe -> created + deployed ---
    t0 = time.time()
    r = h.post(f"{B}/ai/tools/quick", json={"request": "After a wrong answer, tell the student which edge case they probably missed, in two sentences, no code", "scope": {"practice": True}}, headers=H)
    t = r.json() if r.status_code == 200 else {}
    print(f"[{'PASS' if t.get('status') == 'active' else 'FAIL'}] one-box Create & deploy ({time.time() - t0:.0f}s): {t.get('name') or r.text[:200]} -> {t.get('status')} {t.get('scope')}")
finally:
    for p in procs:
        p.terminate()
