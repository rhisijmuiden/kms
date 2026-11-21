"""Administrator created on the server, forced password change, first steps checklist."""
import re

from kmsconsole import bootstrap, store


def _app():
    from kmsconsole import create_app, network
    network.invalidate()
    return create_app(start_scheduler=False, testing=True).test_client()


def test_generated_password_format():
    pw = bootstrap.generate_password()
    assert re.fullmatch(r"[A-Za-z2-9]{4}(-[A-Za-z2-9]{4}){3}", pw)
    assert not set(pw) & set("0O1lI\\")


def test_setup_page_cannot_create_accounts(env):
    c = _app()
    assert c.get("/").headers["Location"].endswith("/setup")
    assert b"kms-ctl reset-password admin" in c.get("/setup").data
    assert c.post("/setup", data={"username": "evil", "password": "x" * 12, "password2": "x" * 12}).status_code == 405
    assert bootstrap.user_count() == 0


def test_init_admin_only_once(env):
    pw = bootstrap.init_admin()
    assert pw and bootstrap.user_count() == 1
    assert bootstrap.init_admin() is None
    assert bootstrap.main(["x", "init-admin"]) == 0 and bootstrap.user_count() == 1


def test_forced_password_change(env):
    pw = bootstrap.init_admin()
    c = _app()
    r = c.post("/login", data={"username": "admin", "password": pw})
    assert r.status_code == 302
    assert c.get("/").headers["Location"].endswith("/password")
    assert c.get("/clients").headers["Location"].endswith("/password")
    assert c.get("/api/info").status_code == 403
    # same password is refused, short one too
    assert b"differs" in c.post("/password", data={"password": pw, "password2": pw}, follow_redirects=True).data
    c.post("/password", data={"password": "short", "password2": "short"})
    assert c.get("/").status_code == 302
    r = c.post("/password", data={"password": "mynewpassword", "password2": "mynewpassword"})
    assert r.headers["Location"].endswith("/")
    assert c.get("/").status_code == 200
    with store.connect() as con:
        assert con.execute("SELECT must_change FROM users").fetchone()[0] == 0


def test_voluntary_change_needs_current_password(client):
    r = client.post("/password", data={"current": "wrong", "password": "anotherpass1", "password2": "anotherpass1"},
                    follow_redirects=True)
    assert b"current password" in r.data
    r = client.post("/password", data={"current": "secretpass1", "password": "anotherpass1", "password2": "anotherpass1"})
    assert r.status_code == 302


def test_reset_password_forces_change(client):
    bootstrap.reset_password("admin")
    assert client.get("/").headers["Location"].endswith("/password")


def test_onboarding_checklist(client):
    page = client.get("/").data
    assert b"First steps" in page and b"1 of 5 done" in page
    store.set("kms.public_host", "kms.lan")
    assert b"2 of 5 done" in client.get("/").data
    client.post("/onboarding/dismiss")
    assert b"First steps" not in client.get("/").data


def test_onboarding_dutch(client):
    client.post("/lang/nl")
    page = client.get("/").data.decode()
    assert "Eerste stappen" in page and "First steps" not in page


def test_migration_adds_must_change(env):
    import sqlite3
    from kmsconsole import config
    con = sqlite3.connect(config.CONSOLE_DB)
    con.execute("DROP TABLE users")
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, email TEXT DEFAULT '', lang TEXT DEFAULT 'en', created INTEGER NOT NULL)")
    con.execute("INSERT INTO users(username, password_hash, created) VALUES('old', 'x', 1)")
    con.commit(); con.close()
    store.init()
    with store.connect() as con:
        assert con.execute("SELECT must_change FROM users").fetchone()[0] == 0
