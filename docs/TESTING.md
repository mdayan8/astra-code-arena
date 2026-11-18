# Testing

Three automated suites plus a manual network checklist. Run the automated ones after every change and on the real server before an event.

## 1. Acceptance test: `tests/test_e2e.py`

Boots a real web server and judge on a throwaway database and walks the spec's acceptance workflow (45 checks):

- public pages contain no admin route; `/admin` is 404; private route is `noindex`; unauthenticated and student sessions are refused by admin APIs; CSRF enforced
- Super Admin creates Event Admins; temp password must be changed; Event Admin cannot grant Super Admin or read the audit log; one Event Admin cannot open another's competition
- questions created, approval blocked until validation; C reference compiled and Python reference executed; empty outputs filled and cross-checked; a C/Python disagreement is detected
- Competition 01: publish blocked without questions; students register/join; duplicate join refused; problems hidden before start; arena never exposes hidden tests or reference code
- judging: Run on samples and custom input; AC (C), AC (Python), WA, CE, RE, TLE (C infinite loop), TLE (Python), MLE, malicious file read; students cannot read each other's submissions
- late join refused; organiser assigns 10 students; 10 concurrent submissions all judged
- paste/tab-switch/snapshot tracking and risk score; live leaderboard order; repeated AC gives no extra points; pause blocks submissions; extend; end; finalize (frozen); 5 CSV exports; audit log
- Competition 02 by another admin with a different question and Python-only language set runs independently
- editing a used question creates v2 while Competition 01 keeps v1 and its results; duplicate has zero participants/submissions
- semester leaderboard combines both rounds by configured points and finalises
- PDF upload: 2 questions detected with formats and samples; non-PDF rejected; draft edited, saved, auto-validated
- AI disabled → clean 503, manual authoring unaffected; login lockout; dead-worker jobs re-queued; health + backup
- students: department must be from the admin list; sign-in with USN + DOB; forgot-password (wrong name refused, correct USN+name+DOB resets to DOB); semester/profile update; badges, streak and heatmap from real results; landing stats + leaderboard from the DB
- imports: Word (.docx) and Markdown papers parsed; unsupported types rejected; script-generated tests run in the sandbox and get reference outputs

```bash
pip install httpx
python3 tests/test_e2e.py                      # on a laptop (reduced sandbox)
docker run --rm --init astra-arena:latest sh -c "pip install -q httpx; python tests/test_e2e.py"   # Linux, judge as root (production sandbox)
```

Last results: **45/45 passed** on macOS; the earlier 39-check version also passed inside the Linux container as root and 38/38 against the real two-container `docker compose` stack.

## 2. Sandbox isolation: `tests/sandbox_check.py`

Runs hostile programs inside the hardened judge container:

```bash
docker compose run --rm judge python tests/sandbox_check.py
```

| Probe | Result |
|---|---|
| read `/data/astra.db` | PermissionError |
| read `/proc/1/environ` | PermissionError |
| open a socket to the internet | Network is unreachable |
| write into `/app` | Read-only file system |
| fork bomb | contained (TLE), all processes killed afterwards |
| background child that outlives the program | killed |
| infinite output | Output Limit Exceeded |
| 1 GB malloc in C | refused (Runtime Error) |
| `#include <bits/stdc++.h>` | compiles (GCC) |

## 3. Load test: `tests/load_test.py`

Simulates N students in one live round: each polls the arena every 15 s, sends activity every 10 s, a third poll the
leaderboard, and everyone submits two C/Python solutions at random times and polls the verdict.

```bash
python3 tests/load_test.py 300 120 4      # students, seconds, judge workers
```

It prints request rate, p50/p95/max latency per endpoint, judged submissions, submit→verdict time and `RESULT: PASS/FAIL`
(fails on any HTTP error or judge error). Run it on the actual server to size `JUDGE_WORKERS`.

## 4. AI harness: `tests/ai_harness_check.py`

Needs a model (e.g. Ollama). Generates questions through the harness and reports how many came out validated.

```bash
AI_MODEL=qwen2.5-coder:1.5b python3 tests/ai_harness_check.py
```

## 5. Network tests (manual, on campus)

Follow the checklist in [DEPLOYMENT.md §4](DEPLOYMENT.md#4-college-lan--wi-fi-checklist-do-this-a-few-days-before-the-event):
wired lab PC, Wi-Fi device, the real IP and port, admin route only with credentials, and a run with the internet disconnected.
These depend on the college network and **cannot be verified until the platform is on the college server**.
