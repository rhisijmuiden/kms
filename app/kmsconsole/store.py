"""Console database: settings, users and backup history. Secrets are encrypted at rest."""
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager

from cryptography.fernet import Fernet, InvalidToken

from . import config

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  email TEXT DEFAULT '',
  lang TEXT DEFAULT 'en',
  created INTEGER NOT NULL,
  must_change INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS backups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  kind TEXT NOT NULL,
  chain TEXT NOT NULL,
  seq INTEGER NOT NULL,
  size INTEGER NOT NULL,
  created INTEGER NOT NULL,
  remote TEXT DEFAULT '',
  status TEXT DEFAULT 'ok',
  message TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  level TEXT NOT NULL,
  message TEXT NOT NULL
);
"""

DEFAULTS = {
    # KMS server
    "kms.public_host": "",          # hostname/IP clients use; empty = auto-detect
    "kms.listen": "0.0.0.0",
    "kms.port": 1688,
    "kms.lcid": 1033,
    "kms.activation_interval": 120,
    "kms.renewal_interval": 10080,
    "kms.hwid": "RANDOM",
    "kms.loglevel": "INFO",
    # Mail
    "mail.enabled": False,
    "mail.host": "",
    "mail.port": 587,
    "mail.security": "starttls",     # none | starttls | ssl
    "mail.verify_tls": True,
    "mail.username": "",
    "mail.password": "",             # encrypted
    "mail.from_name": "KMS Console",
    "mail.from_address": "",
    "mail.reply_to": "",
    "mail.recipients": "",
    "mail.notify_backup_ok": False,
    "mail.notify_backup_fail": True,
    "mail.notify_update": True,
    "mail.notify_blocked": True,
    # Backups
    "backup.remote_enabled": False,
    "backup.protocol": "sftp",       # ftp | ftps | scp | sftp
    "backup.host": "",
    "backup.port": 22,
    "backup.username": "",
    "backup.password": "",           # encrypted
    "backup.ssh_key": "",            # encrypted, PEM private key (scp/sftp)
    "backup.host_fingerprint": "",   # trusted on first use (scp/sftp)
    "backup.verify_tls": True,
    "backup.path": "/backups/kms",
    "backup.passphrase": "",         # encrypted; empty = archives not encrypted
    "backup.full_schedule": "weekly",   # off | daily | weekly | monthly
    "backup.full_weekday": 6,           # 0 = monday
    "backup.full_time": "03:00",
    "backup.inc_schedule": "daily",     # off | hourly | daily
    "backup.inc_time": "03:30",
    "backup.keep_chains": 4,
    # Network & access (see network.py / netrender.py)
    "net.mode": "lan",               # lan | caddy | proxy
    "net.domain": "",
    "net.acme_email": "",
    "net.close_console": True,       # caddy mode: port 8080 only from the container itself
    "net.proxy_ips": [],             # [{cidr, descr, added}]
    "net.web_acl": [],               # empty = everyone may open the web interface
    "net.kms_acl": [],
    "net.kms_restrict": False,       # True = only kms_acl may use the KMS port
    "net.blocked_alerted": {},
    "net.last_error": "",
    # First steps checklist on the dashboard
    "onboarding.dismissed": False,
    # Updates
    "update.channel": "production",
    "update.branch_production": "master",
    "update.branch_test": "test",
    "update.auto_check": True,
    "update.last_check": 0,
    "update.last_result": {},
}

SECRET_KEYS = {"mail.password", "backup.password", "backup.ssh_key", "backup.passphrase"}


def _fernet() -> Fernet:
    path = config.SECRET_KEY_FILE
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
        with os.fdopen(fd, "wb") as fh:
            fh.write(key)
    return Fernet(path.read_bytes().strip())


@contextmanager
def connect():
    with _lock:
        config.CONSOLE_DB.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(config.CONSOLE_DB, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()


def init():
    with connect() as con:
        con.executescript(SCHEMA)
        cols = {r["name"] for r in con.execute("PRAGMA table_info(users)")}
        if "must_change" not in cols:  # installs from before 2.3.0
            con.execute("ALTER TABLE users ADD COLUMN must_change INTEGER DEFAULT 0")
    _fernet()  # create the encryption key on first start


def get(key, default=None):
    with connect() as con:
        row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row is None:
        return DEFAULTS.get(key, default)
    value = json.loads(row["value"])
    if key in SECRET_KEYS and value:
        try:
            value = _fernet().decrypt(value.encode()).decode()
        except InvalidToken:
            value = ""
    return value


def set(key, value):  # noqa: A001 - mirrors dict API
    if key in SECRET_KEYS and value:
        value = _fernet().encrypt(str(value).encode()).decode()
    with connect() as con:
        con.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, json.dumps(value)))


def section(prefix):
    """All settings that start with `prefix.` as a dict without the prefix."""
    out = {}
    for key in DEFAULTS:
        if key.startswith(prefix + "."):
            out[key[len(prefix) + 1:]] = get(key)
    return out


def log_event(level, message):
    with connect() as con:
        con.execute("INSERT INTO events(ts, level, message) VALUES(?, ?, ?)", (int(time.time()), level, message[:2000]))
        con.execute("DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 500)")


def events(limit=50):
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))]
