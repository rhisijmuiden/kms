"""Full and incremental backups of the activation database and configuration.

An archive is a tar.gz with a manifest.json. A *chain* starts with a full backup; each
incremental in the chain only holds files whose content changed since the previous backup
in that chain. The manifest of every archive lists all files that exist at that moment, so a
restore applies the full backup and then each incremental in order up to the chosen one.
Archives can be encrypted with a passphrase (scrypt + Fernet/AES).
"""
import base64
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import tarfile
import tempfile
import threading
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from . import config, mailer, remote, store

_lock = threading.Lock()
NAME_RE = re.compile(r"^kms-backup-(\d{8}-\d{6})-(full|inc)-([0-9a-f]{8})-(\d{3})\.tar\.gz(\.enc)?$")
MAGIC = b"KMSENC1\n"


class BackupError(Exception):
    pass


def _sources():
    return {
        "data/kms.db": (config.KMS_DB, "sqlite"),
        "data/console.db": (config.CONSOLE_DB, "sqlite"),
        "conf/kms.env": (config.KMS_ENV_FILE, "file"),
        "conf/secret.key": (config.SECRET_KEY_FILE, "file"),
    }


def parse_name(name):
    m = NAME_RE.match(name)
    if not m:
        return None
    ts, kind, chain, seq, enc = m.groups()
    return {"name": name, "ts": ts, "kind": kind, "chain": chain, "seq": int(seq), "encrypted": bool(enc),
            "created": time.mktime(time.strptime(ts, "%Y%m%d-%H%M%S"))}


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _snapshot(stage: Path):
    """Copy all sources into `stage` consistently. Returns {logical name: sha256}."""
    hashes = {}
    for logical, (path, kind) in _sources().items():
        if not Path(path).exists():
            continue
        dest = stage / logical
        dest.parent.mkdir(parents=True, exist_ok=True)
        if kind == "sqlite":
            src = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
            dst = sqlite3.connect(dest)
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
            # normalise: same content -> same hash (VACUUM rewrites pages deterministically)
            con = sqlite3.connect(dest)
            con.execute("VACUUM")
            con.close()
        else:
            shutil.copy2(path, dest)
        hashes[logical] = _sha256(dest)
    return hashes


def _key(passphrase, salt):
    kdf = Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode()))


def encrypt_bytes(data, passphrase):
    salt = secrets.token_bytes(16)
    return MAGIC + salt + Fernet(_key(passphrase, salt)).encrypt(data)


def decrypt_bytes(data, passphrase):
    if not data.startswith(MAGIC):
        return data
    if not passphrase:
        raise BackupError("This backup is encrypted. Set the backup passphrase first.")
    salt = data[len(MAGIC):len(MAGIC) + 16]
    try:
        return Fernet(_key(passphrase, salt)).decrypt(data[len(MAGIC) + 16:])
    except InvalidToken as exc:
        raise BackupError("Wrong passphrase for this backup.") from exc


def local_backups():
    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for p in config.BACKUP_DIR.iterdir():
        info = parse_name(p.name)
        if info:
            info["size"] = p.stat().st_size
            out.append(info)
    return sorted(out, key=lambda x: x["name"], reverse=True)


def remote_backups():
    s = store.section("backup")
    files = remote.location(s).list()
    out = []
    for f in files:
        info = parse_name(f["name"])
        if info:
            info["size"] = f["size"]
            out.append(info)
    return sorted(out, key=lambda x: x["name"], reverse=True)


def run(kind="incremental", reason="manual", upload=True):
    """Create a backup. kind = 'full' or 'incremental' (falls back to full when no chain exists)."""
    with _lock:
        state = store.get("backup.state") or {}
        if kind != "full" and not state.get("chain"):
            kind = "full"
        stage = Path(tempfile.mkdtemp(prefix="kms-backup-"))
        try:
            hashes = _snapshot(stage)
            if kind == "full":
                chain, seq, base = secrets.token_hex(4), 0, {}
            else:
                chain, seq, base = state["chain"], int(state["seq"]) + 1, state.get("files", {})
            changed = [n for n, h in hashes.items() if base.get(n) != h] if kind != "full" else list(hashes)
            ts = time.strftime("%Y%m%d-%H%M%S")
            tag = "full" if kind == "full" else "inc"
            manifest = {"format": 1, "kind": tag, "chain": chain, "seq": seq, "created": int(time.time()),
                        "version": config.version(), "reason": reason, "files": hashes, "included": changed}
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as tar:
                data = json.dumps(manifest, indent=2).encode()
                ti = tarfile.TarInfo("manifest.json")
                ti.size, ti.mtime = len(data), int(time.time())
                tar.addfile(ti, io.BytesIO(data))
                for logical in changed:
                    tar.add(stage / logical, arcname=logical)
            payload = buf.getvalue()
            passphrase = store.get("backup.passphrase")
            name = f"kms-backup-{ts}-{tag}-{chain}-{seq:03d}.tar.gz"
            if passphrase:
                payload = encrypt_bytes(payload, passphrase)
                name += ".enc"
            config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            target = config.BACKUP_DIR / name
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(payload)
            store.set("backup.state", {"chain": chain, "seq": seq, "files": hashes})
        finally:
            shutil.rmtree(stage, ignore_errors=True)

        result = {"name": name, "kind": tag, "size": len(payload), "changed": changed, "uploaded": False}
        msg = f"Backup {name} created ({len(changed)} changed file(s), {len(payload)} bytes)"
        s = store.section("backup")
        try:
            if upload and s.get("remote_enabled"):
                loc = remote.location(s)
                loc.upload(target)
                _remember_fingerprint(loc)
                result["uploaded"] = True
                msg += f", uploaded to {s['protocol'].upper()} {s['host']}"
            prune()
        except remote.RemoteError as exc:
            store.log_event("error", f"{msg}, but upload failed: {exc}")
            store.set("backup.last", {"ts": int(time.time()), "ok": False, "name": name, "error": str(exc)})
            mailer.notify("backup_fail", "Backup upload failed", [msg, f"Error: {exc}"])
            result["error"] = str(exc)
            return result
        store.log_event("info", msg)
        store.set("backup.last", {"ts": int(time.time()), "ok": True, "name": name, "uploaded": result["uploaded"]})
        mailer.notify("backup_ok", "Backup completed", [msg])
        return result


def run_safe(kind, reason):
    try:
        return run(kind, reason)
    except Exception as exc:  # noqa: BLE001 - scheduler entry point
        store.log_event("error", f"Backup failed: {exc}")
        store.set("backup.last", {"ts": int(time.time()), "ok": False, "error": str(exc)})
        mailer.notify("backup_fail", "Backup failed", [f"The {kind} backup ({reason}) failed.", f"Error: {exc}"])
        return {"error": str(exc)}


def _remember_fingerprint(loc):
    fp = getattr(loc, "fingerprint", None)
    if fp and not store.get("backup.host_fingerprint"):
        store.set("backup.host_fingerprint", fp)


def _chains(items):
    chains = {}
    for b in items:
        chains.setdefault(b["chain"], []).append(b)
    # newest chain first, by its full backup timestamp
    return sorted(chains.values(), key=lambda c: min(x["name"][11:26] for x in c), reverse=True)


def prune():
    keep = max(1, int(store.get("backup.keep_chains") or 4))
    for chain in _chains(local_backups())[keep:]:
        for b in chain:
            (config.BACKUP_DIR / b["name"]).unlink(missing_ok=True)
    s = store.section("backup")
    if s.get("remote_enabled"):
        loc = remote.location(s)
        for chain in _chains(remote_backups())[keep:]:
            for b in chain:
                loc.delete(b["name"])


def _safe_extract(tar, dest):
    allowed = set(_sources()) | {"manifest.json"}
    for member in tar.getmembers():
        if member.name not in allowed or not member.isfile():
            raise BackupError(f"Unexpected entry in backup: {member.name}")
    try:
        tar.extractall(dest, filter="data")
    except TypeError:  # Python < 3.11.4
        tar.extractall(dest)  # noqa: S202 - members validated above


def _chain_for(name, available):
    target = parse_name(name)
    if not target:
        raise BackupError("Not a KMS Console backup file")
    parts = sorted((b for b in available if b["chain"] == target["chain"] and b["seq"] <= target["seq"]),
                   key=lambda b: b["seq"])
    seqs = [b["seq"] for b in parts]
    if seqs != list(range(target["seq"] + 1)):
        raise BackupError(f"Backup chain {target['chain']} is incomplete (have {seqs}). The full backup or an incremental is missing.")
    return parts


def restore(name, source="local"):
    """Restore the state of backup `name`, from the local store or the remote location."""
    with _lock:
        work = Path(tempfile.mkdtemp(prefix="kms-restore-"))
        try:
            if source == "remote":
                s = store.section("backup")
                loc = remote.location(s)
                parts = _chain_for(name, remote_backups())
                for p in parts:
                    loc.download(p["name"], work / p["name"])
            else:
                parts = _chain_for(name, local_backups())
                for p in parts:
                    shutil.copy2(config.BACKUP_DIR / p["name"], work / p["name"])
            passphrase = store.get("backup.passphrase")
            tree = work / "tree"
            tree.mkdir()
            manifest = None
            for p in parts:
                data = decrypt_bytes((work / p["name"]).read_bytes(), passphrase)
                with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
                    _safe_extract(tar, tree)
                manifest = json.loads((tree / "manifest.json").read_text())
            final = set(manifest["files"])
            for logical in list(_sources()):
                f = tree / logical
                if f.exists() and logical not in final:
                    f.unlink()
            for logical, digest in manifest["files"].items():
                if _sha256(tree / logical) != digest:
                    raise BackupError(f"Checksum mismatch for {logical}; backup is damaged.")
        except Exception:
            shutil.rmtree(work, ignore_errors=True)
            raise
    # Safety net: snapshot the current state before overwriting it (outside the lock)
    run("full", reason="pre-restore", upload=False)
    with _lock:
        try:
            for logical, (path, kind) in _sources().items():
                src = tree / logical
                if not src.exists():
                    continue
                path = Path(path)
                path.parent.mkdir(parents=True, exist_ok=True)
                if kind == "sqlite":
                    s_con = sqlite3.connect(src)
                    d_con = sqlite3.connect(path, timeout=30)
                    try:
                        s_con.backup(d_con)
                    finally:
                        d_con.close()
                        s_con.close()
                else:
                    tmp = path.with_suffix(path.suffix + ".restore")
                    shutil.copyfile(src, tmp)
                    os.chmod(tmp, 0o640)
                    os.replace(tmp, path)
            store.set("backup.state", {})  # next backup starts a fresh chain
        finally:
            shutil.rmtree(work, ignore_errors=True)
    store.log_event("info", f"Restored backup {name} from {source} ({len(parts)} archive(s))")
    return {"name": name, "archives": len(parts)}
