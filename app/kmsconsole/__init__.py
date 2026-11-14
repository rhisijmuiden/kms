"""KMS Console: web management for a py-kms server running in an LXC container."""
import hashlib
import os
import time

from flask import Flask, g, redirect, request, session, url_for
from flask_wtf.csrf import CSRFProtect

from . import config, i18n, store

csrf = CSRFProtect()


def _pykms_version():
    try:
        return (config.PYKMS_DIR.parent / "PYKMS_VERSION").read_text().strip()
    except OSError:
        return "-"


def create_app(start_scheduler=True, testing=False):
    app = Flask(__name__)
    store.init()
    store._fernet()  # noqa: SLF001 - makes sure the key file exists
    secret = hashlib.sha256(b"kms-console-session" + config.SECRET_KEY_FILE.read_bytes()).hexdigest()
    app.config.update(
        SECRET_KEY=secret,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("KMS_CONSOLE_HTTPS") == "1",
        PERMANENT_SESSION_LIFETIME=12 * 3600,
        MAX_CONTENT_LENGTH=64 * 1024 * 1024,
        WTF_CSRF_TIME_LIMIT=None,
        TESTING=testing,
        WTF_CSRF_ENABLED=not testing,
    )
    csrf.init_app(app)

    @app.before_request
    def _lang():
        lang = session.get("lang") or request.cookies.get("lang")
        if lang not in i18n.LANGUAGES:
            lang = request.accept_languages.best_match(list(i18n.LANGUAGES)) or i18n.DEFAULT
            # The app is English by default; only switch when the browser clearly prefers Dutch
            if lang != "nl":
                lang = i18n.DEFAULT
        g.lang = lang

    @app.context_processor
    def _ctx():
        return {
            "t": lambda text, **kw: i18n.translate(g.lang, text, **kw),
            "lang": g.lang,
            "languages": i18n.LANGUAGES,
            "app_version": config.version(),
            "update_channel": store.get("update.channel"),
            "update_info": store.get("update.last_result") or {},
            "now": time.time(),
            "pykms_version": _pykms_version(),
        }

    @app.template_filter("ago")
    def _ago(ts):
        if not ts:
            return "-"
        diff = time.time() - float(ts)
        t = lambda s, **kw: i18n.translate(g.lang, s, **kw)  # noqa: E731
        if diff < 60:
            return t("just now")
        if diff < 3600:
            return t("{n} min ago", n=int(diff // 60))
        if diff < 86400:
            return t("{n} hours ago", n=int(diff // 3600))
        if diff < 2 * 86400:
            return t("yesterday")
        return t("{n} days ago", n=int(diff // 86400))

    @app.template_filter("datetime")
    def _dt(ts):
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts))) if ts else "-"

    @app.template_filter("filesize")
    def _size(n):
        n = float(n or 0)
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024 or unit == "GB":
                return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
            n /= 1024
        return f"{n} B"

    from .views import bp
    app.register_blueprint(bp)

    @app.errorhandler(404)
    def _404(_e):
        return redirect(url_for("main.dashboard"))

    if start_scheduler:
        from . import scheduler
        scheduler.start()
    return app
