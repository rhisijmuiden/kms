"""Backups through a real (local) FTP server, then restore from the remote location."""
import socket
import sqlite3
import threading

import pytest

pyftpdlib = pytest.importorskip("pyftpdlib")
from pyftpdlib.authorizers import DummyAuthorizer  # noqa: E402
from pyftpdlib.handlers import FTPHandler  # noqa: E402
from pyftpdlib.servers import FTPServer  # noqa: E402

from kmsconsole import backup, config, store  # noqa: E402


@pytest.fixture()
def ftp(tmp_path):
    root = tmp_path / "ftproot"
    root.mkdir()
    auth = DummyAuthorizer()
    auth.add_user("u", "p", str(root), perm="elradfmwMT")
    handler = type("H", (FTPHandler,), {"authorizer": auth})
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    server = FTPServer(("127.0.0.1", port), handler)
    t = threading.Thread(target=server.serve_forever, kwargs={"timeout": 0.2}, daemon=True)
    t.start()
    yield port, root
    server.close_all()


def test_upload_list_restore_from_ftp(env, ftp):
    port, root = ftp
    for k, v in {"backup.remote_enabled": True, "backup.protocol": "ftp", "backup.host": "127.0.0.1",
                 "backup.port": port, "backup.username": "u", "backup.password": "p", "backup.path": "/kms/backups"}.items():
        store.set(k, v)
    full = backup.run("full")
    assert full["uploaded"], full
    con = sqlite3.connect(config.KMS_DB); con.execute("DELETE FROM clients WHERE clientMachineId='a1'"); con.commit(); con.close()
    inc = backup.run("incremental")
    assert inc["uploaded"]
    names = [b["name"] for b in backup.remote_backups()]
    assert full["name"] in names and inc["name"] in names
    # wipe local copies: restore must come from the FTP server
    for p in config.BACKUP_DIR.iterdir():
        p.unlink()
    backup.restore(full["name"], "remote")
    assert sqlite3.connect(config.KMS_DB).execute("SELECT COUNT(*) FROM clients").fetchone()[0] == 2
