"""End-to-end test of BASE Design Office with a fictional Kuwait villa.

Run:  python -m pytest -q tests
Every step goes through the real HTTP routes (Flask test client), the real SQLite file and real file storage.
"""
import io
import json
import re
import subprocess
from datetime import date, timedelta

import pytest
from openpyxl import load_workbook

from app import create_app
from app.db import connect
from app.util import today_kw

CSRF = "test-token"


@pytest.fixture()
def env(tmp_path):
    app = create_app(str(tmp_path / "data"), testing=True)
    c = app.test_client()
    with c.session_transaction() as s:
        s["csrf"] = CSRF
    r = c.post("/setup", data=dict(csrf=CSRF, username="jalal", password="villa-test-123", password2="villa-test-123"))
    assert r.status_code == 302
    with c.session_transaction() as s:
        s["csrf"] = CSRF
    return app, c, tmp_path


def post(c, url, data=None, **kw):
    data = dict(data or {})
    data["csrf"] = CSRF
    return c.post(url, data=data, **kw)


def db(app):
    return connect(app.config["DATABASE"])


def project_row(app, pid):
    return db(app).execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()


def levels(app, pid):
    return db(app).execute("SELECT * FROM levels WHERE project_id = ? ORDER BY seq", (pid,)).fetchall()


def filter_numbers(c, flt):
    html = c.get(f"/?f={flt}").get_data(as_text=True)
    table = html.split("<tbody>", 1)[1].split("</tbody>", 1)[0] if "<tbody>" in html else ""
    return set(re.findall(r"BASE-\d{4}-\d{3}", table))


def create_villa(c, name, client_name, extra=None):
    data = {
        "new_client_name": client_name, "new_contact": "أبو خالد", "new_phone": "99887766",
        "new_email": "client@example.com", "name": name, "owner_name": "خالد العلي",
        "area": "صباح الأحمد", "block": "3", "street": "12", "plot": "120",
        "building_type": "villa", "plot_area_m2": "400", "basement_count": "1", "upper_floor_count": "2",
        "has_roof": "1", "roof_structures": "غرفة درج وخزانات", "typical_ftf_mm": "4.000", "gf_ffl_mm": "+0.600",
        "preset": "هيكل أسود + طابوق", "due_date": (today_kw() + timedelta(days=10)).isoformat(),
        "date_received": today_kw().isoformat(), "internal_notes": "SECRET-INTERNAL-NOTE",
        "request_key": f"key-{name}",
    }
    data.update(extra or {})
    r = post(c, "/projects/new", data)
    assert r.status_code == 302, r.get_data(as_text=True)[:2000]
    return int(r.headers["Location"].rstrip("/").split("/")[-1])


def test_full_villa_workflow(env):
    app, c, tmp = env
    year = today_kw().year

    # ---- 1. intake: client + name is enough, number generated, floors generated
    pid = create_villa(c, "فيلا الياسمين - صباح الأحمد", "شركة النخيل للمقاولات")
    p = project_row(app, pid)
    assert p["number"] == f"BASE-{year}-001"
    assert json.loads(p["scope"]) == ["concrete", "rebar", "blockwork"]
    assert p["agreed_fee_fils"] is None
    names = [l["name"] for l in levels(app, pid)]
    assert names == ["Basement", "Ground Floor", "First Floor", "Second Floor", "Roof", "Upper Roof"]
    ffl = {l["name"]: l["ffl_mm"] for l in levels(app, pid)}
    assert ffl == {"Basement": -3400, "Ground Floor": 600, "First Floor": 4600, "Second Floor": 8600,
                   "Roof": 12600, "Upper Roof": 16600}
    page = c.get(f"/projects/{pid}?tab=floors").get_data(as_text=True)
    assert "1 basement + ground + 2 upper = 4 floors (+ roof + roof structure = 6 levels)" in page
    assert "Pending agreement" in c.get(f"/projects/{pid}").get_data(as_text=True)

    # double-submitted New Project form does not create a second project
    r = post(c, "/projects/new", {"new_client_name": "x", "name": "فيلا الياسمين - صباح الأحمد",
                                  "request_key": db(app).execute("SELECT uid FROM projects WHERE id=?", (pid,)).fetchone()["uid"]})
    assert db(app).execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1

    # ---- 2. floor table: preview moves, fixed level preserved, blank != zero
    lv = levels(app, pid)

    def floor_form(rows, action, fixed=(), ftf_override=None, areas=None):
        data = {"action": action, "uid": [], "name": [], "kind": [], "ffl_mm": [], "ftf_mm": [], "area_m2": [],
                "typical_group": [], "slab_offset_mm": [], "clear_height_mm": [], "slab_thk_mm": [], "finish_mm": [],
                "drawing_ref": [], "notes": [], "fixed": [], "confirmed": []}
        for l in reversed(rows):  # the page lists the top level first
            data["uid"].append(l["uid"]); data["name"].append(l["name"]); data["kind"].append(l["kind"])
            data["ffl_mm"].append("" if l["ffl_mm"] is None else f"{l['ffl_mm'] / 1000:.3f}")
            h = (ftf_override or {}).get(l["name"], l["ftf_mm"])
            data["ftf_mm"].append("" if h is None else f"{h / 1000:.3f}")
            data["area_m2"].append(str((areas or {}).get(l["name"], "")))
            data["slab_offset_mm"].append("-0.100" if l["kind"] in ("ground", "upper", "roof") else "")
            for k in ("typical_group", "clear_height_mm", "slab_thk_mm", "finish_mm", "drawing_ref", "notes"):
                data[k].append("")
            if l["name"] in fixed or (l["fixed"] and not fixed):
                data["fixed"].append(l["uid"])
            data["confirmed"].append(l["uid"])
        return data

    r = post(c, f"/projects/{pid}/floors", floor_form(lv, "preview", fixed=("Ground Floor", "Roof"),
                                                      ftf_override={"Ground Floor": 4200}))
    html = r.get_data(as_text=True)
    assert "These levels will move" in html
    assert "First Floor" in html and "+4.800" in html          # GF +0.600 + 4.200
    assert "Roof is fixed at +12.600 m" in html                 # fixed level preserved, warning shown
    assert ffl == {l["name"]: l["ffl_mm"] for l in levels(app, pid)}  # preview did not save
    # save with confirmed elevations, areas and slab offsets (GF→1F 4.000 so 1F = +4.600)
    areas = {"Basement": 300, "Ground Floor": 280, "First Floor": 280, "Second Floor": 280, "Roof": 60}
    r = post(c, f"/projects/{pid}/floors", floor_form(lv, "save", fixed=("Ground Floor",), areas=areas))
    assert r.status_code == 302
    lv = levels(app, pid)
    assert {l["name"]: l["ffl_mm"] for l in lv}["First Floor"] == 4600
    assert {l["name"]: l["area_m2"] for l in lv}["Upper Roof"] is None   # blank stays blank
    assert all(l["confirmed"] for l in lv)

    # duplicate a typical floor: one physical floor added once, levels above move up
    dup_idx = [l["name"] for l in lv].index("Second Floor")
    r = post(c, f"/projects/{pid}/floors", floor_form(lv, f"dup:{dup_idx}", fixed=("Ground Floor",), areas=areas))
    html = r.get_data(as_text=True)
    assert "Second Floor (2)" in html and "+16.600" in html    # Roof moves from +12.600 to +16.600 in preview
    assert len(levels(app, pid)) == 6                            # still only a preview

    # ---- 3. grids (straight, metres from internal origin)
    gdata = {"uid": ["new"] * 5, "name": ["A", "B", "1", "2", "C"], "x1": ["0", "12", "-1.5", "-1.5", "5"],
             "y1": ["-1.5", "-1.5", "0", "15", ""], "x2": ["0", "12", "13.5", "13.5", "5"],
             "y2": ["16.5", "16.5", "0", "15", "10"], "notes": [""] * 5}
    assert post(c, f"/projects/{pid}/grids", gdata).status_code == 302
    assert db(app).execute("SELECT COUNT(*) FROM grids WHERE project_id=?", (pid,)).fetchone()[0] == 5

    # ---- 4. Archive rules: project-only and company
    post(c, "/archive", {"text": "لهالمشروع بس: اللياسة الداخلية لحد أسفل الجسور لأن في سقف معلق", "project_id": pid,
                         "scope": "auto", "topic": "finishes", "request_key": "rule-1"})
    post(c, "/archive", {"text": "لهالمشروع بس: اللياسة الداخلية لحد أسفل الجسور لأن في سقف معلق", "project_id": pid,
                         "scope": "auto", "topic": "finishes", "request_key": "rule-1"})   # retry: no duplicate
    post(c, "/archive", {"text": "حساب القص على قضبان ١٢ متر وتسجيل الهالك الفعلي بدل ١٠٪ ثابتة. اعتمد هالطريقة لكل المشاريع",
                         "project_id": pid, "scope": "auto", "topic": "rebar", "request_key": "rule-2"})
    post(c, "/archive", {"text": "use the client's Excel layout", "project_id": pid, "scope": "auto", "request_key": "rule-3"})
    rules = db(app).execute("SELECT id, scope, status, project_id FROM rules ORDER BY id").fetchall()
    assert [(r["scope"], r["status"]) for r in rules] == [("project", "approved"), ("company", "approved"), ("project", "draft")]
    proj_rule, company_rule, draft_rule = (r["id"] for r in rules)
    floors_page = c.get(f"/projects/{pid}?tab=floors").get_data(as_text=True)
    assert "Project-only rule for this job" in floors_page and "Company rule: applies to every project" in floors_page
    assert "use the client" not in floors_page            # draft is not applied
    assert "الهالك" in c.get("/archive?q=الهالك").get_data(as_text=True)   # Arabic search

    # ---- 5. quotation: 30.000 KWD, Arabic, sent (frozen), edit refused, accepted
    r = post(c, "/quotations/new", {"project_id": pid, "language": "ar", "quote_date": today_kw().isoformat(),
                                    "validity_days": "14", "delivery_period": "٧ أيام عمل",
                                    "item_desc": ["حصر كميات الخرسانة", "حصر حديد التسليح وجدول التفريد", "حصر الطابوق"],
                                    "item_scope": ["concrete", "rebar", "blockwork"],
                                    "item_amount": ["10.000", "12.500", "7.500"],
                                    "payment_terms": "٥٠٪ عند الموافقة و ٥٠٪ عند التسليم", "exclusions": "التشطيبات"})
    qid = int(r.headers["Location"].split("/")[-1])
    q = db(app).execute("SELECT * FROM quotations WHERE id=?", (qid,)).fetchone()
    assert q["number"] == f"QT-{year}-001" and q["status"] == "draft"
    post(c, f"/quotations/{qid}/status", {"action": "sent"})
    q = db(app).execute("SELECT * FROM quotations WHERE id=?", (qid,)).fetchone()
    assert q["status"] == "sent" and q["pdf_stored_name"]
    assert "1" in re.findall(r'awaiting reply.*?<div class="v">(\d+)', c.get("/?f=all").get_data(as_text=True), re.S)[0]
    frozen = c.get(f"/quotations/{qid}/pdf").data
    assert frozen == open(tmp / "data" / "files" / q["pdf_stored_name"], "rb").read()
    # editing a sent revision is refused and changes nothing
    r = post(c, f"/quotations/{qid}/edit", {"item_desc": ["x"], "item_amount": ["1"], "language": "ar", "version": q["version"]})
    assert r.status_code in (302, 409)
    assert db(app).execute("SELECT items FROM quotations WHERE id=?", (qid,)).fetchone()["items"] == q["items"]
    post(c, f"/quotations/{qid}/status", {"action": "accept"})
    p = project_row(app, pid)
    assert p["agreed_fee_fils"] == 30000 and p["fee_basis"] == "quotation"
    assert db(app).execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1   # no duplicate project
    # Arabic PDF text check (shaped Arabic, KWD with 3 decimals)
    pdf_path = tmp / "q.pdf"
    pdf_path.write_bytes(frozen)
    txt = subprocess.run(["pdftotext", "-layout", str(pdf_path), "-"], capture_output=True, text=True).stdout
    assert "30.000" in txt and "12.500" in txt and f"QT-{year}-001" in txt and f"BASE-{year}-001" in txt
    assert "basedesignkw@gmail.com" in txt

    # ---- 6. drawings: two revisions, failed uploads record nothing
    for rev in ("R0", "R1"):
        r = post(c, f"/projects/{pid}/drawings", {"direction": "incoming", "title": "Structural set", "discipline": "Structural",
                                                  "revision": rev, "file": (io.BytesIO(b"%PDF-1.4 fake " + rev.encode()), f"STR-{rev}.pdf")},
                 content_type="multipart/form-data")
        assert r.status_code == 302
    drs = db(app).execute("SELECT revision, is_current FROM drawings WHERE project_id=? ORDER BY id", (pid,)).fetchall()
    assert [(d["revision"], d["is_current"]) for d in drs] == [("R0", 0), ("R1", 1)]
    post(c, f"/projects/{pid}/drawings", {"direction": "incoming", "file": (io.BytesIO(b"MZ"), "virus.exe")},
         content_type="multipart/form-data")
    post(c, f"/projects/{pid}/drawings", {"direction": "incoming", "file": (io.BytesIO(b""), "empty.pdf")},
         content_type="multipart/form-data")
    assert db(app).execute("SELECT COUNT(*) FROM drawings").fetchone()[0] == 2

    # ---- 7. deposit 10.000 (sent twice by a slow phone: counted once) -> balance 20.000
    pay = {"amount": "10.000", "date_received": today_kw().isoformat(), "kind": "deposit", "method": "Bank transfer",
           "request_key": "pay-deposit-1"}
    post(c, f"/projects/{pid}/payments", pay)
    post(c, f"/projects/{pid}/payments", pay)
    assert db(app).execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 1
    from app.logic import project_money
    m = project_money(db(app), project_row(app, pid))
    assert (m["fee"], m["received"], m["balance"]) == (30000, 10000, 20000)
    assert "20.000" in c.get(f"/projects/{pid}?tab=payments").get_data(as_text=True)

    num = p["number"]
    # ---- 8. BOQ ready, not submitted
    post(c, f"/projects/{pid}/status", {"work_status": "ready"})
    assert num in filter_numbers(c, "ready") and num in filter_numbers(c, "not_submitted")
    assert num not in filter_numbers(c, "delivered_balance")

    # ---- 9. partial submission: concrete + rebar
    post(c, f"/projects/{pid}/deliveries", {"d_concrete": "1", "d_rebar": "1", "delivery_date": today_kw().isoformat(),
                                            "recipient": "أبو خالد", "method": "Email", "request_key": "dl-1"})
    post(c, f"/projects/{pid}/deliveries", {"d_concrete": "1", "d_rebar": "1", "delivery_date": today_kw().isoformat(),
                                            "recipient": "أبو خالد", "method": "Email", "request_key": "dl-1"})  # retry
    assert db(app).execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 1
    assert num in filter_numbers(c, "partial") and num in filter_numbers(c, "ready")
    assert num not in filter_numbers(c, "not_submitted") and num not in filter_numbers(c, "delivered_balance")

    # ---- 10. full submission with an outgoing BOQ file
    post(c, f"/projects/{pid}/drawings", {"direction": "outgoing", "title": "BOQ workbook", "discipline": "BOQ", "revision": "R1",
                                          "file": (io.BytesIO(b"PK fake xlsx"), "BASE-BOQ-R1.xlsx")}, content_type="multipart/form-data")
    out_id = db(app).execute("SELECT id FROM drawings WHERE direction='outgoing'").fetchone()["id"]
    post(c, f"/projects/{pid}/deliveries", {"d_blockwork": "1", "drawing_id": out_id, "delivery_date": today_kw().isoformat(),
                                            "recipient": "أبو خالد", "method": "WhatsApp", "request_key": "dl-2"})
    assert num in filter_numbers(c, "delivered_balance")
    assert num not in filter_numbers(c, "partial") and num not in filter_numbers(c, "ready")
    coll = c.get("/collections").get_data(as_text=True)
    assert num in coll and "20.000" in coll and "wa.me/96599887766" in coll
    # issued BOQ recorded the exact Archive rule revisions
    used = json.loads(db(app).execute("SELECT rules_used FROM deliveries WHERE request_key='dl-2'").fetchone()["rules_used"])
    assert {(u["rule_id"], u["revision"]) for u in used} == {(proj_rule, 1), (company_rule, 1)}

    # ---- 11. monthly cash by payment date
    dash = c.get("/?f=all").get_data(as_text=True)
    assert re.search(r"Cash received this month.*?10\.000", dash, re.S)

    # ---- 12. payment follow-up email: draft shows the numbers, nothing logged until confirmed
    e = c.get(f"/projects/{pid}/email/payment?language=ar").get_data(as_text=True)
    assert "30.000" in e and "10.000" in e and "20.000" in e and "client@example.com" in e
    r = post(c, f"/projects/{pid}/email/payment", {"to": "client@example.com", "subject": "s", "body": "b", "action": "eml", "language": "ar"})
    assert r.mimetype == "message/rfc822" and b"X-Unsent: 1" in r.data
    assert db(app).execute("SELECT COUNT(*) FROM emails").fetchone()[0] == 0
    post(c, f"/projects/{pid}/email/payment", {"to": "client@example.com", "subject": "s", "body": "b", "action": "mark_sent", "language": "ar"})
    assert db(app).execute("SELECT COUNT(*) FROM activity WHERE action='email_sent' AND project_id=?", (pid,)).fetchone()[0] == 1
    # quotation email attaches the frozen quotation PDF
    r = post(c, f"/projects/{pid}/email/quotation", {"quotation_id": qid, "to": "client@example.com", "subject": "q", "body": "b",
                                                      "action": "eml", "language": "ar"})
    assert f"QT-{year}-001-Rev0.pdf".encode() in r.data

    # ---- 13. final payment 20.000 (next month) -> leaves the collection list, history stays
    next_month = (today_kw().replace(day=1) + timedelta(days=32)).replace(day=5)
    post(c, f"/projects/{pid}/payments", {"amount": "20.000", "date_received": next_month.isoformat(), "kind": "final",
                                          "request_key": "pay-final"})
    m = project_money(db(app), project_row(app, pid))
    assert m["balance"] == 0 and m["state"] == "paid"
    assert num not in filter_numbers(c, "delivered_balance")
    assert num not in c.get("/collections").get_data(as_text=True)
    assert db(app).execute("SELECT COUNT(*) FROM deliveries WHERE project_id=?", (pid,)).fetchone()[0] == 2
    assert "R1" in c.get(f"/projects/{pid}?tab=drawings").get_data(as_text=True)
    # each receipt counted in its own month
    this_m = c.get(f"/payments?month={today_kw().strftime('%Y-%m')}").get_data(as_text=True)
    next_m = c.get(f"/payments?month={next_month.strftime('%Y-%m')}").get_data(as_text=True)
    assert "Total this month: <span class=\"num\"><bdi dir=\"ltr\">10.000" in this_m
    assert "Total this month: <span class=\"num\"><bdi dir=\"ltr\">20.000" in next_m

    # ---- 14. Revit export: model data only, no money / notes
    r = c.get(f"/projects/{pid}/export/revit.json")
    body = r.get_data(as_text=True)
    data = json.loads(body)
    assert data["schema"] == "base-office/revit-import" and data["schemaVersion"] == "1.0"
    assert data["project"]["number"] == num and data["project"]["client"] == "شركة النخيل للمقاولات"
    ffl_levels = {l["name"]: l["elevationMm"] for l in data["levels"] if l["datum"] == "FFL"}
    assert ffl_levels == {"Basement": -3400, "Ground Floor": 600, "First Floor": 4600, "Second Floor": 8600,
                          "Roof": 12600, "Upper Roof": 16600}
    ssl = {l["name"]: l["elevationMm"] for l in data["levels"] if l["datum"] == "SSL"}
    assert ssl["Ground Floor - Slab Top"] == 500
    assert all(l["status"] == "confirmed" for l in data["levels"])
    assert len({l["stableId"] for l in data["levels"]}) == len(data["levels"])
    assert [g["name"] for g in data["grids"]] == ["A", "B", "1", "2"]          # C has a blank coordinate
    assert data["grids"][0] == {"stableId": data["grids"][0]["stableId"], "name": "A", "startMm": [0, -1500], "endMm": [0, 16500]}
    assert any(o["name"] == "C" for o in data["omitted"])
    for banned in ("fee", "fils", "payment", "SECRET-INTERNAL-NOTE", "30.000", "balance", "quotation"):
        assert banned not in body.lower() if banned.islower() else banned not in body, banned
    (tmp / "villa-revit2022.json").write_text(body, encoding="utf-8")
    # export again: same stable IDs
    data2 = json.loads(c.get(f"/projects/{pid}/export/revit.json").get_data(as_text=True))
    assert [l["stableId"] for l in data2["levels"]] == [l["stableId"] for l in data["levels"]]

    # ---- 15. Excel export from the same record
    wb = load_workbook(io.BytesIO(c.get(f"/projects/{pid}/export/boq.xlsx").data))
    assert wb.sheetnames == ["Intake", "Floors", "BOQ Worksheet", "Missing Info", "Grids", "Rules"]
    fl = wb["Floors"]
    rows = {fl.cell(row=r, column=2).value: r for r in range(5, 11)}
    assert fl.cell(row=rows["Ground Floor"], column=5).value == 0.6
    r1 = rows["First Floor"]
    assert fl.cell(row=r1, column=5).value == f'=IF(OR(E{r1 - 1}="",H{r1 - 1}=""),"",E{r1 - 1}+H{r1 - 1})'
    assert fl.cell(row=rows["Basement"], column=5).value.startswith('=IF(OR(E')    # chained down from GF
    assert fl.cell(row=11, column=14).value == "=SUM(N5:N10)"
    all_text = " ".join(str(c.value) for ws in wb for row in ws.iter_rows() for c in row if c.value is not None)
    assert "SECRET-INTERNAL-NOTE" not in all_text and "30000" not in all_text
    assert "PRELIMINARY ALLOWANCE" in all_text
    rules_ws = [wb["Rules"].cell(row=r, column=5).value for r in range(5, 7)]
    assert any("اللياسة" in (t or "") for t in rules_ws)

    # ---- 16. second villa: project-only rule stays out, company rule revision 2 applies
    post(c, f"/archive/{company_rule}", {"action": "revise", "text": "حساب القص على ١٢ متر، تسجيل الهالك الفعلي حسب القطر",
                                         "reason": "توضيح", "expected_revision": "1"})
    pid2 = create_villa(c, "Villa Al-Rawda", "Fictional Contractor Co.", {"new_email": "", "request_key": "v2"})
    from app.logic import applicable_rules
    r2 = applicable_rules(db(app), project_row(app, pid2))
    assert [(r["id"], r["current_revision"]) for r in r2] == [(company_rule, 2)]
    page2 = c.get(f"/projects/{pid2}?tab=floors").get_data(as_text=True)
    assert "اللياسة الداخلية" not in page2 and "حسب القطر" in page2
    # the issued BOQ of villa 1 still resolves revision 1
    used = json.loads(db(app).execute("SELECT rules_used FROM deliveries WHERE request_key='dl-2'").fetchone()["rules_used"])
    assert next(u for u in used if u["rule_id"] == company_rule)["revision"] == 1
    assert project_row(app, pid2)["number"] == f"BASE-{year}-002"

    # ---- 17. Archive export imported twice: counts unchanged
    exp = c.get("/archive/export.json").get_data(as_text=True)
    before = db(app).execute("SELECT COUNT(*) FROM rules").fetchone()[0], db(app).execute("SELECT COUNT(*) FROM rule_revisions").fetchone()[0]
    for _ in range(2):
        post(c, "/archive/import", {"payload": exp, "apply": "1"})
    after = db(app).execute("SELECT COUNT(*) FROM rules").fetchone()[0], db(app).execute("SELECT COUNT(*) FROM rule_revisions").fetchone()[0]
    assert before == after

    # ---- 18. prepaid job waiting for drawings: pending work, zero due
    pid3 = create_villa(c, "شاليه الخيران", "Fictional Contractor Co.", {"request_key": "v3"})
    post(c, f"/projects/{pid3}/direct", {"fee": "5.000", "channel": "WhatsApp", "evidence": "متفقين ٥ دنانير"})
    post(c, f"/projects/{pid3}/payments", {"amount": "5.000", "date_received": today_kw().isoformat(), "request_key": "pp3"})
    post(c, f"/projects/{pid3}/status", {"work_status": "waiting", "note": "Structural drawings R0 missing"})
    n3 = project_row(app, pid3)["number"]
    assert n3 in filter_numbers(c, "waiting") and n3 not in c.get("/collections").get_data(as_text=True)
    assert project_money(db(app), project_row(app, pid3))["balance"] == 0

    # ---- 19. overpayment shows a credit; reversal keeps history
    pay_id = db(app).execute("SELECT payment_id FROM allocations WHERE project_id=?", (pid3,)).fetchone()["payment_id"]
    post(c, f"/projects/{pid3}/payments", {"amount": "1.250", "date_received": today_kw().isoformat(), "request_key": "over"})
    assert project_money(db(app), project_row(app, pid3))["state"] == "credit"
    assert "Client credit" in c.get(f"/projects/{pid3}?tab=payments").get_data(as_text=True)
    over_id = db(app).execute("SELECT id FROM payments WHERE request_key='over'").fetchone()["id"]
    post(c, f"/payments/{over_id}/reverse", {"reason": "entered twice"})
    assert project_money(db(app), project_row(app, pid3))["balance"] == 0
    assert db(app).execute("SELECT COUNT(*) FROM payments WHERE reverses_payment_id=?", (over_id,)).fetchone()[0] == 1

    # ---- 20. concurrent edit: stale version is refused, nothing overwritten
    p = project_row(app, pid)
    post(c, f"/projects/{pid}/edit", {"name": "Renamed in tab A", "version": p["version"], "client_id": p["client_id"]})
    r = post(c, f"/projects/{pid}/edit", {"name": "Renamed in tab B", "version": p["version"], "client_id": p["client_id"]})
    assert project_row(app, pid)["name"] == "Renamed in tab A"

    # ---- 21. Arabic + English search
    post(c, f"/projects/{pid}/edit", {"name": "فيلا الياسمين - صباح الأحمد", "version": project_row(app, pid)["version"],
                                      "client_id": p["client_id"]})
    assert num in set(re.findall(r"BASE-\d{4}-\d{3}", c.get("/?f=all&q=الياسمين").get_data(as_text=True)))
    assert num in set(re.findall(r"BASE-\d{4}-\d{3}", c.get("/?f=all&q=النخيل").get_data(as_text=True)))   # client name
    assert set(re.findall(r"BASE-\d{4}-\d{3}", c.get("/?f=all&q=rawda").get_data(as_text=True).split("<tbody>")[1])) == {f"BASE-{year}-002"}

    # ---- 22. receipt PDF in Arabic
    rc = c.get(f"/payments/{pay_id}/receipt.pdf?lang=ar")
    assert rc.mimetype == "application/pdf" and rc.data[:4] == b"%PDF"

    # ---- 23. persistence: a fresh app on the same data folder sees everything
    app2 = create_app(str(tmp / "data"), testing=True)
    c2 = app2.test_client()
    # ...but a browser without sign-in cannot open pages or downloads
    did = db(app).execute("SELECT id FROM drawings LIMIT 1").fetchone()["id"]
    r = c2.get(f"/drawings/{did}/download")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    assert c2.get(f"/projects/{pid}/export/revit.json").status_code == 302
    with c2.session_transaction() as s:
        s["csrf"] = CSRF
    post(c2, "/login", {"username": "jalal", "password": "villa-test-123"})
    assert num in c2.get("/?f=all").get_data(as_text=True)
    assert c2.get(f"/drawings/{did}/download").status_code == 200

    # ---- 24. every main page renders in both languages
    for lang in ("en", "ar"):
        c.get(f"/lang/{lang}")
        for url in ["/", "/?f=delivered_balance", "/collections", "/clients", f"/clients/{p['client_id']}", "/quotations",
                    f"/quotations/{qid}", "/payments", "/activity", "/archive", f"/archive/{company_rule}", "/settings",
                    "/projects/new", f"/quotations/new?project_id={pid2}"] + \
                   [f"/projects/{pid}?tab={t}" for t in ("overview", "floors", "drawings", "quotations", "payments", "archive", "activity")] + \
                   [f"/projects/{pid}/email/{k}" for k in ("quotation", "boq", "payment")]:
            r = c.get(url)
            assert r.status_code == 200, (lang, url, r.status_code)
            if lang == "ar":
                assert 'dir="rtl"' in r.get_data(as_text=True)


def test_floor_example_from_brief():
    """GF FFL +0.600 and GF-to-first 4.000 -> first floor FFL +4.600; changing one height moves only higher non-fixed levels."""
    from app import floors
    rows = floors.generate(1, 2, True, None, 600, 4000)
    by = {r["name"]: r for r in rows}
    assert by["First Floor"]["ffl_mm"] == 4600
    assert [r["name"] for r in rows] == ["Basement", "Ground Floor", "First Floor", "Second Floor", "Roof"]
    by["First Floor"]["ftf_mm"] = 4500
    by["Roof"]["fixed"] = 1
    new, warnings = floors.recalculate(rows)
    moves = {m["name"] for m in floors.preview_changes(floors.generate(1, 2, True, None, 600, 4000), new)}
    nb = {r["name"]: r["ffl_mm"] for r in new}
    assert nb["Second Floor"] == 9100 and nb["Roof"] == 12600 and nb["Basement"] == -3400
    assert warnings and "Roof" in warnings[0]
    # blank height -> dependent levels blank, never zero
    rows2 = floors.generate(0, 2, False, None, 600, None)
    assert [r["ffl_mm"] for r in rows2] == [600, None, None]


def test_money_parsing():
    from app.util import parse_kwd, fmt_kwd, parse_m, fmt_m
    assert parse_kwd("30") == 30000 and parse_kwd("٣٠٫٥") == 30500 and parse_kwd("") is None
    with pytest.raises(ValueError):
        parse_kwd("1.2345")
    assert fmt_kwd(20000) == "20.000" and fmt_kwd(-1250) == "-1.250" and fmt_kwd(1234567) == "1,234.567"
    assert parse_m("+0.600") == 600 and parse_m("0") == 0 and parse_m("") is None
    assert fmt_m(-3400, signed=True) == "-3.400" and fmt_m(0, signed=True) == "±0.000"


def test_rule_scope_detection():
    from app.logic import detect_rule_scope
    assert detect_rule_scope("اعتمد هالطريقة لكل المشاريع", True, True)[0] == "company"
    assert detect_rule_scope("هي لهالمشروع بس", True, True)[0] == "project"
    assert detect_rule_scope("for this client use their template", True, True)[0] == "client"
    assert detect_rule_scope("measure plaster to beam soffit", True, True) == ("project", "UNCLEAR")
