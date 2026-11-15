"""Validates the network settings and renders the nftables ruleset and the Caddyfile.

Standard library only: kms-ctl runs this file as root with `python3 -I netrender.py <network.json> <outdir>`,
so everything coming from the (unprivileged) web GUI is validated again here before it reaches nft or Caddy.
"""
import ipaddress
import json
import os
import re
import sys

MODES = ("lan", "caddy", "proxy")
TABLE = "kmsconsole"
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}$")
MAX_ENTRIES = 500

# A public range wider than these is refused: it would open the service to a large part of the internet.
MIN_PUBLIC_PREFIX = {4: 24, 6: 48}


class NetError(ValueError):
    pass


def parse_entry(text):
    """Return (network, is_public) or raise NetError with a reason the GUI can show."""
    text = (text or "").strip()
    try:
        net = ipaddress.ip_network(text, strict=False)
    except ValueError as exc:
        raise NetError("not a valid IP address or range") from exc
    if net.prefixlen == 0:
        raise NetError("0.0.0.0/0 and ::/0 would allow the whole internet")
    public = not (net.is_private or net.is_loopback or net.is_link_local)
    if public and net.prefixlen < MIN_PUBLIC_PREFIX[net.version]:
        raise NetError(f"a public range may be at most /{MIN_PUBLIC_PREFIX[net.version]}")
    return net, public


def _nets(entries):
    if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
        raise NetError("invalid list")
    out = []
    for e in entries:
        cidr = e.get("cidr") if isinstance(e, dict) else e
        out.append(parse_entry(str(cidr))[0])
    return out


def _port(value):
    port = int(value)
    if not 1 <= port <= 65535:
        raise NetError("invalid port")
    return port


def validate(cfg):
    """Normalise a network.json dict. Raises NetError."""
    if not isinstance(cfg, dict):
        raise NetError("invalid settings")
    mode = cfg.get("mode", "lan")
    if mode not in MODES:
        raise NetError("invalid mode")
    out = {
        "mode": mode,
        "console_port": _port(cfg.get("console_port", 8080)),
        "kms_port": _port(cfg.get("kms_port", 1688)),
        "web_acl": _nets(cfg.get("web_acl", [])),
        "kms_acl": _nets(cfg.get("kms_acl", [])),
        "kms_restrict": bool(cfg.get("kms_restrict", False)),
        "proxy_ips": _nets(cfg.get("proxy_ips", [])),
        "close_console": bool(cfg.get("close_console", True)),
        "domain": str(cfg.get("domain", "")).strip().lower(),
        "acme_email": str(cfg.get("acme_email", "")).strip(),
    }
    if out["kms_restrict"] and not out["kms_acl"]:
        raise NetError("the KMS access list is empty, so every client would be blocked")
    if mode == "caddy":
        if not DOMAIN_RE.match(out["domain"]):
            raise NetError("a domain name is required for HTTPS with Caddy")
        if out["acme_email"] and not EMAIL_RE.match(out["acme_email"]):
            raise NetError("invalid email address")
    if mode == "proxy" and not out["proxy_ips"]:
        raise NetError("add the IP address of your reverse proxy")
    return out


def _set(name, family, nets, dynamic=False):
    kind = "ipv4_addr" if family == 4 else "ipv6_addr"
    if dynamic:
        return f"  set {name} {{ type {kind}; size 65535; flags dynamic,timeout; timeout 7d; }}"
    elems = sorted({str(n) for n in nets if n.version == family})
    body = f" elements = {{ {', '.join(elems)} }}" if elems else ""
    return f"  set {name} {{ type {kind}; flags interval; auto-merge;{body} }}"


def render_nft(cfg):
    """Return the nftables ruleset, or '' when nothing needs filtering."""
    web_restricted = bool(cfg["web_acl"])
    console_rule = None   # what happens to the console port
    if cfg["mode"] == "caddy" and cfg["close_console"]:
        console_rule = "drop"
    elif cfg["mode"] == "proxy" or web_restricted:
        console_rule = "list"
    https_rule = cfg["mode"] == "caddy" and web_restricted

    if not (cfg["kms_restrict"] or console_rule or https_rule):
        return ""

    lines = [f"# Managed by KMS Console (Network & access). Changes here are overwritten.",
             f"table inet {TABLE}", f"delete table inet {TABLE}", f"table inet {TABLE} {{"]
    for fam in (4, 6):
        lines.append(_set(f"web{fam}", fam, cfg["web_acl"]))
        lines.append(_set(f"proxy{fam}", fam, cfg["proxy_ips"]))
        lines.append(_set(f"kms{fam}", fam, cfg["kms_acl"]))
        lines.append(_set(f"blocked{fam}", fam, [], dynamic=True))
    lines += [
        "  chain input {",
        "    type filter hook input priority filter; policy accept;",
        '    iif "lo" accept',
        "    ct state established,related accept",
    ]
    if cfg["kms_restrict"]:
        lines.append(f"    tcp dport {cfg['kms_port']} jump kms")
    if console_rule:
        lines.append(f"    tcp dport {cfg['console_port']} jump console")
    if https_rule:
        lines.append("    tcp dport 443 jump web")
    lines.append("  }")

    lines += ["  chain kms {", "    ip saddr @kms4 accept", "    ip6 saddr @kms6 accept",
              "    update @blocked4 { ip saddr counter }", "    update @blocked6 { ip6 saddr counter }",
              "    drop", "  }"]
    lines += ["  chain web {", "    ip saddr @web4 accept", "    ip6 saddr @web6 accept", "    drop", "  }"]
    lines.append("  chain console {")
    if console_rule == "list":
        if cfg["mode"] == "proxy":
            lines += ["    ip saddr @proxy4 accept", "    ip6 saddr @proxy6 accept"]
        if web_restricted:
            lines += ["    ip saddr @web4 accept", "    ip6 saddr @web6 accept", "    drop"]
        elif cfg["mode"] != "proxy":
            lines.append("    accept")
        else:
            lines.append("    drop")
    else:
        lines.append("    drop")
    lines += ["  }", "}", ""]
    return "\n".join(lines)


def render_caddy(cfg):
    if cfg["mode"] != "caddy":
        return ""
    glob = f"{{\n\temail {cfg['acme_email']}\n}}\n\n" if cfg["acme_email"] else ""
    return (f"# Managed by KMS Console (Network & access). Changes here are overwritten.\n{glob}"
            f"{cfg['domain']} {{\n\tencode zstd gzip\n\treverse_proxy 127.0.0.1:{cfg['console_port']}\n"
            f"\theader Strict-Transport-Security \"max-age=31536000\"\n}}\n")


def main(argv):
    if len(argv) != 3:
        print("usage: netrender.py <network.json> <outdir>", file=sys.stderr)
        return 2
    try:
        with open(argv[1], encoding="utf-8") as fh:
            cfg = validate(json.load(fh))
    except (OSError, ValueError) as exc:
        print(f"invalid network settings: {exc}", file=sys.stderr)
        return 1
    outdir = argv[2]
    for name, text in (("kmsconsole.nft", render_nft(cfg)), ("Caddyfile", render_caddy(cfg))):
        path = os.path.join(outdir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    print(cfg["mode"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
