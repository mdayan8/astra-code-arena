"""Database management for Super Admins: overview, read-only table browser, CSV export, maintenance,
backups (create / download / restore) and data clean-up. Everything is audited."""
import csv
import io
import re
import shutil
import sqlite3
import time

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field

from . import config, db
from .api_admin import admin
from .api_student import conn
from .security import audit, client_ip, need

router = APIRouter(prefix="/api/admin/db")
HIDDEN_COLS = {"password_hash", "token_hash", "csrf"}  # never shown in the browser


def tables(c):
    return [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def safe_table(c, name):
    if name not in tables(c):
        raise HTTPException(404, "Unknown table.")
    return name


@router.get("/overview")
def overview(u=Depends(admin), c=Depends(conn)):
    need(u, "database")
    wal = config.DB_PATH.with_name(config.DB_PATH.name + "-wal")
    du = shutil.disk_usage(config.DATA_DIR)
    backups = sorted(config.BACKUP_DIR.glob("astra-*.db"), key=lambda f: f.stat().st_mtime, reverse=True)
    snap = c.execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(detail)),0) FROM activity_events WHERE type='snapshot'").fetchone()
    return {"size_mb": round(config.DB_PATH.stat().st_size / 1e6, 2), "wal_mb": round(wal.stat().st_size / 1e6, 2) if wal.exists() else 0,
            "path": str(config.DB_PATH), "schema_version": c.execute("PRAGMA user_version").fetchone()[0], "sqlite": sqlite3.sqlite_version,
            "disk_free_gb": round(du.free / 1e9, 1), "backup_keep": config.BACKUP_KEEP, "backup_dir": str(config.BACKUP_DIR),
            "integrity": db.jl(db.setting(c, "db:integrity", ""), {}) or None, "optimized_at": int(db.setting(c, "db:optimized_at", "0") or 0) or None,
            "tables": [{"name": t, "rows": c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]} for t in tables(c)],
            "snapshots": {"rows": snap[0], "mb": round(snap[1] / 1e6, 2)},
            "backups": [{"name": f.name, "size_mb": round(f.stat().st_size / 1e6, 2), "at": int(f.stat().st_mtime)} for f in backups]}


@router.get("/table/{name}")
def browse(name: str, q: str = "", limit: int = 50, offset: int = 0, u=Depends(admin), c=Depends(conn)):
    need(u, "database")
    t = safe_table(c, name)
    cols = [r[1] for r in c.execute(f'PRAGMA table_info("{t}")')]
    show = [x for x in cols if x not in HIDDEN_COLS]
    where, args = "", []
    if q:
        where = " WHERE " + " OR ".join(f'CAST("{x}" AS TEXT) LIKE ?' for x in show)
        args = [f"%{q}%"] * len(show)
    limit, offset = max(1, min(int(limit), 200)), max(0, int(offset))
    total = c.execute(f'SELECT COUNT(*) FROM "{t}"{where}', args).fetchone()[0]
    order = "rowid DESC" if "WITHOUT ROWID" not in (c.execute("SELECT sql FROM sqlite_master WHERE name=?", (t,)).fetchone()[0] or "") else "1"
    rows = c.execute(f'SELECT {",".join(chr(34) + x + chr(34) for x in show)} FROM "{t}"{where} ORDER BY {order} LIMIT ? OFFSET ?', args + [limit, offset]).fetchall()
    return {"table": t, "columns": show, "total": total,
            "rows": [[(str(v)[:300] + "…" if isinstance(v, str) and len(v) > 300 else v) for v in r] for r in rows]}


@router.get("/export/{name}")
def export_table(name: str, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "database")
    t = safe_table(c, name)
    cols = [r[1] for r in c.execute(f'PRAGMA table_info("{t}")') if r[1] not in HIDDEN_COLS]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in c.execute(f'SELECT {",".join(chr(34) + x + chr(34) for x in cols)} FROM "{t}"'):
        w.writerow([("'" + x) if isinstance(x, str) and x[:1] in "=+-@" else x for x in r])
    audit(c, u, "db.export_table", detail={"table": t}, ip=client_ip(req))
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{t}.csv"'})


class MaintIn(BaseModel):
    action: str = Field(pattern="^(integrity|checkpoint|optimize|vacuum|purge_snapshots|purge_sessions)$")
    days: int = Field(default=90, ge=1, le=3650)


@router.post("/maintenance")
def maintenance(body: MaintIn, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "database")
    from .worker import integrity_check
    t0, msg = time.time(), ""
    if body.action == "integrity":
        ok = integrity_check(c)
        msg = "Integrity check passed." if ok else "Integrity problems found. See the overview."
    elif body.action == "checkpoint":
        r = c.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        msg = f"Write log folded into the database ({r[2]} pages)."
    elif body.action == "optimize":
        c.execute("PRAGMA optimize")
        c.execute("ANALYZE")
        db.set_setting(c, "db:optimized_at", db.now())
        msg = "Query statistics refreshed."
    elif body.action == "vacuum":
        live = c.execute("SELECT COUNT(*) FROM competitions WHERE status IN ('live','paused')").fetchone()[0]
        if live:
            raise HTTPException(409, "Compacting locks the database for a moment. Do it when no competition is live.")
        before = config.DB_PATH.stat().st_size
        c.execute("VACUUM")
        msg = f"Compacted: {before / 1e6:.2f} MB → {config.DB_PATH.stat().st_size / 1e6:.2f} MB."
    elif body.action == "purge_snapshots":
        cut = db.now() - body.days * 86400
        n = c.execute("DELETE FROM activity_events WHERE type IN ('snapshot','heartbeat','keystrokes') AND created_at < ? "
                      "AND competition_id IN (SELECT id FROM competitions WHERE status IN ('finalized','archived','cancelled'))", (cut,)).rowcount
        msg = f"Removed {n} old code snapshots / heartbeats from finished competitions (older than {body.days} days). Pastes, tab switches and results are kept."
    elif body.action == "purge_sessions":
        n = c.execute("DELETE FROM sessions WHERE expires_at < ?", (db.now(),)).rowcount
        msg = f"Removed {n} expired sign-in sessions."
    audit(c, u, f"db.{body.action}", detail={"days": body.days, "result": msg}, ip=client_ip(req))
    return {"message": msg, "ms": int((time.time() - t0) * 1000)}


@router.post("/backup")
def make_backup(req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "database")
    from .worker import backup
    name = backup(c, "manual")
    audit(c, u, "db.backup", detail={"file": name}, ip=client_ip(req))
    return {"file": name}


class RestoreIn(BaseModel):
    name: str
    confirm: str


def _restore_from(c, src_path, u, req, label):
    from .worker import backup
    chk = sqlite3.connect(src_path)
    try:
        r = chk.execute("PRAGMA quick_check").fetchone()
        if not r or r[0] != "ok":
            raise HTTPException(400, "That backup file is damaged and was not restored.")
        if not chk.execute("SELECT 1 FROM sqlite_master WHERE name='competitions'").fetchone():
            raise HTTPException(400, "That file is not a Code Arena database.")
    except sqlite3.DatabaseError:
        raise HTTPException(400, "That file is not a valid database.")
    finally:
        chk.close()
    safety = backup(c, "before-restore")  # always keep the current state
    src = sqlite3.connect(src_path)
    try:
        src.backup(c)  # online copy into the live database (other requests wait on the lock)
    finally:
        src.close()
    db.init()  # apply any migrations the restored copy is missing
    audit(c, u, "db.restore", detail={"from": label, "safety_backup": safety}, ip=client_ip(req))
    return {"restored": label, "safety_backup": safety}


@router.post("/restore")
def restore(body: RestoreIn, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "database")
    if body.confirm != "RESTORE":
        raise HTTPException(400, "Type RESTORE to confirm.")
    if not re.fullmatch(r"astra-[\w-]+\.db", body.name) or not (config.BACKUP_DIR / body.name).is_file():
        raise HTTPException(404, "Backup not found.")
    if c.execute("SELECT COUNT(*) FROM competitions WHERE status IN ('live','paused')").fetchone()[0]:
        raise HTTPException(409, "A competition is live. Restore only when nothing is running.")
    return _restore_from(c, config.BACKUP_DIR / body.name, u, req, body.name)


@router.post("/upload-backup")
async def upload_backup(req: Request, file: UploadFile = File(...), u=Depends(admin), c=Depends(conn)):
    """Add a backup file (e.g. copied from another machine) to the backup list. It is checked before it can be restored."""
    need(u, "database")
    data = await file.read(2 * 1024 ** 3)
    if not data.startswith(b"SQLite format 3\x00"):
        raise HTTPException(400, "That is not a SQLite database file.")
    name = time.strftime("astra-%Y%m%d-%H%M%S-uploaded.db")
    (config.BACKUP_DIR / name).write_bytes(data)
    (config.BACKUP_DIR / name).chmod(0o600)
    audit(c, u, "db.upload_backup", detail={"file": name, "size": len(data)}, ip=client_ip(req))
    return {"file": name}


@router.delete("/backups/{name}")
def delete_backup(name: str, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "database")
    if not re.fullmatch(r"astra-[\w-]+\.db", name) or not (config.BACKUP_DIR / name).is_file():
        raise HTTPException(404, "Backup not found.")
    (config.BACKUP_DIR / name).unlink()
    audit(c, u, "db.delete_backup", detail={"file": name}, ip=client_ip(req))
    return {"ok": True}
