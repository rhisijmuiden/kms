import sqlite3

import pytest

from kmsconsole import backup, config, store


def _count():
    return sqlite3.connect(config.KMS_DB).execute("SELECT COUNT(*) FROM clients").fetchone()[0]


def test_full_then_incremental_and_restore(env):
    full = backup.run("full", upload=False)
    assert full["kind"] == "full"
    assert set(full["changed"]) >= {"data/kms.db", "conf/kms.env", "conf/secret.key"}

    inc0 = backup.run("incremental", upload=False)
    assert "conf/kms.env" not in inc0["changed"]  # unchanged files are skipped

    con = sqlite3.connect(config.KMS_DB)
    con.execute("INSERT INTO clients VALUES('c3','PC-3','Windows','Windows 11 Enterprise','Activated',0,NULL,1,'')")
    con.commit(); con.close()
    inc = backup.run("incremental", upload=False)
    assert "data/kms.db" in inc["changed"]
    assert _count() == 3

    # break the data, then restore to the first full backup (2 clients)
    con = sqlite3.connect(config.KMS_DB); con.execute("DELETE FROM clients"); con.commit(); con.close()
    backup.restore(full["name"], "local")
    assert _count() == 2

    # restore to the latest incremental (3 clients) rebuilds the chain
    backup.restore(inc["name"], "local")
    assert _count() == 3


def test_encrypted_backup(env):
    store.set("backup.passphrase", "correct horse")
    res = backup.run("full", upload=False)
    assert res["name"].endswith(".enc")
    raw = (config.BACKUP_DIR / res["name"]).read_bytes()
    assert raw.startswith(backup.MAGIC)
    store.set("backup.passphrase", "")
    with pytest.raises(backup.BackupError):
        backup.restore(res["name"], "local")


def test_incomplete_chain_rejected(env):
    backup.run("full", upload=False)
    inc = backup.run("incremental", upload=False)
    for b in backup.local_backups():
        if b["kind"] == "full":
            (config.BACKUP_DIR / b["name"]).unlink()
    with pytest.raises(backup.BackupError):
        backup.restore(inc["name"], "local")


def test_prune_keeps_chains(env):
    store.set("backup.keep_chains", 2)
    for _ in range(4):
        backup.run("full", upload=False)
    chains = {b["chain"] for b in backup.local_backups()}
    assert len(chains) == 2


def test_secrets_encrypted_at_rest(env):
    store.set("mail.password", "hunter2hunter2")
    raw = sqlite3.connect(config.CONSOLE_DB).execute("SELECT value FROM settings WHERE key='mail.password'").fetchone()[0]
    assert "hunter2" not in raw
    assert store.get("mail.password") == "hunter2hunter2"
