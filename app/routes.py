"""HTTP routes for BASE Design Office."""
import hashlib
import io
import json
import os
import uuid
from datetime import date, timedelta

from flask import (abort, current_app, flash, redirect, render_template, request, send_file, session, url_for,
                   Response)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from . import archive, emails, excel_export, floors, logic, pdfs, readiness, revit_export
from .db import get_db, get_settings, new_uid, set_setting
from .util import (fmt_kwd, now_iso, parse_date, parse_float, parse_int, parse_kwd, parse_m, today_kw)

ALLOWED_EXT = {".pdf", ".dwg", ".dxf", ".rvt", ".rfa", ".xlsx", ".xls", ".xlsm", ".csv", ".png", ".jpg", ".jpeg",
               ".zip", ".docx"}
PROJECT_TEXT = ["name", "owner_name", "area", "block", "street", "plot", "map_link", "building_type", "roof_structures",
                "building_notes", "custom_scope", "exclusions", "excel_template", "responsible", "source_drawings",
                "missing_info", "internal_notes"]
BUILDING_TYPES = ["villa", "apartment building", "chalet", "commercial", "other"]


class FormErrors(Exception):
    def __init__(self, errors):
        self.errors = errors


def _get_or_404(sql, args):
    row = get_db().execute(sql, args).fetchone()
    if row is None:
        abort(404)
    return row


def project_or_404(pid):
    return _get_or_404("SELECT * FROM projects WHERE id = ?", (pid,))


def client_or_404(cid):
    return _get_or_404("SELECT * FROM clients WHERE id = ?", (cid,))


def parse_fields(form, spec):
    """spec: {field: parser}. Returns (values, errors). Keeps blank as None."""
    vals, errs = {}, {}
    for field, parser in spec.items():
        raw = form.get(field, "")
        try:
            vals[field] = parser(raw)
        except ValueError as e:
            errs[field] = str(e)
    return vals, errs


def text(raw):
    raw = (raw or "").strip()
    return raw or None


def tri(raw):
    return None if raw in ("", None) else (1 if raw in ("1", "yes", "on", "true") else 0)


PROJECT_SPEC = dict({k: text for k in PROJECT_TEXT}, plot_area_m2=parse_float, basement_count=parse_int,
                    upper_floor_count=parse_int, has_roof=tri, typical_ftf_mm=parse_m, gf_ffl_mm=parse_m,
                    date_received=parse_date, due_date=parse_date)


def scope_from_form(form):
    chosen = [s for s in logic.SCOPES if form.get(f"scope_{s}")]
    preset = form.get("preset")
    if preset:
        presets = json.loads(get_settings().get("scope_presets") or "{}")
        for s in presets.get(preset, []):
            if s not in chosen:
                chosen.append(s)
    return [s for s in logic.SCOPES if s in chosen]


def write_file(data_bytes):
    stored = uuid.uuid4().hex
    path = os.path.join(current_app.config["FILES_DIR"], stored)
    tmp = path + ".part"
    with open(tmp, "wb") as fh:
        fh.write(data_bytes)
    os.replace(tmp, path)
    return stored, hashlib.sha256(data_bytes).hexdigest()


def file_path(stored):
    return os.path.join(current_app.config["FILES_DIR"], stored)


def project_bundle(db, p):
    client = db.execute("SELECT * FROM clients WHERE id = ?", (p["client_id"],)).fetchone()
    levels = db.execute("SELECT * FROM levels WHERE project_id = ? ORDER BY seq", (p["id"],)).fetchall()
    grids = db.execute("SELECT * FROM grids WHERE project_id = ? ORDER BY seq", (p["id"],)).fetchall()
    drawings = db.execute("SELECT * FROM drawings WHERE project_id = ? ORDER BY direction, title, uploaded_at DESC",
                          (p["id"],)).fetchall()
    return client, levels, grids, drawings


def register(app):
    # ------------------------------------------------------------ auth
    @app.route("/setup", methods=["GET", "POST"])
    def setup():
        db = get_db()
        if db.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] > 0:
            return redirect(url_for("login"))
        error = None
        if request.method == "POST":
            if request.form.get("csrf") != session.get("csrf"):
                abort(400)
            u = (request.form.get("username") or "").strip()
            pw = request.form.get("password") or ""
            if not u or len(pw) < 8:
                error = "Choose a username and a password of at least 8 characters."
            elif pw != request.form.get("password2"):
                error = "The two passwords do not match."
            else:
                db.execute("INSERT INTO users(username, password_hash, created_at) VALUES (?,?,?)",
                           (u, generate_password_hash(pw), now_iso()))
                logic.log(db, "user_created", f"Sign-in created for {u}", actor=u)
                db.commit()
                session.clear()
                session["user"] = u
                session.permanent = True
                return redirect(url_for("settings_page"))
        return render_template("auth.html", mode="setup", error=error)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            if request.form.get("csrf") != session.get("csrf"):
                abort(400)
            u = (request.form.get("username") or "").strip()
            row = get_db().execute("SELECT * FROM users WHERE username = ?", (u,)).fetchone()
            if row and check_password_hash(row["password_hash"], request.form.get("password") or ""):
                lang = session.get("lang")
                session.clear()
                session["user"] = u
                if lang:
                    session["lang"] = lang
                session.permanent = True
                nxt = request.args.get("next") or ""
                return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("dashboard"))
            error = "Wrong username or password."
        return render_template("auth.html", mode="login", error=error)

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/lang/<code>")
    def set_lang(code):
        session["lang"] = "ar" if code == "ar" else "en"
        ref = request.referrer or url_for("dashboard")
        return redirect(ref)

    # ------------------------------------------------------------ dashboard
    @app.route("/")
    def dashboard():
        db = get_db()
        flt = request.args.get("f")
        if flt is None:
            flt = session.get("last_filter", "all")
        if flt not in logic.FILTERS:
            flt = "all"
        session["last_filter"] = flt
        q = request.args.get("q", "").strip()
        rows = logic.project_rows(db, flt, q)
        metrics = logic.dashboard_metrics(db)
        ready = logic.project_rows(db, "ready")
        owed = logic.project_rows(db, "delivered_balance")
        today = logic.project_rows(db, "today")
        first_use = db.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"] == 0
        return render_template("dashboard.html", rows=rows, flt=flt, q=q, mt=metrics, ready=ready, owed=owed,
                               today_rows=today, first_use=first_use)

    @app.route("/collections")
    def collections():
        db = get_db()
        rows = logic.project_rows(db, "outstanding")
        delivered = [r for r in rows if r["sub"]["status"] == "full"]
        other = [r for r in rows if r["sub"]["status"] != "full"]
        by_client = {}
        for r in rows:
            c = by_client.setdefault(r["client_id"], dict(name=r["client_name"], total=0, n=0))
            c["total"] += r["money"]["balance"]
            c["n"] += 1
        return render_template("collections.html", delivered=delivered, other=other, by_client=by_client)

    # ------------------------------------------------------------ projects
    @app.route("/projects/new", methods=["GET", "POST"])
    def project_new():
        db = get_db()
        clients = db.execute("SELECT id, name FROM clients ORDER BY name").fetchall()
        presets = json.loads(get_settings().get("scope_presets") or "{}")
        form = request.form if request.method == "POST" else {"client_id": request.args.get("client_id", "")}
        errors = {}
        if request.method == "POST":
            vals, errors = parse_fields(request.form, PROJECT_SPEC)
            try:
                fee = parse_kwd(request.form.get("agreed_fee"))
            except ValueError as e:
                errors["agreed_fee"] = str(e)
                fee = None
            if not vals.get("name"):
                errors["name"] = "Project name is required"
            cid = request.form.get("client_id")
            new_client = (request.form.get("new_client_name") or "").strip()
            if not cid and not new_client:
                errors["client_id"] = "Select a client or type a new client name"
            key = request.form.get("request_key") or ""
            if not errors:
                dup = db.execute("SELECT id FROM projects WHERE uid = ?", (key,)).fetchone() if key else None
                if dup:  # double-submitted form
                    return redirect(url_for("project_view", pid=dup["id"]))
                now = now_iso()
                if not cid:
                    cur = db.execute(
                        "INSERT INTO clients(name, contact_person, phone, whatsapp, email, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                        (new_client, text(request.form.get("new_contact")), text(request.form.get("new_phone")),
                         text(request.form.get("new_phone")), text(request.form.get("new_email")), now, now))
                    cid = cur.lastrowid
                    logic.log(db, "client_created", new_client, client_id=cid)
                else:
                    client_or_404(int(cid))
                number = logic.next_project_number(db)
                scope = scope_from_form(request.form)
                cols = list(PROJECT_SPEC.keys())
                db.execute(
                    f"INSERT INTO projects(uid, number, client_id, {', '.join(cols)}, scope, agreed_fee_fils, fee_basis, "
                    f"fee_basis_note, created_at, updated_at) VALUES (?,?,?,{','.join('?' * len(cols))},?,?,?,?,?,?)",
                    [key or new_uid(), number, int(cid)] + [vals[c] for c in cols] +
                    [json.dumps(scope), fee, "direct" if fee is not None else None,
                     "Fee entered at intake" if fee is not None else None, now, now])
                pid = db.execute("SELECT id FROM projects WHERE number = ?", (number,)).fetchone()["id"]
                logic.log(db, "project_created", f"{number} {vals['name']}", project_id=pid, client_id=int(cid),
                          after=dict(scope=scope, agreed_fee=fmt_kwd(fee) if fee is not None else None))
                if vals.get("basement_count") is not None or vals.get("upper_floor_count") is not None:
                    _generate_levels(db, pid)
                db.commit()
                flash(f"Saved {number}", "ok")
                return redirect(url_for("project_view", pid=pid))
        return render_template("project_form.html", mode="new", clients=clients, presets=presets, form=form,
                               errors=errors, project=None, scope=scope_from_form(form) if form else [],
                               request_key=request.form.get("request_key") or new_uid(), types=BUILDING_TYPES)

    @app.route("/projects/<int:pid>")
    def project_view(pid):
        db = get_db()
        p = project_or_404(pid)
        tab = request.args.get("tab", "overview")
        client, levels, grids, drawings = project_bundle(db, p)
        money = logic.project_money(db, p)
        sub = logic.submission_state(db, p)
        scope = logic.project_scope(p)
        quotes = db.execute("SELECT * FROM quotations WHERE project_id = ? ORDER BY number, revision DESC", (pid,)).fetchall()
        allocs = db.execute("""SELECT a.amount_fils AS alloc, pay.* FROM allocations a JOIN payments pay ON pay.id = a.payment_id
                               WHERE a.project_id = ? ORDER BY pay.date_received, pay.id""", (pid,)).fetchall()
        deliveries = db.execute("""SELECT dl.*, dr.original_name, dr.revision AS drawing_rev FROM deliveries dl
                                   LEFT JOIN drawings dr ON dr.id = dl.drawing_id WHERE dl.project_id = ?
                                   ORDER BY dl.delivery_date, dl.id""", (pid,)).fetchall()
        acts = db.execute("SELECT * FROM activity WHERE project_id = ? ORDER BY at DESC, id DESC", (pid,)).fetchall()
        rules = logic.applicable_rules(db, p)
        ready_info = readiness.trade_readiness(scope, drawings, levels)
        missing = readiness.missing_information(p, levels, drawings, scope)
        clients = db.execute("SELECT id, name FROM clients ORDER BY name").fetchall()
        presets = json.loads(get_settings().get("scope_presets") or "{}")
        explanation = floors.count_explanation(p["basement_count"], p["upper_floor_count"], p["has_roof"], p["roof_structures"])
        drafts = db.execute("""SELECT r.*, rv.text FROM rules r JOIN rule_revisions rv ON rv.rule_id = r.id AND rv.revision = r.current_revision
                               WHERE r.project_id = ? AND r.status <> 'approved' AND r.active = 1""", (pid,)).fetchall()
        return render_template("project.html", drafts=drafts, p=p, tab=tab, client=client, levels=levels, grids=grids,
                               drawings=drawings, money=money, sub=sub, scope=scope, quotes=quotes, allocs=allocs,
                               deliveries=deliveries, acts=acts, rules=rules, ready_info=ready_info, missing=missing,
                               clients=clients, presets=presets, explanation=explanation, types=BUILDING_TYPES,
                               total_area=floors.total_area([dict(l) for l in levels]), new_key=new_uid(),
                               today=today_kw().isoformat(), preview=None, form=None, errors={})

    @app.route("/projects/<int:pid>/edit", methods=["POST"])
    def project_edit(pid):
        db = get_db()
        p = project_or_404(pid)
        vals, errors = parse_fields(request.form, PROJECT_SPEC)
        if not vals.get("name"):
            errors["name"] = "Project name is required"
        try:
            version = int(request.form.get("version", "0"))
        except ValueError:
            version = 0
        cid = int(request.form.get("client_id") or p["client_id"])
        client_or_404(cid)
        if errors:
            flash("Not saved: " + "; ".join(f"{k}: {v}" for k, v in errors.items()), "error")
            return redirect(url_for("project_view", pid=pid, tab="overview"))
        scope = scope_from_form(request.form)
        changes = {}
        for k, v in vals.items():
            if p[k] != v:
                changes[k] = (p[k], v)
        if json.loads(p["scope"]) != scope:
            changes["scope"] = (json.loads(p["scope"]), scope)
        if cid != p["client_id"]:
            changes["client_id"] = (p["client_id"], cid)
        if not changes:
            flash("No changes", "ok")
            return redirect(url_for("project_view", pid=pid))
        sets = ", ".join(f"{k} = ?" for k in vals) + ", scope = ?, client_id = ?, version = version + 1, updated_at = ?"
        cur = db.execute(f"UPDATE projects SET {sets} WHERE id = ? AND version = ?",
                         list(vals.values()) + [json.dumps(scope), cid, now_iso(), pid, version])
        if cur.rowcount == 0:
            db.rollback()
            flash("Someone (or another tab) changed this project after you opened it. Nothing was overwritten: "
                  "reload the page and re-enter your change.", "error")
            return redirect(url_for("project_view", pid=pid))
        logic.log(db, "project_updated", ", ".join(changes.keys()), project_id=pid, client_id=cid,
                  before={k: v[0] for k, v in changes.items()}, after={k: v[1] for k, v in changes.items()})
        db.commit()
        flash("Saved", "ok")
        return redirect(url_for("project_view", pid=pid))

    @app.route("/projects/<int:pid>/status", methods=["POST"])
    def project_status(pid):
        db = get_db()
        p = project_or_404(pid)
        st = request.form.get("work_status")
        if st not in logic.WORK_STATUSES:
            abort(400)
        if st != p["work_status"]:
            db.execute("UPDATE projects SET work_status = ?, version = version + 1, updated_at = ? WHERE id = ?",
                       (st, now_iso(), pid))
            note = (request.form.get("note") or "").strip()
            if st == "waiting" and note:
                missing = (p["missing_info"] + "\n" if p["missing_info"] else "") + note
                db.execute("UPDATE projects SET missing_info = ? WHERE id = ?", (missing, pid))
            logic.log(db, "work_status", f"{logic.WORK_LABELS[p['work_status']][0]} -> {logic.WORK_LABELS[st][0]}"
                      + (f" ({note})" if note else ""), project_id=pid, client_id=p["client_id"],
                      before=p["work_status"], after=st)
            db.commit()
        return redirect(request.form.get("back") or url_for("project_view", pid=pid))

    @app.route("/projects/<int:pid>/archive", methods=["POST"])
    def project_archive(pid):
        db = get_db()
        p = project_or_404(pid)
        new = 0 if p["archived"] else 1
        db.execute("UPDATE projects SET archived = ?, version = version + 1 WHERE id = ?", (new, pid))
        logic.log(db, "project_archived" if new else "project_reopened", p["number"], project_id=pid, client_id=p["client_id"])
        db.commit()
        return redirect(url_for("project_view", pid=pid))

    @app.route("/projects/<int:pid>/direct", methods=["POST"])
    def project_direct(pid):
        """Direct job agreed by WhatsApp / phone: record fee + evidence without a quotation."""
        db = get_db()
        p = project_or_404(pid)
        try:
            fee = parse_kwd(request.form.get("fee"))
            agreed_on = parse_date(request.form.get("agreed_on")) or today_kw().isoformat()
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("project_view", pid=pid, tab="payments"))
        if fee is None:
            flash("Enter the agreed fee", "error")
            return redirect(url_for("project_view", pid=pid, tab="payments"))
        note = f"Agreed {agreed_on} via {request.form.get('channel') or 'WhatsApp'}: {request.form.get('evidence') or ''}".strip()
        before = p["agreed_fee_fils"]
        db.execute("UPDATE projects SET agreed_fee_fils = ?, fee_basis = 'direct', fee_basis_note = ?, version = version + 1, "
                   "updated_at = ? WHERE id = ?", (fee, note, now_iso(), pid))
        logic.log(db, "fee_agreed_direct" if before is None else "fee_corrected",
                  f"{fmt_kwd(fee)} KWD. {note}", project_id=pid, client_id=p["client_id"],
                  before=fmt_kwd(before) if before is not None else None, after=fmt_kwd(fee))
        db.commit()
        flash("Agreed fee saved", "ok")
        return redirect(url_for("project_view", pid=pid, tab="payments"))

    # ------------------------------------------------------------ floors
    def _generate_levels(db, pid):
        p = db.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
        rows = floors.generate(p["basement_count"], p["upper_floor_count"], p["has_roof"], p["roof_structures"],
                               p["gf_ffl_mm"], p["typical_ftf_mm"])
        db.execute("DELETE FROM levels WHERE project_id = ?", (pid,))
        _insert_levels(db, pid, rows)
        logic.log(db, "floors_generated", floors.count_explanation(p["basement_count"], p["upper_floor_count"],
                                                                     p["has_roof"], p["roof_structures"]),
                  project_id=pid, client_id=p["client_id"])

    def _insert_levels(db, pid, rows):
        for i, r in enumerate(rows):
            db.execute("""INSERT INTO levels(project_id, uid, seq, name, kind, ffl_mm, ftf_mm, area_m2, typical_group, notes,
                          fixed, confirmed, slab_offset_mm, clear_height_mm, slab_thk_mm, finish_mm, drawing_ref)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (pid, r["uid"], i, r["name"], r["kind"], r["ffl_mm"], r["ftf_mm"], r["area_m2"], r["typical_group"],
                        r["notes"], int(bool(r["fixed"])), int(bool(r["confirmed"])), r["slab_offset_mm"],
                        r["clear_height_mm"], r["slab_thk_mm"], r["finish_mm"], r["drawing_ref"]))

    @app.route("/projects/<int:pid>/floors/generate", methods=["POST"])
    def floors_generate(pid):
        db = get_db()
        p = project_or_404(pid)
        vals, errors = parse_fields(request.form, dict(basement_count=parse_int, upper_floor_count=parse_int,
                                                       has_roof=tri, roof_structures=text, typical_ftf_mm=parse_m,
                                                       gf_ffl_mm=parse_m))
        if errors:
            flash("; ".join(errors.values()), "error")
            return redirect(url_for("project_view", pid=pid, tab="floors"))
        existing = db.execute("SELECT COUNT(*) AS n FROM levels WHERE project_id = ?", (pid,)).fetchone()["n"]
        if existing and not request.form.get("replace"):
            flash("A floor table already exists. Tick 'Replace the existing table' to regenerate it.", "error")
            return redirect(url_for("project_view", pid=pid, tab="floors"))
        db.execute("UPDATE projects SET basement_count=?, upper_floor_count=?, has_roof=?, roof_structures=?, typical_ftf_mm=?, "
                   "gf_ffl_mm=?, version = version + 1, updated_at=? WHERE id = ?",
                   (vals["basement_count"], vals["upper_floor_count"], vals["has_roof"], vals["roof_structures"],
                    vals["typical_ftf_mm"], vals["gf_ffl_mm"], now_iso(), pid))
        _generate_levels(db, pid)
        db.commit()
        return redirect(url_for("project_view", pid=pid, tab="floors"))

    def _levels_from_form(form, existing):
        uids = form.getlist("uid")
        rows, errs = [], []
        ex = {r["uid"]: r for r in existing}
        for i, uid in enumerate(uids):
            def g(name):
                vals = form.getlist(name)
                return vals[i] if i < len(vals) else ""
            r = dict(uid=uid if uid in ex or uid.startswith("new") else new_uid())
            if r["uid"].startswith("new"):
                r["uid"] = new_uid()
            r["name"] = (g("name") or "").strip() or f"Level {i + 1}"
            r["kind"] = g("kind") or "other"
            r["typical_group"] = text(g("typical_group"))
            r["notes"] = text(g("notes"))
            r["drawing_ref"] = text(g("drawing_ref"))
            r["fixed"] = 1 if uid in form.getlist("fixed") else 0
            r["confirmed"] = 1 if uid in form.getlist("confirmed") else 0
            for f, parser in (("ffl_mm", parse_m), ("ftf_mm", parse_m), ("slab_offset_mm", parse_m),
                              ("clear_height_mm", parse_m), ("slab_thk_mm", parse_int), ("finish_mm", parse_int),
                              ("area_m2", parse_float)):
                try:
                    r[f] = parser(g(f))
                except ValueError as e:
                    errs.append(f"{r['name']}: {e}")
                    r[f] = None
            r["_raw_uid"] = uid
            rows.append(r)
        return rows, errs

    @app.route("/projects/<int:pid>/floors", methods=["POST"])
    def floors_save(pid):
        db = get_db()
        p = project_or_404(pid)
        existing = [dict(r) for r in db.execute("SELECT * FROM levels WHERE project_id = ? ORDER BY seq", (pid,))]
        rows, errs = _levels_from_form(request.form, existing)
        rows.reverse()  # the table shows the top level first; storage is bottom-to-top
        action = request.form.get("action", "preview")
        if action.startswith("dup:"):
            idx = int(action.split(":")[1])
            rows = floors.duplicate(rows, idx)
            action = "preview"
        elif action.startswith("del:"):
            idx = int(action.split(":")[1])
            rows = rows[:idx] + rows[idx + 1:]
            action = "preview"
        elif action == "add":
            rows.append(dict(uid=new_uid(), name=f"Level {len(rows) + 1}", kind="other", ffl_mm=None, ftf_mm=None,
                             area_m2=None, typical_group=None, notes=None, fixed=0, confirmed=0, slab_offset_mm=None,
                             clear_height_mm=None, slab_thk_mm=None, finish_mm=None, drawing_ref=None))
            action = "preview"
        new_rows, warnings = floors.recalculate(rows)
        moves = floors.preview_changes(existing, new_rows)
        names = [r["name"] for r in new_rows]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            errs.append("Level names must be unique (Revit needs unique names): " + ", ".join(dupes))
        if action == "save" and not errs:
            db.execute("DELETE FROM levels WHERE project_id = ?", (pid,))
            _insert_levels(db, pid, new_rows)
            gf = next((r for r in new_rows if r["kind"] == "ground"), None)
            if gf and gf["ffl_mm"] is not None:
                db.execute("UPDATE projects SET gf_ffl_mm = ? WHERE id = ?", (gf["ffl_mm"], pid))
            logic.log(db, "floors_saved", f"{len(new_rows)} levels" + (f"; moved: {', '.join(m['name'] for m in moves)}" if moves else ""),
                      project_id=pid, client_id=p["client_id"],
                      before=[{"name": r["name"], "ffl_mm": r["ffl_mm"]} for r in existing],
                      after=[{"name": r["name"], "ffl_mm": r["ffl_mm"]} for r in new_rows])
            db.commit()
            flash("Floor table saved", "ok")
            return redirect(url_for("project_view", pid=pid, tab="floors"))
        # preview: render the project page with unsaved rows
        client, _, grids, drawings = project_bundle(db, p)
        money = logic.project_money(db, p)
        sub = logic.submission_state(db, p)
        scope = logic.project_scope(p)
        return render_template("project.html", drafts=[], p=p, tab="floors", client=client, levels=new_rows, grids=grids,
                               drawings=drawings, money=money, sub=sub, scope=scope, quotes=[], allocs=[],
                               deliveries=[], acts=[], rules=logic.applicable_rules(db, p),
                               ready_info=readiness.trade_readiness(scope, drawings, new_rows),
                               missing=[], clients=[], presets={}, types=BUILDING_TYPES,
                               explanation=floors.count_explanation(p["basement_count"], p["upper_floor_count"],
                                                                    p["has_roof"], p["roof_structures"]),
                               total_area=floors.total_area(new_rows), new_key=new_uid(), today=today_kw().isoformat(),
                               preview=dict(moves=moves, warnings=warnings, errors=errs), form=None, errors={})

    @app.route("/projects/<int:pid>/grids", methods=["POST"])
    def grids_save(pid):
        db = get_db()
        p = project_or_404(pid)
        old = {r["uid"]: r for r in db.execute("SELECT * FROM grids WHERE project_id = ?", (pid,))}
        uids = request.form.getlist("uid")
        rows, errs = [], []
        for i, uid in enumerate(uids):
            def g(n):
                v = request.form.getlist(n)
                return v[i] if i < len(v) else ""
            name = (g("name") or "").strip()
            if not name:
                continue
            r = dict(uid=uid if uid in old else new_uid(), name=name, notes=text(g("notes")))
            for f in ("x1", "y1", "x2", "y2"):
                try:
                    r[f + "_mm"] = parse_m(g(f))
                except ValueError as e:
                    errs.append(f"Grid {name}: {e}")
                    r[f + "_mm"] = None
            rows.append(r)
        names = [r["name"] for r in rows]
        if len(set(names)) != len(names):
            errs.append("Grid names must be unique")
        if errs:
            flash("Grids not saved: " + "; ".join(errs), "error")
            return redirect(url_for("project_view", pid=pid, tab="floors"))
        db.execute("DELETE FROM grids WHERE project_id = ?", (pid,))
        for i, r in enumerate(rows):
            db.execute("INSERT INTO grids(project_id, uid, seq, name, x1_mm, y1_mm, x2_mm, y2_mm, notes) VALUES (?,?,?,?,?,?,?,?,?)",
                       (pid, r["uid"], i, r["name"], r["x1_mm"], r["y1_mm"], r["x2_mm"], r["y2_mm"], r["notes"]))
        logic.log(db, "grids_saved", f"{len(rows)} grids", project_id=pid, client_id=p["client_id"])
        db.commit()
        flash("Grids saved", "ok")
        return redirect(url_for("project_view", pid=pid, tab="floors"))

    # ------------------------------------------------------------ drawings
    @app.route("/projects/<int:pid>/drawings", methods=["POST"])
    def drawing_upload(pid):
        db = get_db()
        p = project_or_404(pid)
        f = request.files.get("file")
        direction = request.form.get("direction", "incoming")
        if direction not in ("incoming", "outgoing"):
            abort(400)
        back = url_for("project_view", pid=pid, tab="drawings")
        if not f or not f.filename:
            flash("Upload failed: choose a file. Nothing was recorded.", "error")
            return redirect(back)
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in ALLOWED_EXT:
            flash(f"Upload failed: {ext or 'this file type'} is not allowed (PDF, DWG, RVT, Excel...). Nothing was recorded.", "error")
            return redirect(back)
        data = f.read()
        if not data:
            flash("Upload failed: the file is empty. Nothing was recorded.", "error")
            return redirect(back)
        try:
            stored, sha = write_file(data)
        except OSError as e:
            flash(f"Upload failed while saving ({e}). Nothing was recorded.", "error")
            return redirect(back)
        title = (request.form.get("title") or "").strip() or os.path.splitext(f.filename)[0]
        discipline = text(request.form.get("discipline"))
        try:
            dreceived = parse_date(request.form.get("date_received")) or today_kw().isoformat()
        except ValueError:
            dreceived = today_kw().isoformat()
        # a new revision of the same sheet becomes current
        db.execute("UPDATE drawings SET is_current = 0 WHERE project_id = ? AND direction = ? AND title = ? AND "
                   "COALESCE(discipline,'') = COALESCE(?, '')", (pid, direction, title, discipline))
        cur = db.execute("""INSERT INTO drawings(project_id, direction, title, discipline, floor, revision, date_received,
                            is_current, original_name, stored_name, size_bytes, sha256, uploaded_at)
                            VALUES (?,?,?,?,?,?,?,1,?,?,?,?,?)""",
                         (pid, direction, title, discipline, text(request.form.get("floor")),
                          text(request.form.get("revision")), dreceived, secure_name(f.filename), stored, len(data), sha, now_iso()))
        logic.log(db, "drawing_uploaded" if direction == "incoming" else "deliverable_uploaded",
                  f"{title} rev {request.form.get('revision') or '-'} ({secure_name(f.filename)})",
                  project_id=pid, client_id=p["client_id"])
        db.commit()
        flash("File uploaded", "ok")
        return redirect(back)

    def secure_name(name):
        base, ext = os.path.splitext(name)
        safe = secure_filename(name)
        if not safe or safe == ext.strip("."):
            safe = "file" + ext.lower()
        return safe if any(ch.isalnum() for ch in base) and secure_filename(base) else ("file" + ext.lower())

    @app.route("/drawings/<int:did>/download")
    def drawing_download(did):
        d = _get_or_404("SELECT * FROM drawings WHERE id = ?", (did,))
        return send_file(file_path(d["stored_name"]), as_attachment=True, download_name=d["original_name"])

    @app.route("/drawings/<int:did>/current", methods=["POST"])
    def drawing_current(did):
        db = get_db()
        d = _get_or_404("SELECT * FROM drawings WHERE id = ?", (did,))
        db.execute("UPDATE drawings SET is_current = 0 WHERE project_id = ? AND direction = ? AND title = ? AND "
                   "COALESCE(discipline,'') = COALESCE(?, '')", (d["project_id"], d["direction"], d["title"], d["discipline"]))
        db.execute("UPDATE drawings SET is_current = 1 WHERE id = ?", (did,))
        logic.log(db, "drawing_current", f"{d['title']} rev {d['revision'] or '-'} set as current", project_id=d["project_id"])
        db.commit()
        return redirect(url_for("project_view", pid=d["project_id"], tab="drawings"))

    # ------------------------------------------------------------ deliveries
    @app.route("/projects/<int:pid>/deliveries", methods=["POST"])
    def delivery_add(pid):
        db = get_db()
        p = project_or_404(pid)
        key = request.form.get("request_key") or new_uid()
        if db.execute("SELECT 1 FROM deliveries WHERE request_key = ?", (key,)).fetchone():
            flash("This submission was already recorded.", "ok")
            return redirect(url_for("project_view", pid=pid, tab="drawings"))
        scope = [s for s in logic.project_scope(p) if request.form.get(f"d_{s}")]
        try:
            ddate = parse_date(request.form.get("delivery_date")) or today_kw().isoformat()
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("project_view", pid=pid, tab="drawings"))
        completes = 1 if request.form.get("completes_scope") else 0
        if not scope and not completes:
            flash("Tick the scope you delivered (or 'This completes the agreed scope').", "error")
            return redirect(url_for("project_view", pid=pid, tab="drawings"))
        did = request.form.get("drawing_id")
        drawing = db.execute("SELECT * FROM drawings WHERE id = ? AND project_id = ?", (did, pid)).fetchone() if did else None
        before = logic.submission_state(db, p)["status"]
        db.execute("""INSERT INTO deliveries(project_id, delivery_date, scope, completes_scope, drawing_id, file_revision,
                      recipient, method, notes, rules_used, request_key, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (pid, ddate, json.dumps(scope), completes, drawing["id"] if drawing else None,
                    text(request.form.get("file_revision")) or (drawing["revision"] if drawing else None),
                    text(request.form.get("recipient")), text(request.form.get("method")), text(request.form.get("notes")),
                    json.dumps(logic.rules_snapshot(db, p), ensure_ascii=False), key, now_iso()))
        after = logic.submission_state(db, p)["status"]
        logic.log(db, "submission_recorded",
                  f"{ddate}: {', '.join(logic.scope_label(s) for s in scope) or 'remaining scope'} via {request.form.get('method') or '-'}"
                  f" to {request.form.get('recipient') or '-'}; status {logic.SUB_LABELS[before][0]} -> {logic.SUB_LABELS[after][0]}",
                  project_id=pid, client_id=p["client_id"], before=before, after=after)
        if after == "full" and p["work_status"] != "completed":
            db.execute("UPDATE projects SET work_status = 'completed', version = version + 1 WHERE id = ?", (pid,))
            logic.log(db, "work_status", f"{logic.WORK_LABELS[p['work_status']][0]} -> Completed (full submission)",
                      project_id=pid, client_id=p["client_id"])
        db.commit()
        flash("Submission recorded", "ok")
        return redirect(url_for("project_view", pid=pid, tab="drawings"))

    # ------------------------------------------------------------ payments
    def _record_payment(db, client_id, date_received, amount, method, reference, payer, notes, key, allocs,
                        reverses=None):
        if db.execute("SELECT id FROM payments WHERE request_key = ?", (key,)).fetchone():
            return None  # retry of the same form: already recorded once
        cur = db.execute("""INSERT INTO payments(client_id, date_received, amount_fils, method, reference, payer, notes,
                            reverses_payment_id, request_key, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                         (client_id, date_received, amount, method, reference, payer, notes, reverses, key, now_iso()))
        pay_id = cur.lastrowid
        for project_id, a in allocs:
            db.execute("INSERT INTO allocations(payment_id, project_id, amount_fils) VALUES (?,?,?)", (pay_id, project_id, a))
        return pay_id

    @app.route("/projects/<int:pid>/payments", methods=["POST"])
    def payment_add(pid):
        db = get_db()
        p = project_or_404(pid)
        back = url_for("project_view", pid=pid, tab="payments")
        try:
            amount = parse_kwd(request.form.get("amount"))
            dr = parse_date(request.form.get("date_received"))
        except ValueError as e:
            flash(str(e), "error")
            return redirect(back)
        if not amount or amount <= 0:
            flash("Enter the amount received", "error")
            return redirect(back)
        if not dr:
            flash("Enter the date the money was received", "error")
            return redirect(back)
        before = logic.project_money(db, p)
        pay_id = _record_payment(db, p["client_id"], dr, amount, text(request.form.get("method")),
                                 text(request.form.get("reference")), text(request.form.get("payer")),
                                 text(request.form.get("notes")), request.form.get("request_key") or new_uid(),
                                 [(pid, amount)])
        if pay_id is None:
            flash("This payment was already recorded (the form was sent twice). It was counted once.", "ok")
            return redirect(back)
        after = logic.project_money(db, p)
        kind = request.form.get("kind") or "instalment"
        logic.log(db, "payment_recorded", f"{fmt_kwd(amount)} KWD received {dr} ({kind}, {request.form.get('method') or '-'}). "
                  f"Balance {fmt_kwd(before['balance'])} -> {fmt_kwd(after['balance'])}",
                  project_id=pid, client_id=p["client_id"], before=before, after=after)
        db.commit()
        flash(f"Payment recorded. Balance: {fmt_kwd(after['balance'], 'Pending agreement')} KWD", "ok")
        return redirect(back)

    @app.route("/payments/<int:pay_id>/reverse", methods=["POST"])
    def payment_reverse(pay_id):
        db = get_db()
        pay = _get_or_404("SELECT * FROM payments WHERE id = ?", (pay_id,))
        if pay["amount_fils"] < 0 or db.execute("SELECT 1 FROM payments WHERE reverses_payment_id = ?", (pay_id,)).fetchone():
            flash("This payment is already reversed.", "error")
            return redirect(request.form.get("back") or url_for("payments_list"))
        allocs = db.execute("SELECT * FROM allocations WHERE payment_id = ?", (pay_id,)).fetchall()
        reason = text(request.form.get("reason")) or "correction"
        _record_payment(db, pay["client_id"], today_kw().isoformat(), -pay["amount_fils"], pay["method"],
                        f"Reversal of R-{pay_id:05d}", pay["payer"], reason, f"reverse-{pay_id}",
                        [(a["project_id"], -a["amount_fils"]) for a in allocs], reverses=pay_id)
        for a in allocs:
            logic.log(db, "payment_reversed", f"R-{pay_id:05d} {fmt_kwd(a['amount_fils'])} KWD reversed: {reason}",
                      project_id=a["project_id"], client_id=pay["client_id"])
        db.commit()
        flash("Payment reversed (the original stays in the history).", "ok")
        return redirect(request.form.get("back") or url_for("payments_list"))

    @app.route("/payments/<int:pay_id>/receipt.pdf")
    def payment_receipt(pay_id):
        db = get_db()
        pay = _get_or_404("SELECT * FROM payments WHERE id = ?", (pay_id,))
        client = client_or_404(pay["client_id"])
        allocs = db.execute("""SELECT a.*, p.number, p.name FROM allocations a JOIN projects p ON p.id = a.project_id
                               WHERE a.payment_id = ?""", (pay_id,)).fetchall()
        money = {a["project_id"]: logic.project_money(db, project_or_404(a["project_id"])) for a in allocs}
        s = _pdf_settings()
        data = pdfs.receipt_pdf(s, pay, client, allocs, money, request.args.get("lang") or session.get("lang", "ar"))
        return send_file(io.BytesIO(data), mimetype="application/pdf", download_name=f"Receipt-R-{pay_id:05d}.pdf")

    @app.route("/payments")
    def payments_list():
        db = get_db()
        month = request.args.get("month") or today_kw().strftime("%Y-%m")
        rows = db.execute("""SELECT pay.*, c.name AS client_name,
                               (SELECT GROUP_CONCAT(p.number || ' ' || p.name, '; ') FROM allocations a JOIN projects p ON p.id = a.project_id WHERE a.payment_id = pay.id) AS projects,
                               pay.amount_fils - COALESCE((SELECT SUM(amount_fils) FROM allocations a WHERE a.payment_id = pay.id), 0) AS unallocated
                             FROM payments pay JOIN clients c ON c.id = pay.client_id
                             WHERE substr(pay.date_received, 1, 7) = ? ORDER BY pay.date_received, pay.id""", (month,)).fetchall()
        total = sum(r["amount_fils"] for r in rows)
        y, mth = map(int, month.split("-"))
        prev = f"{y - 1}-12" if mth == 1 else f"{y}-{mth - 1:02d}"
        nxt = f"{y + 1}-01" if mth == 12 else f"{y}-{mth + 1:02d}"
        return render_template("payments.html", rows=rows, month=month, total=total, prev=prev, nxt=nxt)

    # ------------------------------------------------------------ clients
    @app.route("/clients")
    def clients_list():
        db = get_db()
        q = logic.normalise_search(request.args.get("q", ""))
        out = []
        for c in db.execute("SELECT * FROM clients ORDER BY name"):
            if q and q not in logic.normalise_search(" ".join(str(c[k] or "") for k in ("name", "contact_person", "phone", "email"))):
                continue
            projects = db.execute("SELECT * FROM projects WHERE client_id = ?", (c["id"],)).fetchall()
            fee = rec = bal = credit = 0
            pending = 0
            for p in projects:
                m = logic.project_money(db, p)
                rec += m["received"]
                if m["fee"] is None:
                    pending += 1
                    continue
                fee += m["fee"]
                if m["balance"] > 0:
                    bal += m["balance"]
                else:
                    credit += -m["balance"]
            unalloc = db.execute("""SELECT COALESCE(SUM(pay.amount_fils - COALESCE((SELECT SUM(a.amount_fils) FROM allocations a WHERE a.payment_id = pay.id),0)),0) AS u
                                    FROM payments pay WHERE pay.client_id = ?""", (c["id"],)).fetchone()["u"]
            out.append(dict(c=c, n=len(projects), fee=fee, rec=rec, bal=bal, credit=credit, pending=pending, unalloc=unalloc))
        return render_template("clients.html", rows=out, q=request.args.get("q", ""))

    CLIENT_SPEC = dict(name=text, contact_person=text, phone=text, whatsapp=text, email=text, language=text, notes=text)

    @app.route("/clients/new", methods=["GET", "POST"])
    @app.route("/clients/<int:cid>/edit", methods=["GET", "POST"])
    def client_form(cid=None):
        db = get_db()
        c = client_or_404(cid) if cid else None
        errors = {}
        form = request.form if request.method == "POST" else (dict(c) if c else {})
        if request.method == "POST":
            vals, errors = parse_fields(request.form, CLIENT_SPEC)
            if not vals["name"]:
                errors["name"] = "Client name is required"
            vals["language"] = vals["language"] if vals["language"] in ("ar", "en") else "ar"
            if not errors:
                now = now_iso()
                if c:
                    db.execute("UPDATE clients SET name=?, contact_person=?, phone=?, whatsapp=?, email=?, language=?, notes=?, updated_at=? WHERE id=?",
                               list(vals.values()) + [now, cid])
                    logic.log(db, "client_updated", vals["name"], client_id=cid, before={k: c[k] for k in vals}, after=vals)
                else:
                    cid = db.execute("INSERT INTO clients(name, contact_person, phone, whatsapp, email, language, notes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                                     list(vals.values()) + [now, now]).lastrowid
                    logic.log(db, "client_created", vals["name"], client_id=cid)
                db.commit()
                return redirect(url_for("client_view", cid=cid))
        return render_template("client_form.html", c=c, form=form, errors=errors)

    @app.route("/clients/<int:cid>")
    def client_view(cid):
        db = get_db()
        c = client_or_404(cid)
        projects = []
        for p in db.execute("SELECT * FROM projects WHERE client_id = ? ORDER BY id DESC", (cid,)):
            projects.append(dict(p=p, money=logic.project_money(db, p), sub=logic.submission_state(db, p)))
        payments = db.execute("""SELECT pay.*, pay.amount_fils - COALESCE((SELECT SUM(a.amount_fils) FROM allocations a WHERE a.payment_id = pay.id),0) AS unallocated
                                 FROM payments pay WHERE client_id = ? ORDER BY date_received DESC""", (cid,)).fetchall()
        quotes = db.execute("SELECT * FROM quotations WHERE client_id = ? ORDER BY number DESC, revision DESC", (cid,)).fetchall()
        rules = db.execute("""SELECT r.*, rv.text FROM rules r JOIN rule_revisions rv ON rv.rule_id = r.id AND rv.revision = r.current_revision
                              WHERE r.client_id = ? AND r.scope = 'client' ORDER BY r.id""", (cid,)).fetchall()
        return render_template("client.html", c=c, projects=projects, payments=payments, quotes=quotes, rules=rules,
                               new_key=new_uid(), today=today_kw().isoformat())

    @app.route("/clients/<int:cid>/receipt", methods=["POST"])
    def client_receipt(cid):
        """One receipt covering several projects of the same client, with explicit allocations."""
        db = get_db()
        c = client_or_404(cid)
        back = url_for("client_view", cid=cid)
        try:
            amount = parse_kwd(request.form.get("amount"))
            dr = parse_date(request.form.get("date_received"))
            allocs = []
            for p in db.execute("SELECT id FROM projects WHERE client_id = ?", (cid,)):
                a = parse_kwd(request.form.get(f"alloc_{p['id']}"))
                if a:
                    allocs.append((p["id"], a))
        except ValueError as e:
            flash(str(e), "error")
            return redirect(back)
        if not amount or amount <= 0 or not dr:
            flash("Enter the amount and the date received", "error")
            return redirect(back)
        if sum(a for _, a in allocs) > amount:
            flash("Allocations add up to more than the amount received.", "error")
            return redirect(back)
        pay_id = _record_payment(db, cid, dr, amount, text(request.form.get("method")), text(request.form.get("reference")),
                                 text(request.form.get("payer")), text(request.form.get("notes")),
                                 request.form.get("request_key") or new_uid(), allocs)
        if pay_id is None:
            flash("Already recorded once.", "ok")
            return redirect(back)
        un = amount - sum(a for _, a in allocs)
        logic.log(db, "receipt_recorded", f"R-{pay_id:05d} {fmt_kwd(amount)} KWD on {dr}; unallocated {fmt_kwd(un)}", client_id=cid)
        for pid, a in allocs:
            logic.log(db, "payment_recorded", f"{fmt_kwd(a)} KWD allocated from receipt R-{pay_id:05d} ({dr})",
                      project_id=pid, client_id=cid)
        db.commit()
        flash(f"Receipt recorded. Unallocated: {fmt_kwd(un)} KWD", "ok")
        return redirect(back)

    # ------------------------------------------------------------ quotations
    def _quote_items_from_form(form):
        items, errs = [], []
        descs, scopes, amounts = form.getlist("item_desc"), form.getlist("item_scope"), form.getlist("item_amount")
        for i, desc in enumerate(descs):
            desc = (desc or "").strip()
            amt_raw = amounts[i] if i < len(amounts) else ""
            if not desc and not (amt_raw or "").strip():
                continue
            try:
                amt = parse_kwd(amt_raw) or 0
            except ValueError as e:
                errs.append(f"Line {i + 1}: {e}")
                amt = 0
            items.append(dict(description=desc or "-", scope=scopes[i] if i < len(scopes) else "", amount_fils=amt))
        return items, errs

    def _quote_defaults(db, project, client, lang):
        s = get_settings(db)
        scope = logic.project_scope(project) if project else []
        items = [dict(description=(f"حصر كميات {logic.scope_label(k, 'ar')} وإعداد جدول الكميات" if lang == "ar"
                                   else f"{logic.scope_label(k)} quantity takeoff and BOQ"), scope=k, amount_fils=0)
                 for k in scope] or [dict(description="", scope="", amount_fils=0)]
        loc = building = ""
        if project:
            parts = [project["area"], ("قطعة " if lang == "ar" else "Block ") + project["block"] if project["block"] else None,
                     ("شارع " if lang == "ar" else "Street ") + project["street"] if project["street"] else None,
                     ("قسيمة " if lang == "ar" else "Plot ") + project["plot"] if project["plot"] else None]
            loc = "، ".join(x for x in parts if x) if lang == "ar" else ", ".join(x for x in parts if x)
            floors_txt = floors.count_explanation(project["basement_count"], project["upper_floor_count"],
                                                  project["has_roof"], project["roof_structures"])
            building = f"{project['building_type'] or ''} - {floors_txt}".strip(" -")
        return dict(items=items, scope=scope, location_text=loc, building_description=building,
                    validity_days=int(s.get("default_validity_days") or 14),
                    payment_terms=s.get("default_payment_terms_ar" if lang == "ar" else "default_payment_terms"),
                    exclusions=project["exclusions"] if project else "", delivery_period="", notes="")

    @app.route("/quotations")
    def quotations_list():
        db = get_db()
        st = request.args.get("status", "")
        sql = """SELECT q.*, c.name AS client_name, p.number AS project_number, p.name AS project_name
                 FROM quotations q JOIN clients c ON c.id = q.client_id LEFT JOIN projects p ON p.id = q.project_id"""
        rows = db.execute(sql + (" WHERE q.status = ?" if st else "") + " ORDER BY q.number DESC, q.revision DESC",
                          (st,) if st else ()).fetchall()
        return render_template("quotations.html", rows=rows, st=st, totals={r["id"]: _quote_total(r) for r in rows})

    def _quote_total(q):
        items = json.loads(q["items"] or "[]")
        return sum(i["amount_fils"] for i in items) - (q["discount_fils"] or 0)

    @app.route("/quotations/new", methods=["GET", "POST"])
    def quotation_new():
        db = get_db()
        pid = request.values.get("project_id", type=int)
        cid = request.values.get("client_id", type=int)
        project = project_or_404(pid) if pid else None
        client = client_or_404(project["client_id"] if project else cid) if (project or cid) else None
        if not client:
            flash("Open a client or project and choose New Quotation from there.", "error")
            return redirect(url_for("clients_list"))
        lang = request.values.get("language") or client["language"] or "ar"
        if request.method == "POST":
            return _quote_save(db, None, client, project)
        defaults = _quote_defaults(db, project, client, lang)
        return render_template("quotation_form.html", q=None, client=client, project=project, lang_q=lang,
                               v=defaults, errors=[], today=today_kw().isoformat())

    def _quote_save(db, q, client, project):
        f = request.form
        items, errs = _quote_items_from_form(f)
        try:
            discount = parse_kwd(f.get("discount")) or 0
            qdate = parse_date(f.get("quote_date")) or today_kw().isoformat()
            validity = parse_int(f.get("validity_days"))
        except ValueError as e:
            errs.append(str(e))
            discount, qdate, validity = 0, today_kw().isoformat(), None
        if not items:
            errs.append("Add at least one service line")
        subtotal = sum(i["amount_fils"] for i in items)
        if discount > subtotal:
            errs.append("Discount is larger than the subtotal")
        lang = f.get("language") if f.get("language") in ("ar", "en") else "ar"
        vals = dict(items=items, scope=[i["scope"] for i in items if i["scope"]], location_text=f.get("location_text", ""),
                    building_description=f.get("building_description", ""), validity_days=validity,
                    payment_terms=f.get("payment_terms", ""), exclusions=f.get("exclusions", ""),
                    delivery_period=f.get("delivery_period", ""), notes=f.get("notes", ""), discount_fils=discount)
        if errs:
            return render_template("quotation_form.html", q=q, client=client, project=project, lang_q=lang, v=vals,
                                   errors=errs, today=qdate)
        now = now_iso()
        cols = dict(language=lang, quote_date=qdate, validity_days=validity, delivery_period=text(vals["delivery_period"]),
                    payment_terms=text(vals["payment_terms"]), exclusions=text(vals["exclusions"]), notes=text(vals["notes"]),
                    building_description=text(vals["building_description"]), location_text=text(vals["location_text"]),
                    discount_fils=discount, items=json.dumps(items, ensure_ascii=False), scope=json.dumps(vals["scope"]))
        if q is None:
            number = logic.next_quote_number(db)
            qid = db.execute(f"INSERT INTO quotations(number, revision, client_id, project_id, status, created_at, updated_at, "
                             f"{', '.join(cols)}) VALUES (?,?,?,?, 'draft', ?, ?, {','.join('?' * len(cols))})",
                             [number, 0, client["id"], project["id"] if project else None, now, now] + list(cols.values())).lastrowid
            logic.log(db, "quotation_created", f"{number} Rev 0 draft, {fmt_kwd(subtotal - discount)} KWD",
                      project_id=project["id"] if project else None, client_id=client["id"])
        else:
            if q["status"] != "draft":
                abort(409)
            try:
                version = int(f.get("version", "0"))
            except ValueError:
                version = 0
            sets = ", ".join(f"{k} = ?" for k in cols)
            cur = db.execute(f"UPDATE quotations SET {sets}, version = version + 1, updated_at = ? WHERE id = ? AND version = ? AND status = 'draft'",
                             list(cols.values()) + [now, q["id"], version])
            if cur.rowcount == 0:
                db.rollback()
                flash("This quotation changed in another tab or was already sent. Nothing was overwritten.", "error")
                return redirect(url_for("quotation_view", qid=q["id"]))
            qid = q["id"]
            logic.log(db, "quotation_edited", f"{q['number']} Rev {q['revision']} draft edited, {fmt_kwd(subtotal - discount)} KWD",
                      project_id=q["project_id"], client_id=client["id"])
        db.commit()
        return redirect(url_for("quotation_view", qid=qid))

    @app.route("/quotations/<int:qid>")
    def quotation_view(qid):
        db = get_db()
        q = _get_or_404("SELECT * FROM quotations WHERE id = ?", (qid,))
        client = client_or_404(q["client_id"])
        project = project_or_404(q["project_id"]) if q["project_id"] else None
        revisions = db.execute("SELECT * FROM quotations WHERE number = ? ORDER BY revision DESC", (q["number"],)).fetchall()
        client_projects = db.execute("SELECT id, number, name FROM projects WHERE client_id = ?", (client["id"],)).fetchall()
        items = json.loads(q["items"])
        return render_template("quotation_view.html", q=q, client=client, project=project, items=items,
                               total=_quote_total(q), revisions=revisions, client_projects=client_projects)

    @app.route("/quotations/<int:qid>/edit", methods=["GET", "POST"])
    def quotation_edit(qid):
        db = get_db()
        q = _get_or_404("SELECT * FROM quotations WHERE id = ?", (qid,))
        client = client_or_404(q["client_id"])
        project = project_or_404(q["project_id"]) if q["project_id"] else None
        if q["status"] != "draft":
            flash("A sent quotation cannot be edited, so the copy the client received stays exact. Create a revision instead.", "error")
            return redirect(url_for("quotation_view", qid=qid))
        if request.method == "POST":
            return _quote_save(db, q, client, project)
        v = dict(q)
        v["items"] = json.loads(q["items"])
        return render_template("quotation_form.html", q=q, client=client, project=project, lang_q=q["language"], v=v,
                               errors=[], today=q["quote_date"])

    def _pdf_settings():
        s = dict(get_settings())
        if s.get("logo_stored_name"):
            s["_logo_path"] = file_path(s["logo_stored_name"])
        return s

    def _render_quote_pdf(db, q):
        client = client_or_404(q["client_id"])
        project = project_or_404(q["project_id"]) if q["project_id"] else None
        return pdfs.quotation_pdf(_pdf_settings(), q, client, project, json.loads(q["items"]))

    @app.route("/quotations/<int:qid>/pdf")
    def quotation_pdf(qid):
        db = get_db()
        q = _get_or_404("SELECT * FROM quotations WHERE id = ?", (qid,))
        name = f"{q['number']}-Rev{q['revision']}.pdf"
        if q["pdf_stored_name"]:  # exact copy that was sent
            return send_file(file_path(q["pdf_stored_name"]), mimetype="application/pdf", download_name=name)
        return send_file(io.BytesIO(_render_quote_pdf(db, q)), mimetype="application/pdf", download_name="DRAFT-" + name)

    def _freeze_quote(db, q):
        if q["pdf_stored_name"]:
            return q
        data = _render_quote_pdf(db, q)
        stored, sha = write_file(data)
        db.execute("UPDATE quotations SET status = 'sent', sent_at = ?, pdf_stored_name = ?, pdf_sha256 = ?, version = version + 1 "
                   "WHERE id = ? AND status = 'draft'", (now_iso(), stored, sha, q["id"]))
        logic.log(db, "quotation_sent", f"{q['number']} Rev {q['revision']} issued ({fmt_kwd(_quote_total(q))} KWD); PDF frozen sha256 {sha[:12]}",
                  project_id=q["project_id"], client_id=q["client_id"])
        return db.execute("SELECT * FROM quotations WHERE id = ?", (q["id"],)).fetchone()

    @app.route("/quotations/<int:qid>/status", methods=["POST"])
    def quotation_status(qid):
        db = get_db()
        q = _get_or_404("SELECT * FROM quotations WHERE id = ?", (qid,))
        action = request.form.get("action")
        if action == "sent":
            if q["status"] != "draft":
                abort(409)
            _freeze_quote(db, q)
        elif action == "revise":
            if q["status"] == "draft":
                flash("Drafts can be edited directly.", "error")
                return redirect(url_for("quotation_view", qid=qid))
            latest = db.execute("SELECT MAX(revision) AS r FROM quotations WHERE number = ?", (q["number"],)).fetchone()["r"]
            now = now_iso()
            cols = ["client_id", "project_id", "language", "validity_days", "delivery_period", "payment_terms", "exclusions",
                    "notes", "building_description", "location_text", "discount_fils", "items", "scope"]
            new_id = db.execute(f"INSERT INTO quotations(number, revision, status, quote_date, created_at, updated_at, {', '.join(cols)}) "
                                f"SELECT number, ?, 'draft', ?, ?, ?, {', '.join(cols)} FROM quotations WHERE id = ?",
                                (latest + 1, today_kw().isoformat(), now, now, qid)).lastrowid
            if q["status"] == "sent":
                db.execute("UPDATE quotations SET status = 'superseded' WHERE id = ?", (qid,))
            logic.log(db, "quotation_revised", f"{q['number']} Rev {latest + 1} created from Rev {q['revision']}",
                      project_id=q["project_id"], client_id=q["client_id"])
            db.commit()
            return redirect(url_for("quotation_edit", qid=new_id))
        elif action == "accept":
            if q["status"] not in ("sent", "draft"):
                flash("Only a sent quotation can be accepted.", "error")
                return redirect(url_for("quotation_view", qid=qid))
            q = _freeze_quote(db, q)  # accepting a draft (e.g. agreed on WhatsApp) still freezes the exact terms
            pid = q["project_id"] or request.form.get("project_id", type=int)
            if not pid and request.form.get("create_project"):
                number = logic.next_project_number(db)
                now = now_iso()
                db.execute("INSERT INTO projects(uid, number, client_id, name, scope, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                           (new_uid(), number, q["client_id"], request.form.get("project_name") or q["building_description"] or number,
                            q["scope"], now, now))
                pid = db.execute("SELECT id FROM projects WHERE number = ?", (number,)).fetchone()["id"]
                logic.log(db, "project_created", f"{number} from accepted quotation {q['number']}", project_id=pid, client_id=q["client_id"])
            if not pid:
                db.rollback()
                flash("Choose the project this quotation is for (or create one).", "error")
                return redirect(url_for("quotation_view", qid=qid))
            p = project_or_404(pid)
            if p["client_id"] != q["client_id"]:
                abort(400)
            total = _quote_total(q)
            # only one accepted revision per project: earlier acceptances become superseded
            db.execute("UPDATE quotations SET status = 'superseded' WHERE project_id = ? AND status = 'accepted' AND id <> ?", (pid, qid))
            db.execute("UPDATE quotations SET status = 'accepted', accepted_at = ?, project_id = ? WHERE id = ?", (now_iso(), pid, qid))
            scope = json.loads(q["scope"]) or json.loads(p["scope"])
            db.execute("UPDATE projects SET agreed_fee_fils = ?, fee_basis = 'quotation', fee_basis_note = ?, accepted_quotation_id = ?, "
                       "scope = ?, version = version + 1, updated_at = ? WHERE id = ?",
                       (total, f"{q['number']} Rev {q['revision']}", qid, json.dumps(scope), now_iso(), pid))
            logic.log(db, "quotation_accepted", f"{q['number']} Rev {q['revision']} accepted: agreed fee {fmt_kwd(total)} KWD",
                      project_id=pid, client_id=q["client_id"],
                      before=fmt_kwd(p["agreed_fee_fils"]) if p["agreed_fee_fils"] is not None else None, after=fmt_kwd(total))
        elif action in ("rejected", "expired"):
            if q["status"] not in ("sent", "draft"):
                abort(409)
            db.execute("UPDATE quotations SET status = ? WHERE id = ?", (action, qid))
            logic.log(db, f"quotation_{action}", f"{q['number']} Rev {q['revision']}", project_id=q["project_id"], client_id=q["client_id"])
        else:
            abort(400)
        db.commit()
        return redirect(url_for("quotation_view", qid=qid))

    # ------------------------------------------------------------ emails
    @app.route("/projects/<int:pid>/email/<kind>", methods=["GET", "POST"])
    def email_draft(pid, kind):
        if kind not in emails.KINDS:
            abort(404)
        db = get_db()
        p = project_or_404(pid)
        client = client_or_404(p["client_id"])
        lang = request.values.get("language") or client["language"] or "ar"
        s = get_settings(db)
        quotes = db.execute("SELECT * FROM quotations WHERE project_id = ? ORDER BY number DESC, revision DESC", (pid,)).fetchall()
        outgoing = db.execute("SELECT * FROM drawings WHERE project_id = ? AND direction = 'outgoing' ORDER BY uploaded_at DESC", (pid,)).fetchall()
        money = logic.project_money(db, p)
        sub = logic.submission_state(db, p)
        qsel = None
        if kind == "quotation":
            qid = request.values.get("quotation_id", type=int)
            qsel = next((q for q in quotes if q["id"] == qid), None) or (quotes[0] if quotes else None)
        if request.method == "GET":
            qd = dict(qsel) if qsel else None
            if qd:
                qd["_total"] = _quote_total(qsel)
            scope_text = ", ".join(logic.scope_label(x, lang) for x in sub["delivered"] or logic.project_scope(p))
            dft = emails.draft(kind, lang, s, p, client, money=money, quotation=qd, scope_text=scope_text)
            att = []
            if kind == "boq" and outgoing:
                att = [str(outgoing[0]["id"])]
            return render_template("email.html", p=p, client=client, kind=kind, lang_e=lang, dft=dft, quotes=quotes,
                                   outgoing=outgoing, qsel=qsel, att=att, money=money, smtp=emails.smtp_configured())
        # POST
        to = (request.form.get("to") or "").strip()
        subject = request.form.get("subject") or ""
        body = request.form.get("body") or ""
        attachments, names = [], []
        if kind == "quotation" and qsel:
            q = qsel if qsel["pdf_stored_name"] else None
            if request.form.get("action") in ("mark_sent", "smtp") and qsel["status"] == "draft":
                q = _freeze_quote(db, qsel)
            elif not q:
                data = _render_quote_pdf(db, qsel)
                tmp = os.path.join(current_app.config["FILES_DIR"], "preview-" + uuid.uuid4().hex)
                with open(tmp, "wb") as fh:
                    fh.write(data)
                attachments.append((f"DRAFT-{qsel['number']}-Rev{qsel['revision']}.pdf", tmp))
            if q:
                attachments.append((f"{q['number']}-Rev{q['revision']}.pdf", file_path(q["pdf_stored_name"])))
        for did in request.form.getlist("attach"):
            d = db.execute("SELECT * FROM drawings WHERE id = ? AND project_id = ?", (did, pid)).fetchone()
            if d:
                attachments.append((d["original_name"], file_path(d["stored_name"])))
        names = [a[0] for a in attachments]
        action = request.form.get("action")
        if action == "eml":
            msg = emails.build_eml(s, to, subject, body, attachments)
            return Response(msg.as_bytes(), mimetype="message/rfc822",
                            headers={"Content-Disposition": f"attachment; filename={p['number']}-{kind}.eml"})
        if action in ("mark_sent", "smtp"):
            if not to:
                flash("Add the recipient email first.", "error")
                return redirect(url_for("email_draft", pid=pid, kind=kind, language=lang))
            via = "confirmed by user"
            if action == "smtp":
                if not emails.smtp_configured():
                    abort(400)
                try:
                    emails.smtp_send(emails.build_eml(s, to, subject, body, attachments))
                    via = "SMTP"
                except Exception as e:  # nothing is logged when sending fails
                    db.rollback()
                    flash(f"Email NOT sent: {e}", "error")
                    return redirect(url_for("email_draft", pid=pid, kind=kind, language=lang))
            db.execute("INSERT INTO emails(project_id, kind, language, to_addr, subject, body, attachments, status, sent_via, sent_at) "
                       "VALUES (?,?,?,?,?,?,?, 'sent', ?, ?)", (pid, kind, lang, to, subject, body, json.dumps(names), via, now_iso()))
            logic.log(db, "email_sent", f"{emails.KINDS[kind][0]} to {to} ({via}); attachments: {', '.join(names) or 'none'}",
                      project_id=pid, client_id=p["client_id"])
            db.commit()
            flash("Email recorded in the activity log", "ok")
            return redirect(url_for("project_view", pid=pid, tab="activity"))
        abort(400)

    # ------------------------------------------------------------ exports
    @app.route("/projects/<int:pid>/export/revit.json")
    def export_revit(pid):
        db = get_db()
        p = project_or_404(pid)
        client, levels, grids, drawings = project_bundle(db, p)
        body, sha = revit_export.build_export(p, client["name"], levels, grids, drawings)
        logic.log(db, "revit_export", f"Revit 2022 export v{revit_export.SCHEMA_VERSION}: "
                  f"{len(json.loads(body)['levels'])} levels, {len(json.loads(body)['grids'])} grids (sha256 {sha[:12]})",
                  project_id=pid, client_id=p["client_id"])
        db.commit()
        return Response(body, mimetype="application/json",
                        headers={"Content-Disposition": f"attachment; filename={p['number']}-revit2022.json"})

    @app.route("/projects/<int:pid>/export/boq.xlsx")
    def export_xlsx(pid):
        db = get_db()
        p = project_or_404(pid)
        client, levels, grids, drawings = project_bundle(db, p)
        data = excel_export.build_workbook(get_settings(db), p, client, levels, grids, drawings, logic.project_scope(p),
                                           logic.applicable_rules(db, p))
        logic.log(db, "excel_export", "Intake + floor table + BOQ worksheet exported", project_id=pid, client_id=p["client_id"])
        db.commit()
        return send_file(io.BytesIO(data), download_name=f"{p['number']}-BOQ-setup.xlsx",
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # ------------------------------------------------------------ Archive | الأرشيف
    @app.route("/archive", methods=["GET", "POST"])
    def archive_page():
        db = get_db()
        if request.method == "POST":
            wording = (request.form.get("text") or "").strip()
            pid = request.form.get("project_id", type=int)
            p = project_or_404(pid) if pid else None
            cid = request.form.get("client_id", type=int) or (p["client_id"] if p else None)
            back = request.form.get("back") or url_for("archive_page")
            if not wording:
                flash("Write the instruction first.", "error")
                return redirect(back)
            rid, scope, status, why, created = archive.save_rule(
                db, wording, project=p, client_id=cid, scope=request.form.get("scope") or "auto",
                topic=request.form.get("topic") or None, kind=request.form.get("kind") or "instruction",
                trade=request.form.get("trade") or None, source_ref=text(request.form.get("source_ref")),
                request_key=request.form.get("request_key") or None, author=session.get("user"))
            db.commit()
            label = logic.RULE_SCOPES[scope][0]
            flash(f"Archive #{rid} saved: {label}, {logic.RULE_STATUS[status][0]} ({why})." if created
                  else f"Archive #{rid} was already saved; nothing duplicated.", "ok")
            return redirect(back)
        f = request.args
        rows = archive.search(db, q=f.get("q", ""), topic=f.get("topic", ""), scope=f.get("scope", ""),
                              status=f.get("status", ""), client_id=f.get("client_id", type=int),
                              include_retired=bool(f.get("retired")))
        clients = db.execute("SELECT id, name FROM clients ORDER BY name").fetchall()
        return render_template("archive.html", rules=rows, clients=clients, f=f, new_key=new_uid())

    @app.route("/archive/<int:rid>", methods=["GET", "POST"])
    def archive_rule(rid):
        db = get_db()
        r = _get_or_404("SELECT * FROM rules WHERE id = ?", (rid,))
        if request.method == "POST":
            action = request.form.get("action")
            if action == "revise":
                txt = (request.form.get("text") or "").strip()
                if txt:
                    rev = archive.revise_rule(db, rid, txt, text(request.form.get("reason")), session.get("user"),
                                              request.form.get("expected_revision", type=int))
                    if rev is None:
                        flash("This rule was revised meanwhile. Reload and check the latest text.", "error")
            elif action == "toggle":
                db.execute("UPDATE rules SET active = 1 - active, updated_at = ? WHERE id = ?", (now_iso(), rid))
                logic.log(db, "rule_retired" if r["active"] else "rule_restored", f"Archive #{rid}",
                          project_id=r["project_id"], client_id=r["client_id"])
            elif action == "confirm":
                scope = request.form.get("scope") or r["scope"]
                if scope not in logic.RULE_SCOPES:
                    abort(400)
                proj = r["project_id"] if scope == "project" else None
                cli = (r["client_id"] or (project_or_404(r["project_id"])["client_id"] if r["project_id"] else None)) if scope == "client" else None
                if (scope == "project" and not proj) or (scope == "client" and not cli):
                    flash("That scope needs a project or client link.", "error")
                    return redirect(url_for("archive_rule", rid=rid))
                db.execute("UPDATE rules SET status = 'approved', scope = ?, project_id = ?, client_id = ?, updated_at = ? WHERE id = ?",
                           (scope, proj, cli, now_iso(), rid))
                logic.log(db, "rule_confirmed", f"Archive #{rid} approved as {scope}", project_id=r["project_id"], client_id=cli)
            elif action == "meta":
                db.execute("UPDATE rules SET topic = ?, trade = ?, source_ref = ?, updated_at = ? WHERE id = ?",
                           (request.form.get("topic") or None, request.form.get("trade") or None,
                            text(request.form.get("source_ref")), now_iso(), rid))
            db.commit()
            return redirect(url_for("archive_rule", rid=rid))
        revs = db.execute("SELECT * FROM rule_revisions WHERE rule_id = ? ORDER BY revision DESC", (rid,)).fetchall()
        used = []
        for dl in db.execute("SELECT dl.*, p.number FROM deliveries dl JOIN projects p ON p.id = dl.project_id ORDER BY dl.delivery_date DESC"):
            for u in json.loads(dl["rules_used"]):
                if u["rule_id"] == rid:
                    used.append(dict(dl=dl, revision=u["revision"]))
        cur = db.execute("SELECT r.*, rv.text FROM rules r JOIN rule_revisions rv ON rv.rule_id = r.id AND rv.revision = r.current_revision WHERE r.id = ?", (rid,)).fetchone()
        project = project_or_404(r["project_id"]) if r["project_id"] else None
        client = client_or_404(r["client_id"]) if r["client_id"] else None
        return render_template("archive_rule.html", r=cur, revs=revs, used=used, project=project, client=client)

    @app.route("/archive/export.json")
    def archive_export():
        body = archive.export_json(get_db())
        return Response(body, mimetype="application/json",
                        headers={"Content-Disposition": f"attachment; filename=base-archive-{today_kw().isoformat()}.json"})

    @app.route("/archive/rules.md")
    def archive_markdown():
        db = get_db()
        return Response(archive.markdown_summary(db, get_settings(db)), mimetype="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": "attachment; filename=base-archive-active-rules.md"})

    @app.route("/archive/import", methods=["POST"])
    def archive_import():
        db = get_db()
        f = request.files.get("file")
        payload = request.form.get("payload") or (f.read().decode("utf-8") if f and f.filename else "")
        try:
            plan = archive.plan_import(db, payload)
        except (ValueError, KeyError) as e:
            flash(f"Import failed: {e}", "error")
            return redirect(url_for("archive_page"))
        if request.form.get("apply"):
            counts = archive.apply_import(db, payload, session.get("user"))
            logic.log(db, "archive_import", json.dumps(counts))
            db.commit()
            flash("Imported: " + ", ".join(f"{k} {v}" for k, v in counts.items()), "ok")
            return redirect(url_for("archive_page"))
        return render_template("archive_import.html", plan=plan, payload=payload)
    # ------------------------------------------------------------ activity + settings
    @app.route("/activity")
    def activity():
        db = get_db()
        rows = db.execute("""SELECT a.*, p.number, p.name AS project_name, c.name AS client_name FROM activity a
                             LEFT JOIN projects p ON p.id = a.project_id LEFT JOIN clients c ON c.id = a.client_id
                             ORDER BY a.at DESC, a.id DESC LIMIT 300""").fetchall()
        return render_template("activity.html", rows=rows)

    SETTINGS_FIELDS = ["company_name", "company_name_ar", "company_email", "company_phone", "company_address",
                       "quotation_footer", "quotation_footer_ar", "number_prefix", "quote_prefix", "default_validity_days",
                       "default_payment_terms", "default_payment_terms_ar"]

    @app.route("/settings", methods=["GET", "POST"])
    def settings_page():
        db = get_db()
        if request.method == "POST":
            action = request.form.get("action", "save")
            if action == "password":
                u = db.execute("SELECT * FROM users WHERE username = ?", (session["user"],)).fetchone()
                pw = request.form.get("new_password") or ""
                if not check_password_hash(u["password_hash"], request.form.get("old_password") or ""):
                    flash("Current password is wrong", "error")
                elif len(pw) < 8:
                    flash("New password must be at least 8 characters", "error")
                else:
                    db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(pw), u["id"]))
                    logic.log(db, "password_changed", "")
                    db.commit()
                    flash("Password changed", "ok")
                return redirect(url_for("settings_page"))
            old = get_settings(db)
            changed = {}
            for k in SETTINGS_FIELDS:
                v = (request.form.get(k) or "").strip()
                if v != (old.get(k) or ""):
                    changed[k] = (old.get(k), v)
                    set_setting(db, k, v)
            presets_txt = request.form.get("scope_presets_text")
            if presets_txt is not None:
                presets = {}
                for line in presets_txt.splitlines():
                    if ":" in line:
                        name, items = line.split(":", 1)
                        keys = [i.strip() for i in items.split(",") if i.strip() in logic.SCOPES]
                        if name.strip() and keys:
                            presets[name.strip()] = keys
                set_setting(db, "scope_presets", json.dumps(presets, ensure_ascii=False))
            logo = request.files.get("logo")
            if logo and logo.filename:
                ext = os.path.splitext(logo.filename)[1].lower()
                if ext in (".png", ".jpg", ".jpeg"):
                    stored, _ = write_file(logo.read())
                    set_setting(db, "logo_stored_name", stored)
                    changed["logo"] = (None, logo.filename)
                else:
                    flash("Logo must be PNG or JPG", "error")
            if changed:
                logic.log(db, "settings_changed", ", ".join(changed), before={k: v[0] for k, v in changed.items()},
                          after={k: v[1] for k, v in changed.items()})
            db.commit()
            flash("Settings saved", "ok")
            return redirect(url_for("settings_page"))
        s = get_settings(db)
        presets = json.loads(s.get("scope_presets") or "{}")
        presets_text = "\n".join(f"{k}: {', '.join(v)}" for k, v in presets.items())
        return render_template("settings.html", s=s, presets_text=presets_text, fields=SETTINGS_FIELDS)

    @app.route("/settings/logo")
    def settings_logo():
        s = get_settings()
        if not s.get("logo_stored_name"):
            abort(404)
        return send_file(file_path(s["logo_stored_name"]))

    @app.errorhandler(400)
    @app.errorhandler(404)
    @app.errorhandler(409)
    def http_error(e):
        return render_template("error.html", e=e), e.code
