import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    data, conf = tmp_path / "data", tmp_path / "conf"
    data.mkdir(); conf.mkdir()
    from kmsconsole import config
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "CONF_DIR", conf)
    monkeypatch.setattr(config, "KMS_DB", data / "kms.db")
    monkeypatch.setattr(config, "CONSOLE_DB", data / "console.db")
    monkeypatch.setattr(config, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(config, "KMS_ENV_FILE", conf / "kms.env")
    monkeypatch.setattr(config, "SECRET_KEY_FILE", conf / "secret.key")
    monkeypatch.setattr(config, "KMS_CTL", "")
    pykms = os.environ.get("KMS_PYKMS_DIR")
    if pykms:
        monkeypatch.setattr(config, "PYKMS_DIR", Path(pykms))
    import sqlite3
    con = sqlite3.connect(data / "kms.db")
    con.execute("CREATE TABLE clients(clientMachineId TEXT, machineName TEXT, applicationId TEXT, skuId TEXT, licenseStatus TEXT, lastRequestTime INTEGER, kmsEpid TEXT, requestCount INTEGER, machineIp TEXT, PRIMARY KEY(clientMachineId, applicationId))")
    con.execute("INSERT INTO clients VALUES('a1','PC-1','Windows','Windows 10/11 Professional','Activated',strftime('%s','now'),'epid',3,'10.0.0.5')")
    con.execute("INSERT INTO clients VALUES('b2','PC-2','Office 2013 / 2016 / 2019 / LTSC 2021 / LTSC 2024','Office LTSC Professional Plus 2024','Grace Period',strftime('%s','now'),NULL,1,'10.0.0.6')")
    con.commit(); con.close()
    from kmsconsole import store
    store.init()
    (conf / "kms.env").write_text("KMS_PORT=1688\n")
    yield tmp_path


@pytest.fixture()
def client(env):
    from kmsconsole import create_app, network
    network.invalidate()
    app = create_app(start_scheduler=False, testing=True)
    c = app.test_client()
    from kmsconsole import bootstrap
    bootstrap.set_password("admin", "secretpass1", must_change=False)
    r = c.post("/login", data={"username": "admin", "password": "secretpass1"})
    assert r.status_code == 302
    return c
