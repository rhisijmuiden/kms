"""Access to the py-kms activation database and product catalog."""
import csv
import io
import socket
import sqlite3
import time
import xml.etree.ElementTree as ET
from functools import lru_cache

from . import config, store


def _connect_ro():
    if not config.KMS_DB.exists():
        return None
    con = sqlite3.connect(f"file:{config.KMS_DB}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def clients():
    con = _connect_ro()
    if con is None:
        return []
    try:
        rows = [dict(r) for r in con.execute("SELECT * FROM clients ORDER BY lastRequestTime DESC")]
    except sqlite3.Error:
        rows = []
    finally:
        con.close()
    for r in rows:
        r.setdefault("machineIp", "")
        r["kind"] = "office" if str(r.get("applicationId", "")).lower().startswith("office") else "windows"
        status = str(r.get("licenseStatus", "")).lower()
        r["state"] = "ok" if status == "activated" else ("pending" if status == "grace period" else "attention")
        r["ok"] = r["state"] != "attention"
    return rows


def delete_client(client_id, app_id):
    if not config.KMS_DB.exists():
        return
    con = sqlite3.connect(config.KMS_DB, timeout=5)
    try:
        con.execute("DELETE FROM clients WHERE clientMachineId=? AND applicationId=?", (client_id, app_id))
        con.commit()
    finally:
        con.close()


def stats(rows=None):
    rows = clients() if rows is None else rows
    now = time.time()
    day = now - 86400
    week = now - 7 * 86400
    return {
        "total": len(rows),
        "windows": sum(1 for r in rows if r["kind"] == "windows"),
        "office": sum(1 for r in rows if r["kind"] == "office"),
        "attention": sum(1 for r in rows if not r["ok"]),
        "seen_today": sum(1 for r in rows if (r.get("lastRequestTime") or 0) >= day),
        "new_week": sum(1 for r in rows if (r.get("lastRequestTime") or 0) >= week and (r.get("requestCount") or 0) <= 1),
        "products": len({r.get("skuId") for r in rows}),
    }


def clients_csv(rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["machineName", "machineIp", "applicationId", "skuId", "licenseStatus", "lastRequestTime", "requestCount", "clientMachineId", "kmsEpid"])
    for r in rows:
        w.writerow([r.get("machineName"), r.get("machineIp"), r.get("applicationId"), r.get("skuId"), r.get("licenseStatus"),
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r.get("lastRequestTime") or 0)),
                    r.get("requestCount"), r.get("clientMachineId"), r.get("kmsEpid")])
    return buf.getvalue()


@lru_cache(maxsize=1)
def catalog():
    """Products with a public GVLK, grouped. Read from py-kms' KmsDataBase.xml."""
    path = config.PYKMS_DIR / "KmsDataBase.xml"
    groups = []
    if not path.exists():
        return groups
    root = ET.parse(path).getroot()
    for app in root.iter("AppItem"):
        app_name = app.get("DisplayName", "")
        kind = "office" if app_name.lower().startswith("office") else "windows"
        for item in app.iter("KmsItem"):
            if item.get("IsPreview") == "true":
                continue
            skus = [{"name": s.get("DisplayName"), "gvlk": s.get("Gvlk"), "id": s.get("Id")}
                    for s in item.iter("SkuItem") if s.get("Gvlk") and "[" not in s.get("DisplayName", "")]
            if not skus:
                continue
            groups.append({"name": item.get("DisplayName"), "kind": kind, "retail": item.get("IsRetail") == "true",
                           "skus": sorted(skus, key=lambda s: s["name"])})
    return groups


def find_sku(sku_id):
    for g in catalog():
        for s in g["skus"]:
            if s["id"] == sku_id:
                return dict(s, kind=g["kind"], group=g["name"])
    return None


def office_version(group_name):
    """Office folder name used by ospp.vbs for a catalog group."""
    if "2010" in group_name:
        return "Office14"
    if "2013" in group_name:
        return "Office15"
    return "Office16"


def detect_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # TEST-NET, no traffic is sent
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def public_host():
    return (store.get("kms.public_host") or "").strip() or detect_ip()


def server_alive(timeout=1.0):
    port = int(store.get("kms.port"))
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False
