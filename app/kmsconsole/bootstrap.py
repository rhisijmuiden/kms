"""First-run helpers, called as root through kms-ctl (installer, first boot of the CT template, password reset).

    python -m kmsconsole.bootstrap init-admin [username]     creates the administrator when there is no user yet
    python -m kmsconsole.bootstrap reset-password [username] sets a new random password (creates the user when missing)

Both print the generated password on stdout (nothing when init-admin finds an existing user) and mark the
account so the web interface asks for a new password at the first login.
"""
import re
import secrets
import sys
import time

from werkzeug.security import generate_password_hash

from . import store

USERNAME_RE = re.compile(r"^[A-Za-z0-9._@-]{3,64}$")
# No look-alikes (0/O, 1/l/I) and no backslash: the password is also shown in /etc/issue on the console.
_ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_password():
    """Four groups of four, for example Kx7m-Qp2v-9TzR-wL4e (about 128 bits)."""
    return "-".join("".join(secrets.choice(_ALPHABET) for _ in range(4)) for _ in range(4))


def user_count():
    with store.connect() as con:
        return con.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def set_password(username, password, must_change=True):
    with store.connect() as con:
        cur = con.execute("UPDATE users SET password_hash=?, must_change=? WHERE username=?",
                          (generate_password_hash(password), int(must_change), username))
        if cur.rowcount == 0:
            con.execute("INSERT INTO users(username, password_hash, email, lang, created, must_change) VALUES(?,?,?,?,?,?)",
                        (username, generate_password_hash(password), "", "en", int(time.time()), int(must_change)))


def init_admin(username="admin"):
    """Create the administrator with a random password. Returns the password, or None when users exist."""
    if user_count() > 0:
        return None
    password = generate_password()
    set_password(username, password)
    return password


def reset_password(username="admin"):
    password = generate_password()
    set_password(username, password)
    return password


def main(argv):
    if len(argv) < 2 or argv[1] not in ("init-admin", "reset-password"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    username = argv[2] if len(argv) > 2 else "admin"
    if not USERNAME_RE.match(username):
        print("invalid username", file=sys.stderr)
        return 2
    store.init()
    password = init_admin(username) if argv[1] == "init-admin" else reset_password(username)
    if password:
        print(password)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
