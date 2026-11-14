"""Remote backup locations: FTP, FTPS (explicit TLS), SCP and SFTP."""
import ftplib
import io
import posixpath
import shlex
import ssl
from pathlib import Path

import paramiko
from scp import SCPClient


class RemoteError(Exception):
    pass


PREFIX = "kms-backup-"


def _is_backup(name):
    return name.startswith(PREFIX) and (name.endswith(".tar.gz") or name.endswith(".tar.gz.enc"))


class _FTPBase:
    def __init__(self, s):
        self.s = s
        self.path = s.get("path") or "/"

    def _ftp(self):
        raise NotImplementedError

    def _open(self):
        try:
            ftp = self._ftp()
            ftp.connect(self.s["host"], int(self.s.get("port") or 21), timeout=30)
            ftp.login(self.s.get("username") or "anonymous", self.s.get("password") or "")
            if isinstance(ftp, ftplib.FTP_TLS):
                ftp.prot_p()
            ftp.set_pasv(True)
            self._cd(ftp)
            return ftp
        except (ftplib.all_errors) as exc:
            raise RemoteError(str(exc)) from exc

    def _cd(self, ftp):
        for part in [p for p in self.path.split("/") if p]:
            try:
                ftp.cwd(part)
            except ftplib.error_perm:
                ftp.mkd(part)
                ftp.cwd(part)
        if self.path.startswith("/") and not [p for p in self.path.split("/") if p]:
            ftp.cwd("/")

    def test(self):
        ftp = self._open()
        try:
            probe = PREFIX + "write-test.tmp"
            ftp.storbinary(f"STOR {probe}", io.BytesIO(b"ok"))
            ftp.delete(probe)
        except ftplib.all_errors as exc:
            raise RemoteError(f"Connected, but cannot write in {self.path}: {exc}") from exc
        finally:
            ftp.quit()
        return "OK"

    def upload(self, local: Path):
        ftp = self._open()
        try:
            with open(local, "rb") as fh:
                ftp.storbinary(f"STOR {local.name}", fh)
        except ftplib.all_errors as exc:
            raise RemoteError(str(exc)) from exc
        finally:
            ftp.quit()

    def list(self):
        ftp = self._open()
        try:
            out = []
            try:
                for name, facts in ftp.mlsd(facts=["size", "type"]):
                    if facts.get("type") == "file" and _is_backup(name):
                        out.append({"name": name, "size": int(facts.get("size", 0))})
            except ftplib.error_perm:
                for name in ftp.nlst():
                    name = posixpath.basename(name)
                    if _is_backup(name):
                        try:
                            size = ftp.size(name) or 0
                        except ftplib.all_errors:
                            size = 0
                        out.append({"name": name, "size": size})
            return sorted(out, key=lambda x: x["name"])
        except ftplib.all_errors as exc:
            raise RemoteError(str(exc)) from exc
        finally:
            ftp.quit()

    def download(self, name, local: Path):
        ftp = self._open()
        try:
            with open(local, "wb") as fh:
                ftp.retrbinary(f"RETR {name}", fh.write)
        except ftplib.all_errors as exc:
            raise RemoteError(str(exc)) from exc
        finally:
            ftp.quit()

    def delete(self, name):
        ftp = self._open()
        try:
            ftp.delete(name)
        except ftplib.all_errors as exc:
            raise RemoteError(str(exc)) from exc
        finally:
            ftp.quit()


class FTP(_FTPBase):
    def _ftp(self):
        return ftplib.FTP()


class _ReusedSessionFTPS(ftplib.FTP_TLS):
    """Many FTPS servers (vsftpd, ProFTPD, FileZilla) require TLS session reuse on the data channel."""

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(conn, server_hostname=self.host, session=self.sock.session)
        return conn, size


class FTPS(_FTPBase):
    def _ftp(self):
        ctx = ssl.create_default_context()
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        if not self.s.get("verify_tls", True):
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return _ReusedSessionFTPS(context=ctx)


class _TOFUPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, expected):
        self.expected = expected
        self.seen = None

    def missing_host_key(self, client, hostname, key):
        fp = f"{key.get_name()} {key.fingerprint}"
        self.seen = fp
        if self.expected and self.expected != fp:
            raise RemoteError(f"Host key changed! Expected {self.expected}, got {fp}. Clear the fingerprint in settings if this is intended.")


class _SSHBase:
    def __init__(self, s):
        self.s = s
        self.path = s.get("path") or "."
        self.fingerprint = None

    def _client(self):
        client = paramiko.SSHClient()
        policy = _TOFUPolicy(self.s.get("host_fingerprint") or "")
        client.set_missing_host_key_policy(policy)
        pkey = None
        if self.s.get("ssh_key"):
            pkey = _load_key(self.s["ssh_key"], self.s.get("password") or None)
        try:
            client.connect(self.s["host"], port=int(self.s.get("port") or 22), username=self.s.get("username") or None,
                           password=None if pkey else (self.s.get("password") or None), pkey=pkey,
                           timeout=30, banner_timeout=30, auth_timeout=30, look_for_keys=False, allow_agent=False)
        except RemoteError:
            raise
        except (paramiko.SSHException, OSError) as exc:
            raise RemoteError(str(exc)) from exc
        self.fingerprint = policy.seen
        return client

    def _exec(self, client, cmd):
        _, stdout, stderr = client.exec_command(cmd, timeout=60)
        code = stdout.channel.recv_exit_status()
        out = stdout.read().decode(errors="replace")
        if code != 0:
            raise RemoteError(stderr.read().decode(errors="replace").strip() or f"exit {code}")
        return out

    def _mkdir(self, client):
        self._exec(client, f"mkdir -p {shlex.quote(self.path)}")


class SCP(_SSHBase):
    def test(self):
        client = self._client()
        try:
            self._mkdir(client)
            probe = posixpath.join(self.path, PREFIX + "write-test.tmp")
            self._exec(client, f"touch {shlex.quote(probe)} && rm -f {shlex.quote(probe)}")
        finally:
            client.close()
        return self.fingerprint or "OK"

    def upload(self, local: Path):
        client = self._client()
        try:
            self._mkdir(client)
            with SCPClient(client.get_transport(), socket_timeout=60) as scp:
                scp.put(str(local), remote_path=posixpath.join(self.path, local.name))
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(str(exc)) from exc
        finally:
            client.close()

    def list(self):
        client = self._client()
        try:
            self._mkdir(client)
            out = self._exec(client, f"cd {shlex.quote(self.path)} && for f in {PREFIX}*; do [ -f \"$f\" ] && printf '%s\\t%s\\n' \"$f\" \"$(wc -c < \"$f\")\"; done; true")
        finally:
            client.close()
        files = []
        for line in out.splitlines():
            if "\t" in line:
                name, size = line.split("\t", 1)
                if _is_backup(name):
                    files.append({"name": name, "size": int(size.strip() or 0)})
        return sorted(files, key=lambda x: x["name"])

    def download(self, name, local: Path):
        client = self._client()
        try:
            with SCPClient(client.get_transport(), socket_timeout=60) as scp:
                scp.get(posixpath.join(self.path, name), local_path=str(local))
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(str(exc)) from exc
        finally:
            client.close()

    def delete(self, name):
        client = self._client()
        try:
            self._exec(client, f"rm -f {shlex.quote(posixpath.join(self.path, name))}")
        finally:
            client.close()


class SFTP(_SSHBase):
    def _sftp(self, client):
        sftp = client.open_sftp()
        cur = ""
        for part in self.path.split("/"):
            if not part:
                cur = "/" if not cur else cur
                continue
            cur = posixpath.join(cur, part) if cur else part
            try:
                sftp.stat(cur)
            except OSError:
                sftp.mkdir(cur)
        sftp.chdir(self.path)
        return sftp

    def _run(self, fn):
        client = self._client()
        try:
            sftp = self._sftp(client)
            try:
                return fn(sftp)
            finally:
                sftp.close()
        except RemoteError:
            raise
        except (paramiko.SSHException, OSError) as exc:
            raise RemoteError(str(exc)) from exc
        finally:
            client.close()

    def test(self):
        def fn(sftp):
            probe = PREFIX + "write-test.tmp"
            with sftp.open(probe, "w") as fh:
                fh.write("ok")
            sftp.remove(probe)
        self._run(fn)
        return self.fingerprint or "OK"

    def upload(self, local: Path):
        self._run(lambda sftp: sftp.put(str(local), local.name))

    def list(self):
        return self._run(lambda sftp: sorted(
            ({"name": a.filename, "size": a.st_size} for a in sftp.listdir_attr() if _is_backup(a.filename)),
            key=lambda x: x["name"]))

    def download(self, name, local: Path):
        self._run(lambda sftp: sftp.get(name, str(local)))

    def delete(self, name):
        self._run(lambda sftp: sftp.remove(name))


def _load_key(pem, passphrase=None):
    for cls in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            return cls.from_private_key(io.StringIO(pem), password=passphrase)
        except (paramiko.SSHException, ValueError):
            continue
    raise RemoteError("Unsupported or invalid SSH private key")


PROTOCOLS = {"ftp": FTP, "ftps": FTPS, "scp": SCP, "sftp": SFTP}


def location(settings):
    proto = settings.get("protocol", "sftp")
    if proto not in PROTOCOLS:
        raise RemoteError(f"Unknown protocol {proto}")
    if not settings.get("host"):
        raise RemoteError("No remote host configured")
    return PROTOCOLS[proto](settings)
