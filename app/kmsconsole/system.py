"""Talks to the privileged helper `kms-ctl` (via sudo) for service control and updates."""
import json
import os
import re
import subprocess
import time

from . import config, mailer, store

BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")


class SystemError_(Exception):
    pass


def _available():
    return bool(config.KMS_CTL) and os.path.exists(config.KMS_CTL)


def ctl(*args, timeout=120):
    if not _available():
        raise SystemError_("kms-ctl is not installed (development mode)")
    cmd = (["sudo", "-n"] if config.USE_SUDO else []) + [config.KMS_CTL, *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise SystemError_(f"kms-ctl {args[0]} timed out") from exc
    if proc.returncode != 0:
        raise SystemError_((proc.stderr or proc.stdout).strip() or f"kms-ctl {args[0]} failed ({proc.returncode})")
    return proc.stdout


def write_env():
    """Write the py-kms service environment from the settings. Values are validated by the views."""
    s = store.section("kms")
    lines = [
        "# Managed by KMS Console. Changes here are overwritten from the web GUI.",
        f"KMS_LISTEN={s['listen']}",
        f"KMS_PORT={int(s['port'])}",
        f"KMS_LCID={int(s['lcid'])}",
        f"KMS_ACTIVATION_INTERVAL={int(s['activation_interval'])}",
        f"KMS_RENEWAL_INTERVAL={int(s['renewal_interval'])}",
        f"KMS_HWID={s['hwid']}",
        f"KMS_LOGLEVEL={s['loglevel']}",
        f"KMS_DB={config.KMS_DB}",
        "",
    ]
    config.KMS_ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.KMS_ENV_FILE.with_suffix(".tmp")
    tmp.write_text("\n".join(lines))
    os.replace(tmp, config.KMS_ENV_FILE)


def clear_credentials():
    """Remove the generated password from the console banner and /root/kms-credentials.txt."""
    try:
        ctl("clear-credentials", timeout=20)
    except SystemError_:
        pass


def restart_server():
    write_env()
    return ctl("restart-server")


def status():
    try:
        return json.loads(ctl("status", timeout=20))
    except (SystemError_, ValueError):
        return {}


def logs(lines=200):
    try:
        return ctl("logs", str(int(lines)), timeout=20)
    except SystemError_ as exc:
        return str(exc)


def branch_for(channel):
    key = "update.branch_test" if channel == "test" else "update.branch_production"
    branch = store.get(key)
    if not BRANCH_RE.match(branch or ""):
        raise SystemError_("Invalid branch name")
    return branch


def _vtuple(v):
    parts = re.findall(r"\d+", v.split("-")[0])[:3]
    return tuple(int(p) for p in parts + ["0"] * (3 - len(parts)))


def check_update(channel=None, notify=False):
    channel = channel or store.get("update.channel")
    branch = branch_for(channel)
    raw = ctl("check", branch, timeout=90)
    info = json.loads(raw)
    current = config.version()
    info.update({
        "channel": channel,
        "branch": branch,
        "current_version": current,
        "checked": int(time.time()),
        "available": info.get("remote_commit") != info.get("local_commit") or info.get("local_branch") != branch,
    })
    info["newer"] = _vtuple(info.get("remote_version") or "0") > _vtuple(current)
    previous = store.get("update.last_result") or {}
    store.set("update.last_check", info["checked"])
    store.set("update.last_result", info)
    if notify and info["available"] and previous.get("remote_commit") != info.get("remote_commit"):
        mailer.notify("update", f"KMS Console update available: {info.get('remote_version')}",
                      [f"A new version is available on the {channel} channel (branch {branch}).",
                       f"Installed: {current} ({(info.get('local_commit') or '')[:7]})",
                       f"Available: {info.get('remote_version')} ({(info.get('remote_commit') or '')[:7]})",
                       "Open KMS Console and click Info to update."])
    return info


def start_update(channel):
    branch = branch_for(channel)
    store.set("update.channel", channel)
    store.log_event("info", f"Update started to branch {branch} ({channel})")
    return ctl("update", branch, timeout=30)


def update_log():
    try:
        return ctl("update-log", timeout=20)
    except SystemError_ as exc:
        return str(exc)
