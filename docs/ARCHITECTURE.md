# Architecture

```
 Lab PCs / Wi-Fi ──HTTP──▶  web container (FastAPI, :APP_PORT)  ──┐
                            • student portal  /                     │  SQLite (WAL)
                            • private admin   /<ADMIN_PATH>/        ├─ /data/astra.db  (0600, root)
                            • REST APIs       /api/...              │  /data/uploads, /data/backups
                                                                    │
                            judge container (no network, read-only) ┘
                            • N worker processes claim queued submissions
                            • compile + run each job as its own uid in /tmp (tmpfs)
```

## Code map

| File | Responsibility |
|---|---|
| `app/main.py` | App, static files, private admin route, security headers, error handling |
| `app/security.py` | scrypt hashing, sessions, CSRF, rate limits, lockout, role permissions, audit helper |
| `app/api_student.py` | Public + student APIs (never returns hidden tests, references or other students' data) |
| `app/api_admin.py` | Control Centre APIs: competitions, questions/versions/tests, PDF, AI, participants, monitor, integrity, reports, users, settings |
| `app/contest.py` | Competition state machine + timing (pause/extend), leaderboard and semester scoring |
| `app/judge.py` | Language table, sandboxed compile/run, verdict classification |
| `app/worker.py` | Queue consumer, retries, crash recovery, auto start/end, daily backups |
| `app/pdfimport.py` | PyMuPDF text + tesseract OCR, question splitting, section parsing |
| `app/ai.py` | Optional OpenAI-compatible client, prompts, draft normalisation |
| `app/db.py` | Schema, migrations, helpers |
| `static/` | Student SPA, shared CSS/JS, bundled Monaco editor, logos |
| `admin/` | Admin SPA (served only under the private route) |

## Data model (main tables)

`users` (admins + students, role, lockout) · `students` (participant ID, USN, dept, year) · `sessions` ·
`competitions` (all settings, timing, status) · `competition_admins` · `competition_participants` ·
`questions` → `question_versions` → `test_cases`, `reference_solutions` · `competition_questions` (pins a version) ·
`submissions` (also the judging queue) → `judging_results` (per test) · `leaderboard_entries` (frozen final results) ·
`semesters`, `semester_competitions` (points) · `announcements` · `uploaded_documents`, `generated_question_drafts` ·
`activity_events` (fair-play telemetry, code snapshots) · `audit_logs` · `platform_settings` · `error_log`.

Every competition-scoped row carries `competition_id`; multi-row changes run inside `BEGIN IMMEDIATE` transactions.
Roles/permissions are a fixed map in `security.py` (3 roles) instead of separate tables. The spec's `judging_jobs` table
is the `status` column of `submissions`, so a submission and its job can never get out of sync.

## Competition states

`draft → published → live ⇄ paused → ended → finalized → archived`, plus `cancelled`.
Students see derived labels: Upcoming, Registration Open/Closed, Live, Paused, Completed, Archived.
End time = start + duration + extensions + total paused time; the worker auto-starts and auto-ends rounds every 5 s.

## Security model

- Admin route is obscurity only; every admin API checks session + role (+ competition organiser membership).
- Separate cookies for students (`astra_s`) and admins (`astra_a`), HTTP-only, SameSite=Strict; CSRF header on all writes.
- All HTML output is escaped client-side before insertion; SQL is parameterised everywhere; CSV exports neutralise formula injection.
- Uploads: PDF magic-byte + size check, stored under random names with 0600, only text is read.
- Judge: separate container with `network_mode: none`, read-only root, 1 GB tmpfs, dropped capabilities, `no-new-privileges`;
  each job runs as uid `20000 + slot` with CPU, address-space, file-size, open-file and process limits; the whole uid is
  killed after each job (handles fork bombs and escaped daemons); a clean environment with no secrets; the DB is unreadable to it.
  Verified by `tests/sandbox_check.py`.

## Migrations

`db.SCHEMA` is version 1 (idempotent `CREATE TABLE IF NOT EXISTS`). Later changes are appended to `db.MIGRATIONS`
and applied once each on start, tracked with `PRAGMA user_version`.

## Known limits (deliberate)

- SQLite single writer: fine for a campus (hundreds of users); switch to PostgreSQL for thousands of concurrent writers.
- Team participation is not implemented (individual only).
- Memory exhaustion in C/C++ usually surfaces as Runtime Error (malloc returns NULL), not Memory Limit Exceeded; Python/Java/JS report MLE.
- Badges and certificates (spec phase 4) are not built yet; semester leaderboards and history are.
