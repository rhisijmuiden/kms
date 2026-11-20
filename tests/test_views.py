import json
import re
from pathlib import Path

PAGES = ["/", "/clients", "/clients?f=office&q=pc", "/activation", "/activation?tab=office&fmt=ps1", "/activation?tab=gpo",
         "/activation?tab=gpo&fmt=ps1", "/activation?tab=dns", "/activation?tab=eval",
         "/activation?tab=eval&fmt=cmd&target=ServerDatacenter", "/activation?tab=odt",
         "/activation?tab=odt&fmt=xml&set=1&edition=ProPlus2019Volume&app=Word&visio=1", "/products", "/server", "/mail", "/backups", "/users"]


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


def test_eval_download_keeps_target(client):
    r = client.get("/activation/download/eval-cmd?target=ServerDatacenter")
    assert r.status_code == 200
    assert b'set "TARGET=ServerDatacenter"' in r.data
    page = client.get("/activation?tab=eval").get_data(as_text=True)
    assert "convert-eval-and-activate.ps1" in page and "DISM" in page


def test_server_product_points_to_eval_tab(client):
    from kmsconsole import kms
    sku = next(s for g in kms.catalog() for s in g["skus"] if s["name"] == "Windows Server 2025 Standard") if kms.catalog() else None
    if sku is None:
        return
    page = client.get(f"/activation?tab=windows&sku={sku['id']}").get_data(as_text=True)
    assert "tab=eval" in page and "target=ServerStandard" in page
    page = client.get("/activation?tab=eval").get_data(as_text=True)
    assert "TVRH6-WHNXV-R9WG3-9XRFY-MY832" in page and "D764K-2NDRG-47T6Q-P8T8W-YP6DF" in page and "26100" in page


def test_odt_tab_and_download(client):
    page = client.get("/activation?tab=odt").get_data(as_text=True)
    assert "install-office.ps1" in page and "configuration.xml" in page and "Office LTSC Professional Plus 2024" in page
    r = client.get("/activation/download/odt-xml?set=1&edition=Standard2024Volume&lang=nl-nl&app=Word&app=Excel&project=1")
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and "configuration.xml" in r.headers["Content-Disposition"]
    assert 'Product ID="Standard2024Volume"' in body and 'Product ID="ProjectPro2024Volume"' in body
    assert '<ExcludeApp ID="Outlook" />' in body and '<Language ID="nl-nl" />' in body
    r = client.get("/activation/download/odt-ps1?set=1&edition=ProPlus2021Volume&arch=32")
    body = r.get_data(as_text=True)
    assert '$Edition  = "ProPlus2021Volume"' in body and '$Arch     = "32"' in body and "$NoRemove = $true" in body
