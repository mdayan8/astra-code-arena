"""Operator CLI.
  python -m app.manage create-admin            # interactive; first account should be super_admin
  python -m app.manage info                    # prints the private admin route
  python -m app.manage reset-password USERNAME # admin account recovery from the server console
  python -m app.manage backup                  # immediate database backup
"""
import getpass
import sys

from . import config, db
from .security import check_password_strength, hash_password


def ask(prompt, default=""):
    v = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    return v or default


def password(prompt="Password (min 8 chars)"):
    while True:
        p = getpass.getpass(f"{prompt}: ")
        if len(p) < 8:
            print("Too short.")
            continue
        if getpass.getpass("Repeat: ") != p:
            print("Does not match.")
            continue
        return p


def main(argv):
    db.init()
    c = db.connect()
    cmd = argv[1] if len(argv) > 1 else "help"
    if cmd == "auto-admin":  # non-interactive: first Super Admin with a generated password (used by one-command deploy)
        if db.one(c, "SELECT 1 AS x FROM users WHERE kind='admin'"):
            print("Admin already exists.")
            return
        import secrets
        pw = secrets.token_urlsafe(9)
        c.execute("INSERT INTO users(username,name,password_hash,kind,role,can_approve,must_change_pw,created_at) VALUES('admin','Super Admin',?,'admin','super_admin',1,1,?)",
                  (hash_password(pw), db.now()))
        print(f"ADMIN_USER=admin\nADMIN_PASSWORD={pw}")
        return
    if cmd == "create-admin":
        first = not db.one(c, "SELECT 1 AS x FROM users WHERE kind='admin'")
        username = ask("Username")
        name = ask("Full name", username)
        email = ask("Email (optional)")
        role = "super_admin" if first else ask("Role (super_admin/event_admin/reviewer)", "event_admin")
        if role not in ("super_admin", "event_admin", "reviewer"):
            sys.exit("Invalid role.")
        pw = password()
        check_password_strength(pw)
        c.execute("INSERT INTO users(username,email,name,password_hash,kind,role,can_approve,created_at) VALUES(?,?,?,?,'admin',?,1,?)",
                  (username, email or None, name, hash_password(pw), role, db.now()))
        print(f"Created {role} '{username}'.")
        cmd = "info"
    if cmd == "info":
        from .main import ADMIN
        print(f"Student portal : http://<server-ip>:{config.APP_PORT}/")
        print(f"Admin route    : http://<server-ip>:{config.APP_PORT}/{ADMIN}/   (share only with organisers)")
        print(f"Database       : {config.DB_PATH}")
    elif cmd == "reset-password":
        u = db.one(c, "SELECT * FROM users WHERE username=? AND kind='admin'", argv[2] if len(argv) > 2 else "")
        if not u:
            sys.exit("No such admin.")
        c.execute("UPDATE users SET password_hash=?, failed_logins=0, locked_until=0, status='active' WHERE id=?", (hash_password(password("New password")), u["id"]))
        c.execute("DELETE FROM sessions WHERE user_id=?", (u["id"],))
        print("Password reset.")
    elif cmd == "backup":
        from .worker import backup
        print("Backup written:", config.BACKUP_DIR / backup(c, "manual"))
    elif cmd != "create-admin":
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv)
