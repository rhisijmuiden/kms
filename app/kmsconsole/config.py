"""Paths and static configuration. Everything can be overridden with environment variables
so the app can run in development and tests without root."""
import os
from pathlib import Path

APP_ROOT = Path(os.environ.get("KMS_CONSOLE_ROOT", Path(__file__).resolve().parents[2]))
DATA_DIR = Path(os.environ.get("KMS_DATA_DIR", "/var/lib/kms"))
CONF_DIR = Path(os.environ.get("KMS_CONF_DIR", "/etc/kms-console"))
PYKMS_DIR = Path(os.environ.get("KMS_PYKMS_DIR", "/opt/py-kms/py-kms"))

KMS_DB = Path(os.environ.get("KMS_DB", DATA_DIR / "kms.db"))
CONSOLE_DB = Path(os.environ.get("KMS_CONSOLE_DB", DATA_DIR / "console.db"))
BACKUP_DIR = Path(os.environ.get("KMS_BACKUP_DIR", DATA_DIR / "backups"))
KMS_ENV_FILE = Path(os.environ.get("KMS_ENV_FILE", CONF_DIR / "kms.env"))
SECRET_KEY_FILE = Path(os.environ.get("KMS_SECRET_FILE", CONF_DIR / "secret.key"))

# Privileged helper (installed root-owned, called through sudo). Empty = dry run (dev/tests).
KMS_CTL = os.environ.get("KMS_CTL", "/usr/local/sbin/kms-ctl")
USE_SUDO = os.environ.get("KMS_USE_SUDO", "1") == "1"

LISTEN = os.environ.get("KMS_CONSOLE_LISTEN", "0.0.0.0")
PORT = int(os.environ.get("KMS_CONSOLE_PORT", "8080"))


def version() -> str:
    try:
        return (APP_ROOT / "VERSION").read_text().strip()
    except OSError:
        return "0.0.0-dev"
