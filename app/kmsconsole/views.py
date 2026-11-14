"""HTTP routes."""
import functools
import re
import threading
import time
from pathlib import Path

from flask import (Blueprint, Response, abort, flash, g, jsonify, make_response, redirect, render_template, request,
                   send_file, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from . import backup, config, i18n, kms, mailer, network, remote, scheduler, scripts, store, system
from .netrender import NetError

bp = Blueprint("main", __name__)
_failures = {}
_HOST_RE = re.compile(r"^[A-Za-z0-9.\-:]{0,253}$")
_EMAIL_LIST_RE = re.compile(r"^[^\s<>]*$")


def _t(text, **kw):
    return i18n.translate(g.lang, text, **kw)


# ---------------------------------------------------------------- auth

def _user_count():
    with store.connect() as con:
        return con.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def _current_user():
    uid = session.get("uid")
    if not uid:
        return None
    with store.connect() as con:
        row = con.execute("SELECT id, username, email, lang FROM users WHERE id=?", (uid,)).fetchone()
    return dict(row) if row else None


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if _user_count() == 0:
            return redirect(url_for("main.setup"))
        g.user = _current_user()
        if not g.user:
            if request.path.startswith("/api/"):
                return jsonify(error="unauthorized"), 401
            return redirect(url_for("main.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def _password_ok(pw):
    return len(pw or "") >= 10


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    if _user_count() > 0:
        return redirect(url_for("main.login"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        pw = request.form.get("password") or ""
        if not re.match(r"^[A-Za-z0-9._@-]{3,64}$", username):
            flash(_t("Use 3 to 64 letters, digits or . _ @ - for the username."), "error")
        elif not _password_ok(pw):
            flash(_t("Use a password of at least 10 characters."), "error")
        elif pw != request.form.get("password2"):
            flash(_t("The passwords do not match."), "error")
        else:
            with store.connect() as con:
                con.execute("INSERT INTO users(username, password_hash, email, lang, created) VALUES(?,?,?,?,?)",
                            (username, generate_password_hash(pw), request.form.get("email", ""), g.lang, int(time.time())))
            system_env_init()
            flash(_t("Welcome! Your administrator account is ready."), "ok")
            return _do_login(username)
    return render_template("setup.html")


def system_env_init():
    try:
        if not config.KMS_ENV_FILE.exists():
            system.write_env()
    except OSError:
        pass


def _do_login(username):
    with store.connect() as con:
        row = con.execute("SELECT id, lang FROM users WHERE username=?", (username,)).fetchone()
    session.clear()
    session.permanent = True
    session["uid"] = row["id"]
    session["lang"] = row["lang"] or g.lang
    return redirect(url_for("main.dashboard"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if _user_count() == 0:
        return redirect(url_for("main.setup"))
    if request.method == "POST":
        ip = request.remote_addr or "?"
        fails = [t for t in _failures.get(ip, []) if t > time.time() - 900]
        if len(fails) >= 8:
            flash(_t("Too many attempts. Try again in 15 minutes."), "error")
            return render_template("login.html"), 429
        username = (request.form.get("username") or "").strip()
        with store.connect() as con:
            row = con.execute("SELECT password_hash FROM users WHERE username=?", (username,)).fetchone()
        if row and check_password_hash(row["password_hash"], request.form.get("password") or ""):
            _failures.pop(ip, None)
            resp = _do_login(username)
            nxt = request.args.get("next") or ""
            if nxt.startswith("/") and not nxt.startswith("//"):
                resp = redirect(nxt)
            return resp
        fails.append(time.time())
        _failures[ip] = fails
        time.sleep(0.5)
        flash(_t("Wrong username or password."), "error")
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("main.login"))


@bp.route("/lang/<code>", methods=["POST"])
def set_lang(code):
    if code in i18n.LANGUAGES:
        session["lang"] = code
        uid = session.get("uid")
        if uid:
            with store.connect() as con:
                con.execute("UPDATE users SET lang=? WHERE id=?", (code, uid))
    resp = make_response(redirect(request.referrer or url_for("main.dashboard")))
    resp.set_cookie("lang", code, max_age=365 * 86400, samesite="Lax", httponly=True)
    return resp


# ---------------------------------------------------------------- pages

@bp.route("/")
@login_required
def dashboard():
    rows = kms.clients()
    return render_template("dashboard.html", active="dashboard", stats=kms.stats(rows), recent=rows[:6],
                           host=kms.public_host(), port=store.get("kms.port"), alive=kms.server_alive(),
                           last_backup=store.get("backup.last") or {}, mail=store.section("mail"),
                           backup_settings=store.section("backup"), events=store.events(8))


def _filtered_clients():
    rows = kms.clients()
    q = (request.args.get("q") or "").strip().lower()
    f = request.args.get("f", "all")
    if q:
        rows = [r for r in rows if q in " ".join(str(r.get(k, "")) for k in ("machineName", "machineIp", "skuId", "applicationId", "clientMachineId")).lower()]
    if f in ("windows", "office"):
        rows = [r for r in rows if r["kind"] == f]
    elif f == "attention":
        rows = [r for r in rows if r["state"] != "ok"]
    return rows, q, f


@bp.route("/clients")
@login_required
def clients():
    rows, q, f = _filtered_clients()
    page = max(1, request.args.get("page", 1, type=int))
    per = 25
    return render_template("clients.html", active="clients", rows=rows[(page - 1) * per: page * per], total=len(rows),
                           page=page, pages=max(1, (len(rows) + per - 1) // per), q=q, f=f, stats=kms.stats())


@bp.route("/clients/export.csv")
@login_required
def clients_csv():
    rows, _, _ = _filtered_clients()
    return Response(kms.clients_csv(rows), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=kms-clients-{time.strftime('%Y%m%d')}.csv"})


@bp.route("/clients/delete", methods=["POST"])
@login_required
def clients_delete():
    kms.delete_client(request.form.get("cmid", ""), request.form.get("app", ""))
    flash(_t("Client removed from the list."), "ok")
    return redirect(url_for("main.clients"))


def _dns_domain():
    """DNS suffix of the configured hostname (kms.example.lan -> example.lan); 'lan' when an IP is used."""
    host = (store.get("kms.public_host") or "").strip()
    if not host or re.match(r"^[0-9.]+$", host) or ":" in host:
        return "lan"
    return host.partition(".")[2] or "lan"


def _eval_target():
    target = request.args.get("target", "auto")
    return target if target in scripts.EVAL_TARGETS else "auto"


def _script_args(kind, sku_id):
    sku = kms.find_sku(sku_id) if sku_id and not kind.startswith("eval-") else None
    domain = _dns_domain()
    return {
        "host": kms.public_host(),
        "port": store.get("kms.port"),
        "key": sku["gvlk"] if sku else "",
        "product": sku["name"] if sku else "",
        "office_dir": kms.office_version(sku["group"]) if sku and sku["kind"] == "office" else "Office16",
        "domain": domain,
        "target": _eval_target(),
    }


@bp.route("/activation")
@login_required
def activation():
    tab = request.args.get("tab", "windows")
    if tab not in ("windows", "eval", "office", "gpo", "dns"):
        tab = "windows"
    catalog = kms.catalog()
    kind_for_tab = "office" if tab == "office" else "windows"
    sku_id = request.args.get("sku", "")
    groups = [c for c in catalog if c["kind"] == kind_for_tab and not c["retail"]]
    if not sku_id and groups and tab in ("windows", "office"):
        preferred = "Windows 10/11 Professional" if tab == "windows" else "Office LTSC Professional Plus 2024"
        for grp in groups:
            for s in grp["skus"]:
                if s["name"] == preferred:
                    sku_id = s["id"]
    fmt = request.args.get("fmt", "ps1" if tab == "eval" else "cmd")
    if tab == "eval":
        sku_id = ""
        kind = f"eval-{'ps1' if fmt == 'ps1' else 'cmd'}"
    elif tab in ("windows", "office"):
        kind = f"{tab}-{'ps1' if fmt == 'ps1' else 'cmd'}"
    else:
        kind = {"gpo": "reg" if fmt != "ps1" else "gpo", "dns": "dns"}[tab]
    args = _script_args(kind, sku_id)
    code = scripts.generate(kind, **args)
    return render_template("activation.html", active="activation", tab=tab, fmt=fmt, kind=kind, code=code,
                           filename=scripts.FILENAMES[kind], groups=groups, sku_id=sku_id, host=args["host"],
                           port=args["port"], key=args["key"], product=args["product"], domain=args["domain"],
                           target=args["target"], eval_targets=scripts.EVAL_TARGETS,
                           server_versions=[v[0] for v in scripts.SERVER_GVLK.values()],
                           host_is_ip=bool(re.match(r"^[0-9.]+$", args["host"]) or ":" in args["host"]))


@bp.route("/activation/download/<kind>")
@login_required
def activation_download(kind):
    if kind not in scripts.TYPES:
        abort(404)
    code = scripts.generate(kind, **_script_args(kind, request.args.get("sku", "")))
    return Response(code, mimetype="application/octet-stream",
                    headers={"Content-Disposition": f"attachment; filename={scripts.FILENAMES[kind]}"})


@bp.route("/products")
@login_required
def products():
    q = (request.args.get("q") or "").strip().lower()
    groups = []
    for grp in kms.catalog():
        skus = [s for s in grp["skus"] if not q or q in s["name"].lower() or q in grp["name"].lower()]
        if skus:
            groups.append(dict(grp, skus=skus))
    return render_template("products.html", active="products", groups=groups, q=q)


# ---------------------------------------------------------------- settings

def _int(name, lo, hi):
    try:
        v = int(request.form.get(name, ""))
    except ValueError as exc:
        raise ValueError(name) from exc
    if not lo <= v <= hi:
        raise ValueError(name)
    return v


@bp.route("/server", methods=["GET", "POST"])
@login_required
def server():
    if request.method == "POST":
        try:
            host = (request.form.get("public_host") or "").strip()
            listen = (request.form.get("listen") or "0.0.0.0").strip()
            hwid = (request.form.get("hwid") or "RANDOM").strip().upper()
            if not _HOST_RE.match(host) or not re.match(r"^[0-9a-fA-F.:]{2,45}$", listen):
                raise ValueError("host")
            if hwid != "RANDOM" and not re.match(r"^[0-9A-F]{16}$", hwid):
                raise ValueError("hwid")
            values = {
                "kms.public_host": host, "kms.listen": listen, "kms.port": _int("port", 1, 65535),
                "kms.lcid": _int("lcid", 1, 99999), "kms.activation_interval": _int("activation_interval", 1, 10080 * 4),
                "kms.renewal_interval": _int("renewal_interval", 1, 259200), "kms.hwid": hwid,
                "kms.loglevel": request.form.get("loglevel") if request.form.get("loglevel") in ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "MININFO") else "INFO",
            }
        except ValueError:
            flash(_t("Some values are not valid. Check the fields and try again."), "error")
            return redirect(url_for("main.server"))
        for k, v in values.items():
            store.set(k, v)
        try:
            system.restart_server()
            flash(_t("Settings saved and KMS server restarted."), "ok")
        except (system.SystemError_, OSError) as exc:
            flash(_t("Settings saved, but the server could not be restarted: {err}", err=exc), "error")
        return redirect(url_for("main.server"))
    return render_template("server.html", active="server", s=store.section("kms"), detected=kms.detect_ip(),
                           status=system.status(), alive=kms.server_alive(),
                           update_branch_production=store.get("update.branch_production"),
                           update_branch_test=store.get("update.branch_test"), update_auto=store.get("update.auto_check"))


@bp.route("/server/restart", methods=["POST"])
@login_required
def server_restart():
    try:
        system.restart_server()
        flash(_t("KMS server restarted."), "ok")
    except (system.SystemError_, OSError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("main.server"))


@bp.route("/server/logs")
@login_required
def server_logs():
    return Response(system.logs(300), mimetype="text/plain")


def _save_secret(key, field):
    """Secrets are only replaced when the user typed something; a blank field keeps the stored value."""
    value = request.form.get(field, "")
    if request.form.get(f"{field}_clear"):
        store.set(key, "")
    elif value:
        store.set(key, value)


@bp.route("/mail", methods=["GET", "POST"])
@login_required
def mail():
    if request.method == "POST":
        try:
            security = request.form.get("security")
            if security not in ("none", "starttls", "ssl"):
                raise ValueError("security")
            recips = request.form.get("recipients", "").strip()
            if not _EMAIL_LIST_RE.match(recips.replace(" ", "")):
                raise ValueError("recipients")
            store.set("mail.port", _int("port", 1, 65535))
        except ValueError:
            flash(_t("Some values are not valid. Check the fields and try again."), "error")
            return redirect(url_for("main.mail"))
        store.set("mail.enabled", bool(request.form.get("enabled")))
        store.set("mail.host", request.form.get("host", "").strip())
        store.set("mail.security", security)
        store.set("mail.verify_tls", bool(request.form.get("verify_tls")))
        store.set("mail.username", request.form.get("username", "").strip())
        _save_secret("mail.password", "password")
        for f in ("from_name", "from_address", "reply_to"):
            store.set(f"mail.{f}", request.form.get(f, "").strip())
        store.set("mail.recipients", recips)
        for f in ("notify_backup_ok", "notify_backup_fail", "notify_update", "notify_blocked"):
            store.set(f"mail.{f}", bool(request.form.get(f)))
        flash(_t("Mail settings saved."), "ok")
        if request.form.get("action") == "test":
            return mail_test()
        return redirect(url_for("main.mail"))
    return render_template("mail.html", active="mail", s=store.section("mail"))


@bp.route("/mail/test", methods=["POST"])
@login_required
def mail_test():
    try:
        mailer.send(_t("KMS Console test mail"), [_t("This is a test message from KMS Console."),
                                                   _t("If you can read this, your mail server settings work.")])
        flash(_t("Test mail sent. Check the inbox of the recipients."), "ok")
    except mailer.MailError as exc:
        flash(_t("Sending failed: {err}", err=exc), "error")
    return redirect(url_for("main.mail"))


# ---------------------------------------------------------------- backups

@bp.route("/backups", methods=["GET", "POST"])
@login_required
def backups():
    if request.method == "POST":
        try:
            proto = request.form.get("protocol")
            if proto not in remote.PROTOCOLS:
                raise ValueError("protocol")
            for key, allowed in (("full_schedule", ("off", "daily", "weekly", "monthly")), ("inc_schedule", ("off", "hourly", "daily"))):
                if request.form.get(key) not in allowed:
                    raise ValueError(key)
            for key in ("full_time", "inc_time"):
                if not re.match(r"^\d{2}:\d{2}$", request.form.get(key, "")):
                    raise ValueError(key)
            port = _int("port", 1, 65535)
            weekday = _int("full_weekday", 0, 6)
            keep = _int("keep_chains", 1, 100)
        except ValueError:
            flash(_t("Some values are not valid. Check the fields and try again."), "error")
            return redirect(url_for("main.backups"))
        old_host = (store.get("backup.host"), store.get("backup.port"))
        store.set("backup.remote_enabled", bool(request.form.get("remote_enabled")))
        store.set("backup.protocol", proto)
        store.set("backup.host", request.form.get("host", "").strip())
        store.set("backup.port", port)
        store.set("backup.username", request.form.get("username", "").strip())
        store.set("backup.path", request.form.get("path", "").strip() or "/")
        store.set("backup.verify_tls", bool(request.form.get("verify_tls")))
        _save_secret("backup.password", "password")
        _save_secret("backup.ssh_key", "ssh_key")
        _save_secret("backup.passphrase", "passphrase")
        if old_host != (store.get("backup.host"), port) or request.form.get("reset_fingerprint"):
            store.set("backup.host_fingerprint", "")
        for key in ("full_schedule", "full_time", "inc_schedule", "inc_time"):
            store.set(f"backup.{key}", request.form.get(key))
        store.set("backup.full_weekday", weekday)
        store.set("backup.keep_chains", keep)
        scheduler.reload()
        flash(_t("Backup settings saved."), "ok")
        return redirect(url_for("main.backups"))
    return render_template("backups.html", active="backups", s=store.section("backup"), local=backup.local_backups(),
                           last=store.get("backup.last") or {}, jobs=scheduler.jobs())


def _background(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True).start()


@bp.route("/backups/run", methods=["POST"])
@login_required
def backups_run():
    kind = "full" if request.form.get("kind") == "full" else "incremental"
    result = backup.run_safe(kind, "manual")
    if result.get("error"):
        flash(_t("Backup problem: {err}", err=result["error"]), "error")
    else:
        flash(_t("Backup {name} created.", name=result["name"]), "ok")
    return redirect(url_for("main.backups"))


@bp.route("/backups/test", methods=["POST"])
@login_required
def backups_test():
    try:
        loc = remote.location(store.section("backup"))
        info = loc.test()
        backup._remember_fingerprint(loc)  # noqa: SLF001
        return jsonify(ok=True, message=_t("Connection works and the folder is writable.") + (f" {info}" if info != "OK" else ""))
    except remote.RemoteError as exc:
        return jsonify(ok=False, message=str(exc))


@bp.route("/backups/remote")
@login_required
def backups_remote():
    try:
        items = backup.remote_backups()
        return jsonify(ok=True, items=[{**b, "created_text": time.strftime("%Y-%m-%d %H:%M", time.localtime(b["created"]))} for b in items])
    except (remote.RemoteError, OSError) as exc:
        return jsonify(ok=False, message=str(exc))


@bp.route("/backups/restore", methods=["POST"])
@login_required
def backups_restore():
    name = request.form.get("name", "")
    source = "remote" if request.form.get("source") == "remote" else "local"
    if request.form.get("confirm") != "RESTORE":
        flash(_t("Type RESTORE to confirm."), "error")
        return redirect(url_for("main.backups"))
    try:
        result = backup.restore(name, source)
        try:
            system.restart_server()
        except (system.SystemError_, OSError):
            pass
        scheduler.reload()
        flash(_t("Restore complete: {n} archive(s) applied. A safety backup of the old state was made first.", n=result["archives"]), "ok")
    except (backup.BackupError, remote.RemoteError, OSError) as exc:
        flash(_t("Restore failed: {err}", err=exc), "error")
    return redirect(url_for("main.backups"))


@bp.route("/backups/download/<name>")
@login_required
def backups_download(name):
    if not backup.parse_name(name):
        abort(404)
    path = config.BACKUP_DIR / name
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=name)


@bp.route("/backups/upload", methods=["POST"])
@login_required
def backups_upload():
    files = request.files.getlist("files")
    added = 0
    for f in files:
        name = secure_filename(f.filename or "")
        if backup.parse_name(name):
            f.save(config.BACKUP_DIR / name)
            added += 1
    flash(_t("{n} backup file(s) added. You can restore them from the list.", n=added), "ok" if added else "error")
    return redirect(url_for("main.backups"))


@bp.route("/backups/delete", methods=["POST"])
@login_required
def backups_delete():
    name = request.form.get("name", "")
    if backup.parse_name(name):
        (config.BACKUP_DIR / name).unlink(missing_ok=True)
        flash(_t("Local backup removed."), "ok")
    return redirect(url_for("main.backups"))


# ---------------------------------------------------------------- users

@bp.route("/users", methods=["GET", "POST"])
@login_required
def users():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            username = (request.form.get("username") or "").strip()
            pw = request.form.get("password") or ""
            if not re.match(r"^[A-Za-z0-9._@-]{3,64}$", username) or not _password_ok(pw):
                flash(_t("Use a valid username and a password of at least 10 characters."), "error")
            else:
                try:
                    with store.connect() as con:
                        con.execute("INSERT INTO users(username, password_hash, email, lang, created) VALUES(?,?,?,?,?)",
                                    (username, generate_password_hash(pw), request.form.get("email", ""), "en", int(time.time())))
                    flash(_t("User added."), "ok")
                except Exception:  # noqa: BLE001 - unique constraint
                    flash(_t("This username already exists."), "error")
        elif action == "delete":
            uid = request.form.get("id", type=int)
            if uid == g.user["id"]:
                flash(_t("You cannot remove yourself."), "error")
            else:
                with store.connect() as con:
                    con.execute("DELETE FROM users WHERE id=?", (uid,))
                flash(_t("User removed."), "ok")
        elif action == "password":
            pw = request.form.get("password") or ""
            if not _password_ok(pw):
                flash(_t("Use a password of at least 10 characters."), "error")
            else:
                with store.connect() as con:
                    con.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(pw), g.user["id"]))
                flash(_t("Your password has been changed."), "ok")
        return redirect(url_for("main.users"))
    with store.connect() as con:
        rows = [dict(r) for r in con.execute("SELECT id, username, email, created FROM users ORDER BY username")]
    return render_template("users.html", active="users", rows=rows)


# ---------------------------------------------------------------- info / updates

@bp.route("/api/info")
@login_required
def api_info():
    return jsonify(version=config.version(), channel=store.get("update.channel"),
                   branches={"production": store.get("update.branch_production"), "test": store.get("update.branch_test")},
                   last=store.get("update.last_result") or {}, status=system.status())


@bp.route("/api/update/check", methods=["POST"])
@login_required
def api_update_check():
    channel = "test" if request.form.get("channel") == "test" else "production"
    try:
        return jsonify(ok=True, info=system.check_update(channel))
    except (system.SystemError_, ValueError) as exc:
        return jsonify(ok=False, message=str(exc))


@bp.route("/api/update/start", methods=["POST"])
@login_required
def api_update_start():
    channel = "test" if request.form.get("channel") == "test" else "production"
    try:
        backup.run("full", reason="pre-update", upload=True)
    except Exception as exc:  # noqa: BLE001
        if not request.form.get("force"):
            return jsonify(ok=False, message=_t("Backup before update failed: {err}", err=exc))
    try:
        system.start_update(channel)
        return jsonify(ok=True, message=_t("Update started. The console restarts in a moment."))
    except system.SystemError_ as exc:
        return jsonify(ok=False, message=str(exc))


@bp.route("/api/update/log")
@login_required
def api_update_log():
    return Response(system.update_log(), mimetype="text/plain")


@bp.route("/settings/update", methods=["POST"])
@login_required
def settings_update():
    prod = request.form.get("branch_production", "").strip()
    test = request.form.get("branch_test", "").strip()
    if not system.BRANCH_RE.match(prod) or not system.BRANCH_RE.match(test):
        flash(_t("Invalid branch name."), "error")
    else:
        store.set("update.branch_production", prod)
        store.set("update.branch_test", test)
        store.set("update.auto_check", bool(request.form.get("auto_check")))
        flash(_t("Update settings saved."), "ok")
    return redirect(url_for("main.server"))


# ---------------------------------------------------------------- network & access

def _client_ip():
    return request.remote_addr or ""


def _apply_or_revert(revert):
    """Apply the firewall/Caddy. When kms-ctl refuses, put the previous settings back."""
    try:
        network.apply()
        return True
    except system.SystemError_ as exc:
        if not system._available():  # noqa: SLF001 - development mode, nothing to apply
            store.set("net.last_error", "")
            flash(_t("Saved. The firewall is not applied in development mode."), "ok")
            return True
        revert()
        network.invalidate()
        try:
            network.write_config()
        except OSError:
            pass
        flash(_t("Not applied, the previous settings are back: {err}", err=exc), "error")
        return False


def _snapshot(*keys):
    saved = {k: store.get(k) for k in keys}

    def revert():
        for k, v in saved.items():
            store.set(k, v)
    return revert


def _lines(text):
    return [p.strip() for p in re.split(r"[\s,;]+", text or "") if p.strip()][:50]


@bp.route("/network", endpoint="network")
@login_required
def network_page():
    tab = "kms" if request.args.get("tab") == "kms" else "web"
    s = network.settings()
    ctx = {"active": "network", "tab": tab, "s": s, "client_ip": _client_ip(), "lan": network.local_network(),
           "console_port": config.PORT, "kms_port": store.get("kms.port"), "host_ip": kms.detect_ip(),
           "via_proxy": bool(request.environ.get("kms.proxy_addr")), "is_https": request.is_secure,
           "https_check": store.get("net.https_check") or {}, "mail_on": store.get("mail.enabled"),
           "notify_blocked": store.get("mail.notify_blocked")}
    for e in s["kms_acl"]:
        try:
            e["public"] = network.parse_entry(e["cidr"])[1]
        except NetError:
            e["public"] = True
    if tab == "kms":
        ctx["activity"] = network.activity(s["kms_acl"])
        ctx["blocked"] = network.blocked() if s["kms_restrict"] else []
    return render_template("network.html", **ctx)


@bp.route("/network/mode", methods=["POST"])
@login_required
def network_mode():
    mode = request.form.get("mode", "lan")
    old_mode = store.get("net.mode")
    proxies = []
    try:
        for p in _lines(request.form.get("proxy_ips")):
            net, _ = network.parse_entry(p)
            proxies.append({"cidr": str(net) if net.num_addresses > 1 else str(net.network_address), "descr": "", "added": int(time.time())})
        close = bool(request.form.get("close_console"))
        if mode == "caddy" and old_mode != "caddy":
            close = False   # keep port 8080 open until HTTPS has been seen working
        if mode == "caddy" and close and not request.environ.get("kms.proxy_addr"):
            raise NetError("open the web interface through https://your-domain first, then close port 8080")
        values = {"mode": mode, "domain": (request.form.get("domain") or "").strip().lower(),
                  "acme_email": (request.form.get("acme_email") or "").strip(), "close_console": close,
                  "proxy_ips": proxies if mode == "proxy" else store.get("net.proxy_ips")}
        network.check(values)
        if mode == "proxy":
            ip = _client_ip()
            via = request.environ.get("kms.proxy_addr") or ip
            if not network.covered(via, proxies) and not network.covered(ip, store.get("net.web_acl") or []):
                raise NetError("you would lock yourself out: add your own address to the web access list first, or open this page through the proxy")
    except NetError as exc:
        flash(_t("Not saved: {err}", err=_t(str(exc))), "error")
        return redirect(url_for("main.network"))
    revert = _snapshot("net.mode", "net.domain", "net.acme_email", "net.close_console", "net.proxy_ips")
    for k, v in values.items():
        store.set(f"net.{k}", v)
    if _apply_or_revert(revert):
        if mode == "caddy" and old_mode != "caddy":
            flash(_t("Caddy is requesting a certificate for {domain}. Open https://{domain} to check it, then close port 8080.", domain=values["domain"]), "ok")
        else:
            flash(_t("Network settings saved and applied."), "ok")
    return redirect(url_for("main.network"))


@bp.route("/network/acl", methods=["POST"])
@login_required
def network_acl():
    scope = "kms" if request.form.get("scope") == "kms" else "web"
    action = request.form.get("action")
    revert = _snapshot(f"net.{scope}_acl")
    try:
        if action == "add":
            cidr = request.form.get("cidr", "")
            if request.form.get("mine"):
                cidr = _client_ip()
            _, public = network.add_entry(scope, cidr, request.form.get("descr", ""), _client_ip())
            if public:
                flash(_t("{cidr} is a public address. Only add fixed addresses of sites you know.", cidr=cidr), "error")
        elif action == "remove":
            network.remove_entry(scope, request.form.get("cidr", ""), _client_ip())
        else:
            abort(400)
    except NetError as exc:
        flash(_t("Not saved: {err}", err=_t(str(exc))), "error")
        return redirect(url_for("main.network", tab=scope))
    if _apply_or_revert(revert):
        flash(_t("Access list updated."), "ok")
    return redirect(url_for("main.network", tab=scope))


@bp.route("/network/kms", methods=["POST"])
@login_required
def network_kms():
    restrict = request.form.get("restrict") == "1"
    try:
        network.check({"kms_restrict": restrict})
    except NetError as exc:
        flash(_t("Not saved: {err}", err=_t(str(exc))), "error")
        return redirect(url_for("main.network", tab="kms"))
    revert = _snapshot("net.kms_restrict")
    store.set("net.kms_restrict", restrict)
    if _apply_or_revert(revert):
        flash(_t("Only addresses on the list can use the KMS server now.") if restrict
              else _t("Every address can use the KMS server again."), "ok")
    return redirect(url_for("main.network", tab="kms"))


@bp.route("/network/test-https", methods=["POST"])
@login_required
def network_test_https():
    domain = store.get("net.domain")
    result = network.check_https(domain) if domain else {"ok": False, "error": "no domain"}
    result["ts"] = int(time.time())
    store.set("net.https_check", result)
    if result["ok"]:
        flash(_t("HTTPS works for {domain}.", domain=domain), "ok")
    else:
        flash(_t("HTTPS does not work yet: {err}", err=result["error"]), "error")
    return redirect(url_for("main.network"))


@bp.route("/healthz")
def healthz():
    return jsonify(ok=True, kms=kms.server_alive(), version=config.version())


@bp.route("/favicon.ico")
def favicon():
    return send_file(Path(__file__).parent / "static" / "img" / "favicon.svg", mimetype="image/svg+xml")
