import json
import re
from pathlib import Path

PAGES = ["/", "/clients", "/clients?f=office&q=pc", "/activation", "/activation?tab=office&fmt=ps1", "/activation?tab=gpo",
         "/activation?tab=gpo&fmt=ps1", "/activation?tab=dns", "/products", "/server", "/mail", "/backups", "/users"]


def test_pages_render(client):
    for page in PAGES:
        r = client.get(page)
        assert r.status_code == 200, page


def test_pages_render_dutch(client):
    client.post("/lang/nl")
    r = client.get("/")
    assert "Geactiveerde clients" in r.get_data(as_text=True)


def test_login_required(env):
    from kmsconsole import create_app
    app = create_app(start_scheduler=False, testing=True)
    c = app.test_client()
    assert c.get("/").headers["Location"].endswith("/setup")


def test_csv_export(client):
    r = client.get("/clients/export.csv")
    assert r.status_code == 200 and b"PC-1" in r.data


def test_script_download(client):
    r = client.get("/activation/download/windows-cmd")
    assert r.status_code == 200 and b"slmgr.vbs" in r.data
    assert client.get("/activation/download/evil").status_code in (302, 404)


def test_server_settings_validation(client):
    from kmsconsole import store
    client.post("/server", data={"public_host": "kms.lan; rm -rf /", "listen": "0.0.0.0", "port": "1688", "lcid": "1033",
                                 "activation_interval": "120", "renewal_interval": "10080", "hwid": "RANDOM", "loglevel": "INFO"})
    assert store.get("kms.public_host") == ""
    client.post("/server", data={"public_host": "kms.lan", "listen": "0.0.0.0", "port": "1689", "lcid": "1043",
                                 "activation_interval": "120", "renewal_interval": "10080", "hwid": "RANDOM", "loglevel": "INFO"})
    assert store.get("kms.public_host") == "kms.lan" and store.get("kms.port") == 1689


def test_backup_buttons(client):
    r = client.post("/backups/run", data={"kind": "full"})
    assert r.status_code == 302
    page = client.get("/backups").get_data(as_text=True)
    assert "kms-backup-" in page


def test_dutch_translation_complete():
    root = Path(__file__).resolve().parents[1] / "app" / "kmsconsole"
    nl = json.loads((root / "translations" / "nl.json").read_text())
    missing = set()
    for tpl in (root / "templates").glob("*.html"):
        for key in re.findall(r"\bt\('((?:[^'\\]|\\.)+)'", tpl.read_text()):
            if key not in nl:
                missing.add(key)
    views = (root / "views.py").read_text()
    for key in re.findall(r'_t\("((?:[^"\\]|\\.)+)"', views):
        if key not in nl:
            missing.add(key)
    assert not missing, sorted(missing)


def test_dns_domain_from_ip_and_hostname(client):
    from kmsconsole import store
    store.set("kms.public_host", "10.10.4.113")
    page = client.get("/activation?tab=dns").get_data(as_text=True)
    assert "_vlmcs._tcp.lan" in page and "kms.lan" in page and "UniFi" in page
    store.set("kms.public_host", "kms.home.lan")
    page = client.get("/activation?tab=dns").get_data(as_text=True)
    assert "_vlmcs._tcp.home.lan" in page
