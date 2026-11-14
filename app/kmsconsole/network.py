"""Network & access: how the web interface is reached (LAN, Caddy HTTPS, reverse proxy) and the
access lists for the web interface and the KMS port. The firewall itself is nftables inside the
container, applied by kms-ctl from network.json (see netrender.py)."""
import ipaddress
import json
import os
import socket
import ssl
import time

from . import config, kms, mailer, store
from .netrender import MODES, NetError, parse_entry, render_nft, validate

DEFAULTS = {k: v for k, v in store.DEFAULTS.items() if k.startswith("net.")}

_cache = {"ts": 0.0, "value": None}


def settings():
    return {k.split(".", 1)[1]: store.get(k) for k in DEFAULTS}


def _network_file():
    return config.CONF_DIR / "network.json"


def config_dict(overrides=None):
    s = settings()
    if overrides:
        s.update(overrides)
    return {
        "mode": s["mode"], "console_port": config.PORT, "kms_port": int(store.get("kms.port")),
        "web_acl": s["web_acl"], "kms_acl": s["kms_acl"], "kms_restrict": s["kms_restrict"],
        "proxy_ips": s["proxy_ips"], "close_console": s["close_console"],
        "domain": s["domain"], "acme_email": s["acme_email"],
    }


def check(overrides=None):
    """Validate a proposed change before it is stored. Raises NetError."""
    return validate(config_dict(overrides))


def write_config():
    path = _network_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(config_dict(), indent=2))
    os.replace(tmp, path)


def apply():
    """Write network.json and let kms-ctl load the firewall and Caddy. Raises system.SystemError_."""
    from . import system
    invalidate()
    write_config()
    try:
        system.ctl("net-apply", timeout=90)
        store.set("net.last_error", "")
    except system.SystemError_ as exc:
        store.set("net.last_error", str(exc))
        raise


def invalidate():
    _cache["ts"] = 0.0


def trusted_proxies():
    """Networks whose X-Forwarded-* headers are believed (cached for a few seconds)."""
    now = time.time()
    if _cache["value"] is not None and now - _cache["ts"] < 5:
        return _cache["value"]
    try:
        mode = store.get("net.mode")
        if mode == "caddy":
            nets = [ipaddress.ip_network("127.0.0.1/32"), ipaddress.ip_network("::1/128")]
        elif mode == "proxy":
            nets = [parse_entry(e["cidr"])[0] for e in store.get("net.proxy_ips") or []]
        else:
            nets = []
        acl = [parse_entry(e["cidr"])[0] for e in store.get("net.web_acl") or []]
    except (NetError, KeyError, TypeError):
        nets, acl = [], []
    _cache.update(ts=now, value=(nets, acl))
    return nets, acl


def _in(ip, nets):
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return any(addr in n for n in nets)


def covered(ip, entries):
    nets = []
    for e in entries:
        try:
            nets.append(parse_entry(e["cidr"])[0])
        except (NetError, KeyError, TypeError):
            continue
    return _in(ip, nets)


class ProxyMiddleware:
    """Takes the client address and scheme from X-Forwarded-* only when the request comes from a trusted proxy."""

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        remote = environ.get("REMOTE_ADDR", "")
        proxies, _ = trusted_proxies()
        if proxies and _in(remote, proxies):
            chain = [p.strip() for p in environ.get("HTTP_X_FORWARDED_FOR", "").split(",") if p.strip()]
            client = remote
            for hop in reversed(chain):   # right to left, skip our own proxies
                client = hop
                if not _in(hop, proxies):
                    break
            environ["kms.proxy_addr"] = remote
            environ["REMOTE_ADDR"] = client
            proto = environ.get("HTTP_X_FORWARDED_PROTO", "").split(",")[0].strip().lower()
            if proto in ("http", "https"):
                environ["wsgi.url_scheme"] = proto
            host = environ.get("HTTP_X_FORWARDED_HOST", "").split(",")[0].strip()
            if host:
                environ["HTTP_HOST"] = host
        return self.app(environ, start_response)


def web_allowed(ip):
    """The app-level check of the web access list (nftables only sees the proxy in proxy mode)."""
    _, acl = trusted_proxies()
    return not acl or _in(ip, acl) or _in(ip, [ipaddress.ip_network("127.0.0.1/32"), ipaddress.ip_network("::1/128")])


# ------------------------------------------------------------------ lists

def add_entry(scope, cidr, descr, client_ip=None):
    """Add to 'web' or 'kms'. Returns (entry, is_public). Raises NetError."""
    net, public = parse_entry(cidr)
    key = f"net.{scope}_acl"
    entries = list(store.get(key) or [])
    if any(parse_entry(e["cidr"])[0] == net for e in entries):
        raise NetError("this address is already in the list")
    entry = {"cidr": str(net) if net.num_addresses > 1 else str(net.network_address),
             "descr": (descr or "").strip()[:80], "added": int(time.time())}
    new = entries + [entry]
    if scope == "web" and client_ip and not entries and not covered(client_ip, new):
        # The first entry switches the list on: make sure the admin who adds it keeps access.
        new.append({"cidr": client_ip, "descr": "Your address (added automatically)", "added": int(time.time())})
    check({f"{scope}_acl": new})
    store.set(key, new)
    return entry, public


def remove_entry(scope, cidr, client_ip=None):
    key = f"net.{scope}_acl"
    entries = [e for e in (store.get(key) or []) if e["cidr"] != cidr]
    if scope == "web" and client_ip and entries and not covered(client_ip, entries):
        raise NetError("you would lock yourself out: your own address is not covered by the remaining entries")
    if scope == "kms" and store.get("net.kms_restrict") and not entries:
        raise NetError("the last entry cannot be removed while the KMS list is switched on")
    store.set(key, entries)


def reset_web_access():
    """Used by `kms-ctl access-reset` from the Proxmox console when someone is locked out."""
    store.set("net.web_acl", [])
    if store.get("net.mode") == "proxy":
        store.set("net.mode", "lan")
    store.set("net.close_console", False)
    invalidate()
    write_config()


def local_network():
    ip = kms.detect_ip()
    try:
        return str(ipaddress.ip_network(f"{ip}/24", strict=False))
    except ValueError:
        return ""


def activity(entries, days=30):
    """Activations per KMS list entry over the last days, from the py-kms client table."""
    since = time.time() - days * 86400
    rows = [r for r in kms.clients() if (r.get("lastRequestTime") or 0) >= since and r.get("machineIp")]
    out = {}
    for e in entries:
        try:
            net = parse_entry(e["cidr"])[0]
        except NetError:
            continue
        out[e["cidr"]] = sum(1 for r in rows if _in(r["machineIp"], [net]))
    return out


# ------------------------------------------------------------------ blocked attempts

def blocked():
    """Addresses that hit the KMS port while not on the list: [{ip, attempts, expires}]."""
    from . import system
    try:
        raw = system.ctl("net-blocked", timeout=20)
    except system.SystemError_:
        return []
    out = []
    for line in raw.splitlines():
        try:
            data = json.loads(line)
        except ValueError:
            continue
        for item in data.get("nftables", []):
            for elem in (item.get("set") or {}).get("elem", []) or []:
                e = elem.get("elem", elem) if isinstance(elem, dict) else {"val": elem}
                ip = e.get("val")
                if not isinstance(ip, str):
                    continue
                out.append({"ip": ip, "attempts": int((e.get("counter") or {}).get("packets", 0)),
                            "expires": int(e.get("expires", 0))})
    return sorted(out, key=lambda x: -x["attempts"])


def check_blocked(threshold=20):
    """Scheduler job: mail once about addresses that keep trying the KMS port."""
    if not store.get("net.kms_restrict") or not store.get("mail.enabled") or not store.get("mail.notify_blocked"):
        return
    alerted = dict(store.get("net.blocked_alerted") or {})
    now = int(time.time())
    alerted = {ip: ts for ip, ts in alerted.items() if now - ts < 7 * 86400}
    new = [b for b in blocked() if b["attempts"] >= threshold and b["ip"] not in alerted]
    if new:
        lines = ["These addresses tried to use the KMS server but are not on the access list:", ""]
        lines += [f"{b['ip']}: {b['attempts']} attempts" for b in new]
        lines += ["", "Allow them under Network & access if they are yours."]
        mailer.notify("blocked", "KMS Console: blocked activation attempts", lines)
        for b in new:
            alerted[b["ip"]] = now
        store.log_event("warning", f"Blocked KMS attempts from {', '.join(b['ip'] for b in new)}")
    store.set("net.blocked_alerted", alerted)


# ------------------------------------------------------------------ HTTPS check

def check_https(domain, timeout=6):
    """Connect to the domain on 443 and report the certificate. Returns dict(ok, error, expires, issuer)."""
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((domain, 443), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=domain) as tls:
                cert = tls.getpeercert()
        expires = ssl.cert_time_to_seconds(cert["notAfter"])
        issuer = dict(x[0] for x in cert.get("issuer", ())).get("organizationName", "")
        return {"ok": True, "expires": expires, "issuer": issuer, "error": ""}
    except (OSError, ssl.SSLError, KeyError, ValueError) as exc:
        return {"ok": False, "expires": 0, "issuer": "", "error": str(exc)}


__all__ = ["MODES", "NetError", "render_nft"]
