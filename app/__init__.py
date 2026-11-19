"""BASE Design Office - private BOQ business management (Flask + SQLite)."""
import json
import os
import secrets
from flask import Flask, abort, g, redirect, request, session, url_for
from markupsafe import Markup, escape
from . import db as dbm
from . import logic, util


def create_app(data_dir=None, testing=False):
    app = Flask(__name__)
    data_dir = os.path.abspath(data_dir or os.environ.get("BASE_OFFICE_DATA") or
                               os.path.join(os.path.dirname(os.path.dirname(__file__)), "data"))
    os.makedirs(os.path.join(data_dir, "files"), exist_ok=True)
    key_file = os.path.join(data_dir, "secret.key")
    if not os.path.exists(key_file):
        with open(key_file, "w") as fh:
            fh.write(secrets.token_hex(32))
    with open(key_file) as fh:
        secret = fh.read().strip()
    app.config.update(
        SECRET_KEY=secret,
        DATABASE=os.path.join(data_dir, "base_office.db"),
        DATA_DIR=data_dir,
        FILES_DIR=os.path.join(data_dir, "files"),
        MAX_CONTENT_LENGTH=200 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 14,
        TESTING=testing,
    )
    dbm.init_db(app.config["DATABASE"])
    app.teardown_appcontext(dbm.close_db)

    @app.before_request
    def guard():
        if request.endpoint in ("static", "login", "setup", "set_lang"):
            return None
        db = dbm.get_db()
        if db.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0:
            return redirect(url_for("setup"))
        if not session.get("user"):
            return redirect(url_for("login", next=request.full_path if request.method == "GET" else None))
        return None

    @app.before_request
    def csrf():
        if request.method == "POST":
            sent = request.form.get("csrf") or request.headers.get("X-CSRF")
            if not sent or sent != session.get("csrf"):
                abort(400, "Form expired. Go back, reload the page and try again.")

    def csrf_token():
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        return session["csrf"]

    def lang():
        return session.get("lang", "en")

    def L(en, ar):
        return ar if lang() == "ar" else en

    def label(table, key):
        pair = table.get(key)
        if not pair:
            return key or ""
        return pair[1] if lang() == "ar" else pair[0]

    def bidi(text):
        """Isolate IDs, emails and signed numbers inside RTL text."""
        if text is None:
            return ""
        return Markup('<bdi dir="ltr">') + escape(str(text)) + Markup("</bdi>")

    @app.context_processor
    def inject():
        settings = dbm.get_settings() if request.endpoint != "static" else {}
        return dict(
            L=L, lang=lang(), csrf_token=csrf_token, label=label, bidi=bidi,
            kwd=util.fmt_kwd, m=util.fmt_m, d=util.fmt_date, dt=util.fmt_dt, wa=util.wa_link,
            WORK=logic.WORK_LABELS, SUB=logic.SUB_LABELS, QUOTE=logic.QUOTE_LABELS, MONEY=logic.MONEY_LABELS,
            FILTERS=logic.FILTERS, SCOPES=logic.SCOPES, scope_label=lambda k: logic.scope_label(k, lang()),
            settings=settings, json_loads=json.loads, user=session.get("user"),
        )

    from . import routes
    routes.register(app)
    return app
