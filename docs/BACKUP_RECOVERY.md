# Backup and recovery

All data is one SQLite database (`astra.db`) plus uploaded PDFs, in the data directory
(Docker volume `astra-data` mounted at `/data`; `./data` without Docker).

## Automatic backups
- The judge worker takes a consistent online backup (SQLite backup API) **every 24 hours** into `<data>/backups/`, keeping the newest `BACKUP_KEEP` (default 14).
- **Admin → System Settings → Back up database now** or `python -m app.manage backup` makes one immediately. Do this right after every competition.
- System Settings shows the time and name of the last backup.

## Copy backups off the server
Keep at least one copy on another machine or drive:

```bash
docker run --rm -v astra-code-arena_astra-data:/data -v "$PWD":/out alpine sh -c 'cp /data/backups/*.db /out/'
```

(Without Docker just copy `data/backups/`.) Uploaded PDFs are in `<data>/uploads/` if you want them too.

## Restore
1. Stop the platform: `docker compose down`.
2. Copy the chosen backup over the live database:
   ```bash
   docker run --rm -v astra-code-arena_astra-data:/data alpine sh -c \
     'cp /data/astra.db /data/astra.db.before-restore; cp /data/backups/astra-YYYYMMDD-HHMMSS-manual.db /data/astra.db; rm -f /data/astra.db-wal /data/astra.db-shm; chmod 600 /data/astra.db'
   ```
3. Start again: `docker compose up -d`. Sign in and check the competitions and leaderboards.

## What happens when something crashes
| Event | Behaviour |
|---|---|
| Web server restarts | Sessions, competitions and timers are in the database; students just refresh. Code being typed is kept in their browser. |
| Judge worker crashes or restarts mid-judging | Submissions are the queue, stored before judging. Jobs left "running" by a dead worker are re-queued within ~60 s. Nothing is lost. |
| Judge infrastructure error (e.g. a toolchain missing) | Retried 3 times, then marked **Internal Judge Error**, which never counts as Wrong Answer or against the student. Fix the cause, then **Leaderboard → Re-judge errors**. |
| Server reboot during a round | Containers start automatically. Timer math uses stored start/pause/extension times, so the end time is unchanged. If the outage was long, use **Extend time**. |
| Power cut | SQLite with WAL keeps committed data. Restore from the last backup only if the database file is damaged (`sqlite3 astra.db "PRAGMA integrity_check"` reports errors). |

Finalised competitions read a frozen leaderboard snapshot, and question versions used by them are never modified, so historical results stay exactly as they were.
