"""All runtime configuration comes from environment variables (see .env.example)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv():
    f = ROOT / ".env"
    if not f.exists():
        return
    for line in f.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            v = v.split(" #", 1)[0].strip().strip('"').strip("'")  # allow trailing comments
            os.environ.setdefault(k.strip(), v)


_load_dotenv()

env = os.environ.get
APP_HOST = env("APP_HOST", "0.0.0.0")
APP_PORT = int(env("APP_PORT", "8000"))
APP_ENV = env("APP_ENV", "development")
APP_TIMEZONE = env("APP_TIMEZONE", "Asia/Kolkata")
DATA_DIR = Path(env("DATA_DIR", str(ROOT / "data")))
DB_PATH = Path(env("DB_PATH", str(DATA_DIR / "astra.db")))
UPLOAD_DIR = DATA_DIR / "uploads"
BACKUP_DIR = Path(env("BACKUP_DIR", str(DATA_DIR / "backups")))
ADMIN_PATH = env("ADMIN_PATH", "")  # empty -> generated once and stored in the DB
COOKIE_SECURE = env("COOKIE_SECURE", "false").lower() == "true"  # true only when served over HTTPS
SESSION_HOURS = int(env("SESSION_HOURS", "12"))
MAX_UPLOAD_MB = int(env("MAX_UPLOAD_MB", "20"))
JUDGE_WORKERS = int(env("JUDGE_WORKERS", "2"))
JUDGE_TMP = Path(env("JUDGE_TMP", "/tmp/astra-judge"))
JUDGE_UID_BASE = int(env("JUDGE_UID_BASE", "20000"))  # sandbox uid = base + worker slot (only when worker runs as root)
JUDGE_NETWORK_ISOLATED = env("JUDGE_NETWORK_ISOLATED", "false").lower() == "true"  # set by docker-compose (network_mode: none)
BACKUP_KEEP = int(env("BACKUP_KEEP", "30"))
BACKUP_EVERY_H = float(env("BACKUP_EVERY_H", "6"))           # automatic backup interval (hourly while a round is live)
BACKUP_MIRROR = env("BACKUP_MIRROR", "")                     # optional second folder (another disk / USB) that gets a copy of every backup
# Optional AI: any OpenAI-compatible endpoint. Ollama local: http://localhost:11434/v1
AI_BASE_URL = env("AI_BASE_URL", "")
AI_MODEL = env("AI_MODEL", "")
AI_API_KEY = env("AI_API_KEY", "")
SEED_PRACTICE = env("SEED_PRACTICE", "1") != "0"  # ship the built-in C practice modules
AI_TIMEOUT = int(env("AI_TIMEOUT", "180"))
AI_CONCURRENCY = int(env("AI_CONCURRENCY", "2"))  # simultaneous AI calls (keeps RAM/CPU low on small servers)
# Comma-separated CIDRs allowed to open the platform, e.g. "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16". Empty = allow all.
ALLOWED_NETWORKS = [x.strip() for x in env("ALLOWED_NETWORKS", "").split(",") if x.strip()]

for d in (DATA_DIR, UPLOAD_DIR, BACKUP_DIR):
    d.mkdir(parents=True, exist_ok=True)
