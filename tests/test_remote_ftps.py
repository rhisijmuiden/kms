"""FTPS (explicit TLS, protected data channel) against a local pyftpdlib server with a self-signed cert."""
import datetime
import socket
import threading

import pytest

pytest.importorskip("OpenSSL")
pytest.importorskip("pyftpdlib")
from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402
from pyftpdlib.authorizers import DummyAuthorizer  # noqa: E402
from pyftpdlib.handlers import TLS_FTPHandler  # noqa: E402
from pyftpdlib.servers import FTPServer  # noqa: E402

from kmsconsole import backup, remote, store  # noqa: E402


def _cert(path):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
            .sign(key, hashes.SHA256()))
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
                     + cert.public_bytes(serialization.Encoding.PEM))


def test_ftps_roundtrip(env, tmp_path):
    root = tmp_path / "ftps"; root.mkdir()
    pem = tmp_path / "cert.pem"; _cert(pem)
    auth = DummyAuthorizer(); auth.add_user("u", "p", str(root), perm="elradfmwMT")
    handler = type("H", (TLS_FTPHandler,), {"authorizer": auth, "certfile": str(pem), "tls_control_required": True, "tls_data_required": True})
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    server = FTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, kwargs={"timeout": 0.2}, daemon=True).start()
    try:
        for k, v in {"backup.remote_enabled": True, "backup.protocol": "ftps", "backup.host": "127.0.0.1", "backup.port": port,
                     "backup.username": "u", "backup.password": "p", "backup.path": "/b", "backup.verify_tls": False}.items():
            store.set(k, v)
        assert remote.location(store.section("backup")).test() == "OK"
        res = backup.run("full")
        assert res["uploaded"], res
        assert [b["name"] for b in backup.remote_backups()] == [res["name"]]
    finally:
        server.close_all()
