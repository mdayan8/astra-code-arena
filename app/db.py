"""SQLite storage. WAL mode handles a campus contest (hundreds of users) fine.
ponytail: SQLite single-writer; move to PostgreSQL if you ever run thousands of concurrent writers."""
import json
import sqlite3
import time
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  email TEXT,
  name TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('admin','student')),
  role TEXT CHECK(role IN ('super_admin','event_admin','reviewer')),
  can_approve INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','suspended')),
  failed_logins INTEGER NOT NULL DEFAULT 0,
  locked_until INTEGER NOT NULL DEFAULT 0,
  must_change_pw INTEGER NOT NULL DEFAULT 0,
  last_login_at INTEGER, last_login_ip TEXT,
  created_by INTEGER REFERENCES users(id),
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS students(
  user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  participant_id TEXT NOT NULL UNIQUE,
  usn TEXT UNIQUE COLLATE NOCASE,
  department TEXT NOT NULL DEFAULT '',
  year INTEGER NOT NULL DEFAULT 1,
  section TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sessions(
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  csrf TEXT NOT NULL,
  ip TEXT, ua TEXT,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS semesters(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  academic_year TEXT NOT NULL,
  term TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','frozen','finalized','archived')),
  created_by INTEGER REFERENCES users(id),
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS competitions(
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE COLLATE NOCASE,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  instructions TEXT NOT NULL DEFAULT '',
  academic_year TEXT NOT NULL DEFAULT '',
  semester_label TEXT NOT NULL DEFAULT '',
  round_no INTEGER NOT NULL DEFAULT 1,
  category TEXT NOT NULL DEFAULT 'General',
  eligible_years TEXT NOT NULL DEFAULT '[]',
  eligible_departments TEXT NOT NULL DEFAULT '[]',
  registration_mode TEXT NOT NULL DEFAULT 'open' CHECK(registration_mode IN ('open','invite_only')),
  reg_start INTEGER, reg_end INTEGER,
  start_at INTEGER,
  duration_min INTEGER NOT NULL DEFAULT 90,
  extra_min INTEGER NOT NULL DEFAULT 0,
  paused_at INTEGER,
  paused_total_sec INTEGER NOT NULL DEFAULT 0,
  languages TEXT NOT NULL DEFAULT '["c","python"]',
  max_marks INTEGER NOT NULL DEFAULT 100,
  scoring TEXT NOT NULL DEFAULT '{}',
  leaderboard_visibility TEXT NOT NULL DEFAULT 'live' CHECK(leaderboard_visibility IN ('live','after_end','hidden')),
  show_test_counts INTEGER NOT NULL DEFAULT 1,
  allow_late_join INTEGER NOT NULL DEFAULT 0,
  auto_start INTEGER NOT NULL DEFAULT 0,
  paste_policy TEXT NOT NULL DEFAULT 'log' CHECK(paste_policy IN ('allow','log','block_external')),
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','published','live','paused','ended','finalized','archived','cancelled')),
  created_by INTEGER REFERENCES users(id),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  started_at INTEGER, ended_at INTEGER, finalized_at INTEGER
);
CREATE TABLE IF NOT EXISTS competition_admins(
  competition_id INTEGER NOT NULL REFERENCES competitions(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  PRIMARY KEY(competition_id, user_id)
);
CREATE TABLE IF NOT EXISTS competition_participants(
  competition_id INTEGER NOT NULL REFERENCES competitions(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  joined_at INTEGER NOT NULL,
  source TEXT NOT NULL DEFAULT 'self',
  status TEXT NOT NULL DEFAULT 'registered' CHECK(status IN ('registered','disqualified')),
  PRIMARY KEY(competition_id, user_id)
);
CREATE TABLE IF NOT EXISTS semester_competitions(
  semester_id INTEGER NOT NULL REFERENCES semesters(id) ON DELETE CASCADE,
  competition_id INTEGER NOT NULL UNIQUE REFERENCES competitions(id) ON DELETE CASCADE,
  points INTEGER NOT NULL DEFAULT 100,
  PRIMARY KEY(semester_id, competition_id)
);
CREATE TABLE IF NOT EXISTS questions(
  id INTEGER PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  category TEXT NOT NULL DEFAULT 'Problem Solving',
  difficulty TEXT NOT NULL DEFAULT 'Easy' CHECK(difficulty IN ('Easy','Intermediate','Challenging')),
  tags TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','approved','archived')),
  latest_version_id INTEGER,
  source TEXT NOT NULL DEFAULT 'manual',
  practice INTEGER NOT NULL DEFAULT 0,
  created_by INTEGER REFERENCES users(id),
  approved_by INTEGER REFERENCES users(id),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS question_versions(
  id INTEGER PRIMARY KEY,
  question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  version_no INTEGER NOT NULL,
  title TEXT NOT NULL,
  statement TEXT NOT NULL DEFAULT '',
  input_format TEXT NOT NULL DEFAULT '',
  output_format TEXT NOT NULL DEFAULT '',
  constraints TEXT NOT NULL DEFAULT '',
  languages TEXT NOT NULL DEFAULT '["c","cpp","python","java","javascript"]',
  time_limit_ms INTEGER NOT NULL DEFAULT 2000,
  memory_mb INTEGER NOT NULL DEFAULT 256,
  max_marks INTEGER NOT NULL DEFAULT 100,
  validated_at INTEGER,
  validation TEXT,
  created_by INTEGER REFERENCES users(id),
  created_at INTEGER NOT NULL,
  UNIQUE(question_id, version_no)
);
CREATE TABLE IF NOT EXISTS reference_solutions(
  version_id INTEGER NOT NULL REFERENCES question_versions(id) ON DELETE CASCADE,
  language TEXT NOT NULL,
  code TEXT NOT NULL,
  PRIMARY KEY(version_id, language)
);
CREATE TABLE IF NOT EXISTS test_cases(
  id INTEGER PRIMARY KEY,
  version_id INTEGER NOT NULL REFERENCES question_versions(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL DEFAULT 0,
  input TEXT NOT NULL DEFAULT '',
  expected TEXT NOT NULL DEFAULT '',
  visibility TEXT NOT NULL DEFAULT 'hidden' CHECK(visibility IN ('sample','hidden')),
  kind TEXT NOT NULL DEFAULT 'normal' CHECK(kind IN ('normal','boundary','edge','stress')),
  weight INTEGER NOT NULL DEFAULT 1,
  explanation TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  validated INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS competition_questions(
  competition_id INTEGER NOT NULL REFERENCES competitions(id) ON DELETE CASCADE,
  question_id INTEGER NOT NULL REFERENCES questions(id),
  version_id INTEGER NOT NULL REFERENCES question_versions(id),
  ordinal INTEGER NOT NULL DEFAULT 0,
  marks INTEGER NOT NULL DEFAULT 100,
  PRIMARY KEY(competition_id, question_id)
);
-- submissions table is also the judging queue (status column).
CREATE TABLE IF NOT EXISTS submissions(
  id INTEGER PRIMARY KEY,
  competition_id INTEGER REFERENCES competitions(id),
  user_id INTEGER NOT NULL REFERENCES users(id),
  question_id INTEGER NOT NULL REFERENCES questions(id),
  version_id INTEGER NOT NULL REFERENCES question_versions(id),
  kind TEXT NOT NULL CHECK(kind IN ('submit','run','validate','practice')),
  language TEXT NOT NULL,
  code TEXT NOT NULL,
  custom_input TEXT,
  fill_expected INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','done','error')),
  verdict TEXT,
  score REAL NOT NULL DEFAULT 0,
  passed INTEGER NOT NULL DEFAULT 0,
  total INTEGER NOT NULL DEFAULT 0,
  time_ms INTEGER, memory_kb INTEGER,
  compile_output TEXT,
  run_output TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  worker TEXT,
  ip TEXT,
  created_at INTEGER NOT NULL,
  started_at INTEGER, judged_at INTEGER
);
CREATE TABLE IF NOT EXISTS judging_results(
  submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
  test_case_id INTEGER,
  ordinal INTEGER NOT NULL,
  visibility TEXT NOT NULL,
  verdict TEXT NOT NULL,
  weight INTEGER NOT NULL DEFAULT 1,
  time_ms INTEGER, memory_kb INTEGER,
  output TEXT,
  stderr TEXT,
  PRIMARY KEY(submission_id, ordinal)
);
CREATE TABLE IF NOT EXISTS leaderboard_entries(
  competition_id INTEGER NOT NULL REFERENCES competitions(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id),
  rank INTEGER NOT NULL,
  score REAL NOT NULL,
  solved INTEGER NOT NULL,
  penalty INTEGER NOT NULL,
  detail TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY(competition_id, user_id)
);
CREATE TABLE IF NOT EXISTS announcements(
  id INTEGER PRIMARY KEY,
  competition_id INTEGER REFERENCES competitions(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  created_by INTEGER REFERENCES users(id),
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS uploaded_documents(
  id INTEGER PRIMARY KEY,
  filename TEXT NOT NULL,
  stored_name TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  size INTEGER NOT NULL,
  pages INTEGER,
  status TEXT NOT NULL DEFAULT 'processing' CHECK(status IN ('processing','done','failed')),
  extracted_text TEXT,
  warnings TEXT NOT NULL DEFAULT '[]',
  uploaded_by INTEGER REFERENCES users(id),
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS generated_question_drafts(
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL CHECK(source IN ('pdf','ai')),
  document_id INTEGER REFERENCES uploaded_documents(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('generating','pending','approved','rejected','failed')),
  payload TEXT NOT NULL DEFAULT '{}',
  warnings TEXT NOT NULL DEFAULT '[]',
  question_id INTEGER REFERENCES questions(id),
  created_by INTEGER REFERENCES users(id),
  reviewed_by INTEGER REFERENCES users(id),
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS activity_events(
  id INTEGER PRIMARY KEY,
  competition_id INTEGER NOT NULL REFERENCES competitions(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id),
  question_id INTEGER,
  type TEXT NOT NULL,
  detail TEXT,
  ip TEXT,
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_logs(
  id INTEGER PRIMARY KEY,
  user_id INTEGER REFERENCES users(id),
  action TEXT NOT NULL,
  entity TEXT, entity_id INTEGER,
  competition_id INTEGER,
  detail TEXT,
  ip TEXT,
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS platform_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS error_log(id INTEGER PRIMARY KEY, source TEXT, message TEXT, created_at INTEGER NOT NULL);

CREATE INDEX IF NOT EXISTS ix_sub_queue ON submissions(status, id);
CREATE INDEX IF NOT EXISTS ix_sub_comp_user ON submissions(competition_id, user_id, question_id);
CREATE INDEX IF NOT EXISTS ix_sub_comp_kind ON submissions(competition_id, kind, status);
CREATE INDEX IF NOT EXISTS ix_cp_user ON competition_participants(user_id);
CREATE INDEX IF NOT EXISTS ix_tc_version ON test_cases(version_id, ordinal);
CREATE INDEX IF NOT EXISTS ix_qv_q ON question_versions(question_id);
CREATE INDEX IF NOT EXISTS ix_act ON activity_events(competition_id, user_id, created_at);
CREATE INDEX IF NOT EXISTS ix_audit ON audit_logs(created_at);
CREATE INDEX IF NOT EXISTS ix_comp_status ON competitions(status, start_at);
CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id);
"""

DEFAULT_SETTINGS = {
    "registration_open": "true",      # students may self-register
    "name_display": "full",           # full | initials | participant_id  (public leaderboards)
    "highlights_enabled": "true",
    "site_banner": "",
    "submit_cooldown_sec": "5",
    "practice_autogrow": "true",      # when a student runs out of practice at their level, the AI writes a new sandbox-checked problem
    "departments": '["CSE", "ISE", "AIML", "CSE (DS)", "ECE", "EEE", "ME", "CV"]',  # edited by admins
}


def now() -> int:
    return int(time.time())


def connect() -> sqlite3.Connection:
    c = sqlite3.connect(config.DB_PATH, timeout=30, isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA busy_timeout=30000")
    c.execute("PRAGMA synchronous=FULL")  # every commit reaches the disk: survives power cuts
    return c


# Schema changes after v1 go here, in order. Each entry runs once (tracked with PRAGMA user_version).
# Example: "ALTER TABLE competitions ADD COLUMN team_size INTEGER NOT NULL DEFAULT 1"
MIGRATIONS: list[str] = [
    "ALTER TABLE students ADD COLUMN dob TEXT",            # 1: date of birth (YYYY-MM-DD); default password = DDMMYYYY
    "ALTER TABLE students ADD COLUMN semester INTEGER",    # 2: current semester, updated by the student
    # 3: AI Tools Lab (admin-built tools, switched on per competition / practice) + usage log
    """CREATE TABLE IF NOT EXISTS ai_tools(id INTEGER PRIMARY KEY, name TEXT NOT NULL, spec TEXT NOT NULL DEFAULT '{}',
         status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','active','off')), scope TEXT NOT NULL DEFAULT '{"competitions":[],"practice":false}',
         created_by INTEGER REFERENCES users(id), created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
       CREATE TABLE IF NOT EXISTS ai_tool_uses(id INTEGER PRIMARY KEY, tool_id INTEGER NOT NULL REFERENCES ai_tools(id) ON DELETE CASCADE,
         user_id INTEGER NOT NULL REFERENCES users(id), competition_id INTEGER, question_id INTEGER, input TEXT, output TEXT, ms INTEGER, created_at INTEGER NOT NULL);
       CREATE INDEX IF NOT EXISTS ix_tool_uses ON ai_tool_uses(tool_id, user_id, question_id);""",
    # 4: "Finish test" (final submission): after this the participant can no longer submit in that competition
    """CREATE TABLE IF NOT EXISTS participant_finish(competition_id INTEGER NOT NULL REFERENCES competitions(id) ON DELETE CASCADE,
         user_id INTEGER NOT NULL REFERENCES users(id), finished_at INTEGER NOT NULL, PRIMARY KEY(competition_id, user_id));""",
    # 5: question types (standard / debug = fix the bug / complete = fill in the code) with per-language starter code
    """ALTER TABLE question_versions ADD COLUMN qtype TEXT NOT NULL DEFAULT 'standard';
       ALTER TABLE question_versions ADD COLUMN starter TEXT NOT NULL DEFAULT '{}';""",
    # 6: profile pictures + manual result publishing
    """ALTER TABLE users ADD COLUMN avatar TEXT;
       ALTER TABLE competitions ADD COLUMN results_published_at INTEGER;""",
    # 7: exam lockdown rules per competition (internet, full screen, leaving the test, auto-submit, copy/paste)
    """ALTER TABLE competitions ADD COLUMN lockdown TEXT NOT NULL DEFAULT '{}';""",
    # 8: multiple-choice options (JSON list of texts); the correct one is the expected text of the hidden test
    "ALTER TABLE question_versions ADD COLUMN options TEXT NOT NULL DEFAULT '[]'",
    # 9: each student gets their own draw of the questions (pool), shuffled order; the draw is stored so it never changes
    """ALTER TABLE competitions ADD COLUMN draw INTEGER NOT NULL DEFAULT 0;
       ALTER TABLE competitions ADD COLUMN shuffle INTEGER NOT NULL DEFAULT 1;
       ALTER TABLE competitions ADD COLUMN ai_pool INTEGER NOT NULL DEFAULT 0;
       ALTER TABLE competitions ADD COLUMN ai_prompt TEXT NOT NULL DEFAULT '';
       ALTER TABLE competitions ADD COLUMN results_at INTEGER;
       CREATE INDEX IF NOT EXISTS ix_act_type ON activity_events(competition_id, type, user_id, created_at);
       ALTER TABLE competition_participants ADD COLUMN questions TEXT;""",
    # 10: each student's own clock: when they opened the test
    """ALTER TABLE competition_participants ADD COLUMN started_at INTEGER;
       -- a round already running: students who already opened it keep the time they really started (first heartbeat after the start)
       UPDATE competition_participants SET started_at = (
         SELECT MAX(co.start_at, COALESCE((SELECT MIN(a.created_at) FROM activity_events a WHERE a.competition_id=competition_participants.competition_id
                AND a.user_id=competition_participants.user_id AND a.type!='joined' AND a.created_at>=co.start_at), co.start_at))
         FROM competitions co WHERE co.id=competition_participants.competition_id)
       WHERE questions IS NOT NULL AND competition_id IN (SELECT id FROM competitions WHERE status IN ('live','paused') AND start_at IS NOT NULL);""",
]


def _healthy(path) -> bool:
    try:
        x = sqlite3.connect(path)
        ok = x.execute("PRAGMA quick_check").fetchone()[0] == "ok" and x.execute("SELECT 1 FROM sqlite_master WHERE name='competitions'").fetchone()
        x.close()
        return bool(ok)
    except sqlite3.DatabaseError:
        return False


def recover_if_damaged():
    """At start-up: if the database file is damaged (power cut mid-write on a bad disk, etc.), keep the damaged copy aside
    and restore the newest healthy backup. Records what happened so admins are told."""
    if not config.DB_PATH.exists() or _healthy(config.DB_PATH):
        return None
    import shutil
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for suffix in ("", "-wal", "-shm"):
        f = config.DB_PATH.with_name(config.DB_PATH.name + suffix)
        if f.exists():
            f.rename(f.with_name(f"{f.name}.damaged-{stamp}"))
    dirs = [config.BACKUP_DIR] + ([__import__("pathlib").Path(config.BACKUP_MIRROR)] if config.BACKUP_MIRROR else [])
    for b in sorted((f for d in dirs if d.exists() for f in d.glob("astra-*.db")), key=lambda f: f.stat().st_mtime, reverse=True):
        if _healthy(b):
            shutil.copy2(b, config.DB_PATH)
            c = connect()
            set_setting(c, "db:restored", json.dumps({"at": now(), "from": b.name, "damaged": f"{config.DB_PATH.name}.damaged-{stamp}"}))
            c.close()
            return b.name
    return "none"


def init():
    recover_if_damaged()
    c = connect()
    c.executescript(SCHEMA)
    ver = c.execute("PRAGMA user_version").fetchone()[0]
    for i, sql in enumerate(MIGRATIONS, start=1):
        if i > ver:
            c.executescript(sql)
            c.execute(f"PRAGMA user_version={i}")
    for k, v in DEFAULT_SETTINGS.items():
        c.execute("INSERT OR IGNORE INTO platform_settings(key,value) VALUES(?,?)", (k, v))
    c.close()
    try:
        config.DB_PATH.chmod(0o600)  # sandbox users must never read the DB
    except OSError:
        pass


@contextmanager
def tx(c: sqlite3.Connection):
    """BEGIN IMMEDIATE ... COMMIT; rolls back on any exception."""
    c.execute("BEGIN IMMEDIATE")
    try:
        yield c
        c.execute("COMMIT")
    except BaseException:
        c.execute("ROLLBACK")
        raise


def one(c, sql, *a):
    r = c.execute(sql, a).fetchone()
    return dict(r) if r else None


def all_(c, sql, *a):
    return [dict(r) for r in c.execute(sql, a).fetchall()]


def setting(c, key, default=""):
    r = c.execute("SELECT value FROM platform_settings WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def set_setting(c, key, value):
    c.execute("INSERT INTO platform_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def jl(s, default=None):
    try:
        return json.loads(s) if s else (default if default is not None else [])
    except (ValueError, TypeError):
        return default if default is not None else []


def log_error(c, source, message):
    try:
        c.execute("INSERT INTO error_log(source,message,created_at) VALUES(?,?,?)", (source, str(message)[:2000], now()))
        c.execute("DELETE FROM error_log WHERE id < (SELECT MAX(id) - 500 FROM error_log)")
    except sqlite3.Error:
        pass
