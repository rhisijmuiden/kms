import json
import subprocess
import shutil

import pytest

from kmsconsole import netrender
from kmsconsole.netrender import NetError, parse_entry, render_caddy, render_nft, validate


def cfg(**kw):
    base = {"mode": "lan", "console_port": 8080, "kms_port": 1688, "web_acl": [], "kms_acl": [],
            "kms_restrict": False, "proxy_ips": [], "close_console": True, "domain": "", "acme_email": ""}
    base.update(kw)
    return validate(base)


@pytest.mark.parametrize("bad", ["0.0.0.0/0", "::/0", "8.8.0.0/16", "2001:db8::/32x", "nonsense", "1.2.3.4; drop"])
def test_rejects_dangerous_entries(bad):
    with pytest.raises(NetError):
        parse_entry(bad)


def test_private_and_small_public_allowed():
    assert parse_entry("10.10.0.0/16") == (parse_entry("10.10.0.0/16")[0], False)
    assert parse_entry("185.93.12.40")[1] is True
    assert parse_entry("185.93.12.0/24")[1] is True


def test_nothing_to_filter_gives_empty_ruleset():
    assert render_nft(cfg()) == ""


def test_kms_list_blocks_and_counts():
    out = render_nft(cfg(kms_restrict=True, kms_acl=[{"cidr": "10.10.0.0/16"}, {"cidr": "185.93.12.40"}]))
    assert "tcp dport 1688 jump kms" in out
    assert "elements = { 10.10.0.0/16, 185.93.12.40/32 }" in out
    assert "update @blocked4 { ip saddr counter }" in out
    assert "delete table inet kmsconsole" in out


def test_kms_restrict_needs_entries():
    with pytest.raises(NetError):
        cfg(kms_restrict=True)


def test_caddy_mode_closes_console_and_filters_443():
    c = cfg(mode="caddy", domain="kms.example.nl", acme_email="a@example.nl", web_acl=[{"cidr": "84.105.22.17"}])
    out = render_nft(c)
    assert "tcp dport 8080 jump console" in out and "tcp dport 443 jump web" in out
    assert "dport 80 " not in out   # port 80 stays open for ACME
    caddy = render_caddy(c)
    assert "kms.example.nl {" in caddy and "reverse_proxy 127.0.0.1:8080" in caddy and "email a@example.nl" in caddy


def test_caddy_needs_valid_domain():
    for d in ("", "localhost", "kms.example.nl\n}\n:80 {", "bad_domain.nl"):
        with pytest.raises(NetError):
            cfg(mode="caddy", domain=d)


def test_proxy_mode_only_proxy_and_list():
    with pytest.raises(NetError):
        cfg(mode="proxy")
    out = render_nft(cfg(mode="proxy", proxy_ips=[{"cidr": "10.10.4.10"}]))
    console = out.split("chain console {")[1]
    assert "@proxy4 accept" in console and console.strip().split("\n")[2].strip() == "drop"


@pytest.mark.skipif(not shutil.which("nft"), reason="nft not installed")
def test_rendered_rules_pass_nft_check(tmp_path):
    c = cfg(mode="proxy", proxy_ips=[{"cidr": "10.10.4.10"}], web_acl=[{"cidr": "10.10.4.0/24"}, {"cidr": "2a02:a46:1f::/48"}],
            kms_restrict=True, kms_acl=[{"cidr": "10.10.0.0/16"}])
    f = tmp_path / "r.nft"
    f.write_text(render_nft(c))
    proc = subprocess.run(["nft", "-c", "-f", str(f)], capture_output=True, text=True)
    if proc.returncode != 0 and "Operation not permitted" in proc.stderr:
        pytest.skip("no permission for nft -c")
    assert proc.returncode == 0, proc.stderr


def test_cli_writes_files(tmp_path):
    src = tmp_path / "network.json"
    src.write_text(json.dumps({"mode": "lan", "kms_restrict": True, "kms_acl": [{"cidr": "10.0.0.0/8"}]}))
    assert netrender.main(["x", str(src), str(tmp_path)]) == 0
    assert "jump kms" in (tmp_path / "kmsconsole.nft").read_text()
    assert (tmp_path / "Caddyfile").read_text() == ""
    src.write_text(json.dumps({"mode": "caddy", "domain": "x"}))
    assert netrender.main(["x", str(src), str(tmp_path)]) == 1


# ---------------------------------------------------------------- web app

def test_network_pages_render(client):
    assert client.get("/network").status_code == 200
    assert client.get("/network?tab=kms").status_code == 200


def test_add_kms_entries_and_restrict(client):
    from kmsconsole import store
    r = client.post("/network/kms", data={"restrict": "1"})
    assert store.get("net.kms_restrict") is False        # refused while the list is empty
    client.post("/network/acl", data={"scope": "kms", "action": "add", "cidr": "10.0.0.0/24", "descr": "LAN"})
    client.post("/network/acl", data={"scope": "kms", "action": "add", "cidr": "8.8.0.0/16"})
    assert [e["cidr"] for e in store.get("net.kms_acl")] == ["10.0.0.0/24"]
    client.post("/network/kms", data={"restrict": "1"})
    assert store.get("net.kms_restrict") is True
    client.post("/network/acl", data={"scope": "kms", "action": "remove", "cidr": "10.0.0.0/24"})
    assert len(store.get("net.kms_acl")) == 1             # the last entry stays while restricted
    page = client.get("/network?tab=kms").get_data(as_text=True)
    assert "10.0.0.0/24" in page and "PC-" not in page


def test_web_list_keeps_admin_and_blocks_others(client):
    from kmsconsole import network, store
    # The test client connects from 127.0.0.1; add a foreign range first
    client.post("/network/acl", data={"scope": "web", "action": "add", "cidr": "192.168.77.0/24"},
                environ_base={"REMOTE_ADDR": "10.0.0.9"})
    cidrs = [e["cidr"] for e in store.get("net.web_acl")]
    assert "192.168.77.0/24" in cidrs and "10.0.0.9" in cidrs     # own address added automatically
    network.invalidate()
    assert client.get("/network", environ_base={"REMOTE_ADDR": "10.0.0.50"}).status_code == 403
    assert client.get("/healthz", environ_base={"REMOTE_ADDR": "10.0.0.50"}).status_code == 200
    # cannot remove the entry that lets you in
    client.post("/network/acl", data={"scope": "web", "action": "remove", "cidr": "10.0.0.9"},
                environ_base={"REMOTE_ADDR": "10.0.0.9"})
    assert "10.0.0.9" in [e["cidr"] for e in store.get("net.web_acl")]
    network.reset_web_access()
    assert store.get("net.web_acl") == []


def test_forwarded_for_only_from_trusted_proxy(client):
    from kmsconsole import network, store
    store.set("net.mode", "proxy")
    store.set("net.proxy_ips", [{"cidr": "10.10.4.10"}])
    store.set("net.web_acl", [{"cidr": "84.105.22.17"}])
    network.invalidate()
    h = {"X-Forwarded-For": "84.105.22.17", "X-Forwarded-Proto": "https"}
    assert client.get("/network", headers=h, environ_base={"REMOTE_ADDR": "10.10.4.10"}).status_code == 200
    # the same header from an untrusted address is ignored
    assert client.get("/network", headers=h, environ_base={"REMOTE_ADDR": "10.10.4.99"}).status_code == 403
    # a spoofed left-most entry does not help
    h2 = {"X-Forwarded-For": "84.105.22.17, 6.6.6.6"}
    assert client.get("/network", headers=h2, environ_base={"REMOTE_ADDR": "10.10.4.10"}).status_code == 403


def test_mode_switch_to_proxy_refuses_lockout(client):
    from kmsconsole import store
    client.post("/network/mode", data={"mode": "proxy", "proxy_ips": "10.10.4.10"}, environ_base={"REMOTE_ADDR": "10.0.0.9"})
    assert store.get("net.mode") == "lan"
    client.post("/network/acl", data={"scope": "web", "action": "add", "mine": "1"}, environ_base={"REMOTE_ADDR": "10.0.0.9"})
    client.post("/network/mode", data={"mode": "proxy", "proxy_ips": "10.10.4.10"}, environ_base={"REMOTE_ADDR": "10.0.0.9"})
    assert store.get("net.mode") == "proxy"


def test_caddy_switch_keeps_console_open(client):
    from kmsconsole import store
    client.post("/network/mode", data={"mode": "caddy", "domain": "kms.example.nl", "close_console": "on"})
    assert store.get("net.mode") == "caddy" and store.get("net.close_console") is False


def test_blocked_parses_real_nft_output(monkeypatch):
    from kmsconsole import network, system
    raw = ('{"nftables": [{"metainfo": {"version": "1.1.3", "json_schema_version": 1}}, {"set": {"family": "inet", '
           '"name": "blocked4", "table": "kmsconsole", "type": "ipv4_addr", "handle": 8, "size": 65535, "flags": ["timeout", "dynamic"], '
           '"timeout": 604800, "elem": [{"elem": {"val": "10.10.4.3", "expires": 604796, "counter": {"packets": 9, "bytes": 540}}}]}}]}\n\n'
           '{"nftables": [{"metainfo": {"version": "1.1.3", "json_schema_version": 1}}, {"set": {"family": "inet", "name": "blocked6", '
           '"table": "kmsconsole", "type": "ipv6_addr", "handle": 12, "size": 65535, "flags": ["timeout", "dynamic"], "timeout": 604800}}]}\n')
    monkeypatch.setattr(system, "ctl", lambda *a, **k: raw)
    assert network.blocked() == [{"ip": "10.10.4.3", "attempts": 9, "expires": 604796}]


def test_kms_name_goes_into_scripts(client, monkeypatch):
    from kmsconsole import network, store
    monkeypatch.setattr(network, "resolve", lambda name: ["10.10.4.113"])
    monkeypatch.setattr(network.kms, "detect_ip", lambda: "10.10.4.113")
    client.post("/network/kms-name", data={"kms_name": "KMS.Example.lan."})
    assert store.get("kms.public_host") == "kms.example.lan"
    assert b"/skms kms.example.lan:1688" in client.get("/activation/download/windows-cmd").data
    page = client.get("/network").get_data(as_text=True)
    assert "Points to this container" in page
    monkeypatch.setattr(network, "resolve", lambda name: ["203.0.113.5"])
    assert "Points elsewhere" in client.get("/network").get_data(as_text=True)
    client.post("/network/kms-name", data={"kms_name": "bad name;rm"})
    assert store.get("kms.public_host") == "kms.example.lan"
