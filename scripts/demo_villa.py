"""Create a separate DEMO database with fictional projects (never use your real data folder).

    python scripts/demo_villa.py demo-data
    set BASE_OFFICE_DATA=demo-data && python run.py      (Windows)

Sign in with demo / demo-pass-123. Also writes revit/samples/villa-revit2022.json.
"""
import io
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app  # noqa: E402
from app.util import today_kw  # noqa: E402

T = "demo"


def main(data_dir):
    if os.path.exists(os.path.join(data_dir, "base_office.db")):
        sys.exit(f"{data_dir} already has a database. Choose an empty folder for the demo.")
    app = create_app(data_dir)
    c = app.test_client()
    with c.session_transaction() as s:
        s["csrf"] = T
    def post(url, data, **kw):
        data = dict(data); data["csrf"] = T
        r = c.post(url, data=data, **kw)
        assert r.status_code in (200, 302), (url, r.status_code)
        return r
    post("/setup", dict(username="demo", password="demo-pass-123", password2="demo-pass-123"))
    with c.session_transaction() as s:
        s["csrf"] = T
    today = today_kw()

    def villa(name, client, key, **extra):
        d = dict(new_client_name=client, new_contact="أبو خالد", new_phone="99887766", new_email="client@example.com",
                 name=name, owner_name="خالد العلي", area="صباح الأحمد", block="3", street="12", plot="120",
                 building_type="villa", plot_area_m2="400", basement_count="1", upper_floor_count="2", has_roof="1",
                 roof_structures="غرفة درج وخزانات", typical_ftf_mm="4.000", gf_ffl_mm="+0.600", preset="هيكل أسود + طابوق",
                 due_date=(today + timedelta(days=10)).isoformat(), date_received=today.isoformat(), request_key=key)
        d.update(extra)
        r = post("/projects/new", d)
        return int(r.headers["Location"].split("/")[-1])

    pid = villa("فيلا الياسمين - صباح الأحمد", "شركة النخيل للمقاولات (وهمية)", "demo-1")
    # confirm levels, areas, slab offsets (GF/upper/roof -0.100)
    from app.db import connect
    db = connect(app.config["DATABASE"])
    for name, area in (("Basement", 300), ("Ground Floor", 280), ("First Floor", 280), ("Second Floor", 280), ("Roof", 60)):
        db.execute("UPDATE levels SET area_m2=?, confirmed=1 WHERE project_id=? AND name=?", (area, pid, name))
    db.execute("UPDATE levels SET slab_offset_mm=-100 WHERE project_id=? AND kind IN ('ground','upper','roof')", (pid,))
    db.execute("UPDATE levels SET confirmed=1 WHERE project_id=?", (pid,))
    db.commit()
    post(f"/projects/{pid}/grids", {"uid": ["new"] * 4, "name": ["A", "B", "1", "2"], "x1": ["0", "12", "-1.5", "-1.5"],
                                     "y1": ["-1.5", "-1.5", "0", "15"], "x2": ["0", "12", "13.5", "13.5"],
                                     "y2": ["16.5", "16.5", "0", "15"], "notes": [""] * 4})
    post("/archive", dict(text="حساب القص على قضبان ١٢ متر وتسجيل الهالك الفعلي. اعتمد هالطريقة لكل المشاريع", topic="rebar",
                          project_id=pid, request_key="demo-r1"))
    post("/archive", dict(text="لهالمشروع بس: اللياسة الداخلية لحد أسفل الجسور (سقف معلق)", topic="finishes", project_id=pid,
                          request_key="demo-r2"))
    r = post("/quotations/new", {"project_id": pid, "language": "ar", "quote_date": today.isoformat(), "validity_days": "14",
                                 "delivery_period": "٧ أيام عمل من استلام المخططات كاملة",
                                 "item_desc": ["حصر كميات الخرسانة وإعداد جدول الكميات", "حصر حديد التسليح وجدول التفريد BBS", "حصر الطابوق الداخلي والخارجي"],
                                 "item_scope": ["concrete", "rebar", "blockwork"], "item_amount": ["10.000", "12.500", "7.500"],
                                 "payment_terms": "٥٠٪ عند الموافقة و ٥٠٪ عند التسليم", "exclusions": "التشطيبات والأعمال الكهروميكانيكية"})
    qid = int(r.headers["Location"].split("/")[-1])
    post(f"/quotations/{qid}/status", {"action": "sent"})
    post(f"/quotations/{qid}/status", {"action": "accept"})
    post(f"/projects/{pid}/drawings", {"direction": "incoming", "title": "Structural set", "discipline": "Structural", "revision": "R0",
                                       "file": (io.BytesIO(b"%PDF-1.4 demo"), "STR-R0.pdf")}, content_type="multipart/form-data")
    post(f"/projects/{pid}/payments", {"amount": "10.000", "date_received": today.isoformat(), "kind": "deposit", "request_key": "demo-p1"})
    post(f"/projects/{pid}/status", {"work_status": "ready"})
    post(f"/projects/{pid}/deliveries", {"d_concrete": "1", "d_rebar": "1", "delivery_date": today.isoformat(), "method": "Email",
                                         "recipient": "أبو خالد", "request_key": "demo-d1"})
    post(f"/projects/{pid}/deliveries", {"d_blockwork": "1", "delivery_date": today.isoformat(), "method": "WhatsApp",
                                         "recipient": "أبو خالد", "request_key": "demo-d2"})
    # second villa: ready to submit, fee pending agreement
    pid2 = villa("Villa Al-Rawda", "Fictional Contractor Co.", "demo-2", new_email="", area="Abdullah Al-Mubarak")
    post(f"/projects/{pid2}/status", {"work_status": "ready"})
    # chalet: prepaid, waiting for drawings
    pid3 = villa("شاليه الخيران", "Fictional Contractor Co.", "demo-3", area="الخيران", basement_count="0", upper_floor_count="1")
    post(f"/projects/{pid3}/direct", {"fee": "5.000", "channel": "WhatsApp", "evidence": "متفقين على ٥ دنانير"})
    post(f"/projects/{pid3}/payments", {"amount": "5.000", "date_received": today.isoformat(), "request_key": "demo-p3"})
    post(f"/projects/{pid3}/status", {"work_status": "waiting", "note": "Structural drawings R0 missing"})

    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "revit", "samples", "villa-revit2022.json")
    with open(out, "wb") as fh:
        fh.write(c.get(f"/projects/{pid}/export/revit.json").data)
    print("Demo ready in", data_dir, "- sign in with demo / demo-pass-123. Revit sample:", out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "demo-data")
