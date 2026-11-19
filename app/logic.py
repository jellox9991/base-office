"""Business rules: numbering, balances, submission status, dashboard filters, Archive rules."""
import json
import re
from .db import get_db, get_settings
from .util import now_iso, today_kw

SCOPES = ["concrete", "rebar", "blockwork", "plaster", "waterproofing", "tiling", "skirting", "ceilings", "cladding"]
SCOPE_LABELS = {
    "concrete": ("Concrete", "الخرسانة"),
    "rebar": ("Reinforcement", "حديد التسليح"),
    "blockwork": ("Blockwork", "الطابوق"),
    "plaster": ("Plaster", "اللياسة"),
    "waterproofing": ("Waterproofing", "العزل المائي"),
    "tiling": ("Tiling", "البلاط"),
    "skirting": ("Skirting", "النعلات"),
    "ceilings": ("Ceilings", "الأسقف المعلقة"),
    "cladding": ("Cladding", "التكسيات"),
}
WORK_STATUSES = ["not_started", "in_progress", "waiting", "ready", "completed"]
WORK_LABELS = {
    "not_started": ("Not started", "لم يبدأ"),
    "in_progress": ("In progress", "قيد العمل"),
    "waiting": ("Waiting for drawings", "بانتظار المخططات"),
    "ready": ("Ready to submit", "جاهز للتسليم"),
    "completed": ("Completed", "مكتمل"),
}
SUB_LABELS = {
    "none": ("Not submitted", "لم يُسلَّم"),
    "partial": ("Partially submitted", "تسليم جزئي"),
    "full": ("Fully submitted", "تم التسليم كاملاً"),
}
QUOTE_LABELS = {
    "draft": ("Draft", "مسودة"), "sent": ("Sent", "مرسل"), "accepted": ("Accepted", "مقبول"),
    "rejected": ("Rejected", "مرفوض"), "expired": ("Expired", "منتهي"), "superseded": ("Superseded", "مستبدل"),
}
FILTERS = {
    "all": ("All projects", "كل المشاريع"),
    "today": ("Work on today", "شغل اليوم"),
    "not_submitted": ("Not submitted", "لم يُسلَّم"),
    "ready": ("Ready to submit", "جاهز للتسليم"),
    "partial": ("Partially submitted", "تسليم جزئي"),
    "waiting": ("Waiting for drawings", "بانتظار المخططات"),
    "delivered_balance": ("Delivered with balance", "مُسلَّم وعليه رصيد"),
    "overdue": ("Overdue", "متأخر"),
    "outstanding": ("Outstanding on agreed jobs", "مبالغ مستحقة"),
    "open": ("Open projects", "مشاريع مفتوحة"),
    "archived": ("Archived", "مؤرشف"),
}


def scope_label(key, lang="en"):
    if key in SCOPE_LABELS:
        return SCOPE_LABELS[key][1 if lang == "ar" else 0]
    return key


# ---------------------------------------------------------------- activity log
def log(db, action, detail="", project_id=None, client_id=None, before=None, after=None, actor=None):
    from flask import session, has_request_context
    if actor is None and has_request_context():
        actor = session.get("user")
    db.execute(
        "INSERT INTO activity(at, actor, project_id, client_id, action, detail, before, after) VALUES (?,?,?,?,?,?,?,?)",
        (now_iso(), actor, project_id, client_id, action, detail,
         json.dumps(before, ensure_ascii=False) if before is not None else None,
         json.dumps(after, ensure_ascii=False) if after is not None else None))


# ---------------------------------------------------------------- numbering
def next_project_number(db, year=None):
    s = get_settings(db)
    prefix = s.get("number_prefix") or "BASE"
    year = year or today_kw().year
    pat = f"{prefix}-{year}-"
    rows = db.execute("SELECT number FROM projects WHERE number LIKE ?", (pat + "%",)).fetchall()
    seqs = [int(m.group(1)) for r in rows if (m := re.match(re.escape(pat) + r"(\d+)$", r["number"]))]
    return f"{pat}{(max(seqs) + 1) if seqs else 1:03d}"


def next_quote_number(db, year=None):
    s = get_settings(db)
    prefix = s.get("quote_prefix") or "QT"
    year = year or today_kw().year
    pat = f"{prefix}-{year}-"
    rows = db.execute("SELECT DISTINCT number FROM quotations WHERE number LIKE ?", (pat + "%",)).fetchall()
    seqs = [int(m.group(1)) for r in rows if (m := re.match(re.escape(pat) + r"(\d+)$", r["number"]))]
    return f"{pat}{(max(seqs) + 1) if seqs else 1:03d}"


# ---------------------------------------------------------------- money
def received_fils(db, project_id):
    r = db.execute("SELECT COALESCE(SUM(amount_fils), 0) AS s FROM allocations WHERE project_id = ?",
                   (project_id,)).fetchone()
    return r["s"]


def project_money(db, p):
    """Returns dict(fee, received, balance, state). balance None while the fee is pending agreement."""
    received = received_fils(db, p["id"])
    fee = p["agreed_fee_fils"]
    if fee is None:
        return dict(fee=None, received=received, balance=None, state="pending_agreement")
    bal = fee - received
    state = "paid" if bal == 0 else ("credit" if bal < 0 else ("unpaid" if received == 0 else "part_paid"))
    return dict(fee=fee, received=received, balance=bal, state=state)


MONEY_LABELS = {
    "pending_agreement": ("Pending agreement", "بانتظار الاتفاق"),
    "unpaid": ("Unpaid", "غير مدفوع"),
    "part_paid": ("Partly paid", "مدفوع جزئياً"),
    "paid": ("Paid", "مدفوع"),
    "credit": ("Credit (overpaid)", "رصيد دائن للعميل"),
}


def payment_unallocated(db, payment_id):
    r = db.execute("""SELECT p.amount_fils - COALESCE((SELECT SUM(amount_fils) FROM allocations a WHERE a.payment_id = p.id), 0) AS u
                      FROM payments p WHERE p.id = ?""", (payment_id,)).fetchone()
    return r["u"] if r else 0


# ---------------------------------------------------------------- submissions
def project_scope(p):
    try:
        s = json.loads(p["scope"] or "[]")
    except ValueError:
        s = []
    if p["custom_scope"]:
        s = s + [c.strip() for c in p["custom_scope"].split(",") if c.strip()]
    return s


def submission_state(db, p):
    rows = db.execute("SELECT scope, completes_scope, delivery_date FROM deliveries WHERE project_id = ? ORDER BY delivery_date",
                      (p["id"],)).fetchall()
    if not rows:
        return dict(status="none", delivered=[], remaining=project_scope(p), last_date=None)
    delivered = []
    complete = False
    for r in rows:
        for s in json.loads(r["scope"] or "[]"):
            if s not in delivered:
                delivered.append(s)
        if r["completes_scope"]:
            complete = True
    agreed = project_scope(p)
    remaining = [s for s in agreed if s not in delivered]
    if complete or (agreed and not remaining):
        status, remaining = "full", []
    else:
        status = "partial"
    return dict(status=status, delivered=delivered, remaining=remaining, last_date=rows[-1]["delivery_date"])


# ---------------------------------------------------------------- project rows for the dashboard
def project_rows(db, flt="all", q=""):
    sql = """SELECT p.*, c.name AS client_name, c.phone AS client_phone, c.whatsapp AS client_whatsapp,
                    c.email AS client_email
             FROM projects p JOIN clients c ON c.id = p.client_id"""
    rows = db.execute(sql + " ORDER BY p.due_date IS NULL, p.due_date, p.id DESC").fetchall()
    today = today_kw().isoformat()
    out = []
    qn = normalise_search(q)
    for p in rows:
        if qn:
            hay = normalise_search(" ".join(str(p[k] or "") for k in
                                            ("number", "name", "owner_name", "area", "block", "plot", "client_name",
                                             "client_phone", "responsible")))
            if qn not in hay:
                continue
        money = project_money(db, p)
        sub = submission_state(db, p)
        d = dict(p)
        d.update(money=money, sub=sub, scope_list=project_scope(p),
                 overdue=bool(p["due_date"] and p["due_date"] < today and sub["status"] != "full" and not p["archived"]))
        if matches(d, flt, today):
            out.append(d)
    return out


def matches(d, flt, today):
    archived = d["archived"]
    sub = d["sub"]["status"]
    m = d["money"]
    if flt == "archived":
        return bool(archived)
    if archived:
        return False
    if flt in ("all", "open", ""):
        return True
    if flt == "not_submitted":
        return sub == "none"
    if flt == "ready":
        return d["work_status"] == "ready" and sub != "full"
    if flt == "partial":
        return sub == "partial"
    if flt == "waiting":
        return d["work_status"] == "waiting"
    if flt == "delivered_balance":
        return sub == "full" and m["balance"] is not None and m["balance"] > 0
    if flt == "overdue":
        return d["overdue"]
    if flt == "outstanding":
        return m["balance"] is not None and m["balance"] > 0
    if flt == "today":
        # work to do: not fully submitted, not waiting on the client, and either overdue, due within 3 days,
        # in progress, or ready to send
        if sub == "full" or d["work_status"] == "waiting":
            return False
        if d["overdue"] or d["work_status"] in ("in_progress", "ready"):
            return True
        if d["due_date"]:
            from datetime import date, timedelta
            return d["due_date"] <= (date.fromisoformat(today) + timedelta(days=3)).isoformat()
        return False
    return True


def normalise_search(text):
    """Case-fold and fold common Arabic letter variants so أحمد / احمد and ة / ه match."""
    if not text:
        return ""
    t = str(text).casefold()
    for a, b in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ى", "ي"), ("ة", "ه"), ("ؤ", "و"), ("ئ", "ي"), ("ـ", "")):
        t = t.replace(a, b)
    t = re.sub(r"[ً-ْ]", "", t)  # harakat
    return t


def dashboard_metrics(db):
    month = today_kw().strftime("%Y-%m")
    cash = db.execute("SELECT COALESCE(SUM(amount_fils),0) AS s, COUNT(*) AS n FROM payments WHERE substr(date_received,1,7) = ?",
                      (month,)).fetchone()
    rows = project_rows(db, "all")
    outstanding = sum(r["money"]["balance"] for r in rows if r["money"]["balance"] and r["money"]["balance"] > 0)
    credits = sum(-r["money"]["balance"] for r in rows if r["money"]["balance"] and r["money"]["balance"] < 0)
    open_count = len(rows)
    awaiting = db.execute("SELECT COUNT(*) AS n FROM quotations WHERE status = 'sent'").fetchone()["n"]
    counts = {k: len([r for r in rows if matches(r, k, today_kw().isoformat())]) for k in
              ("today", "not_submitted", "ready", "partial", "waiting", "delivered_balance", "overdue")}
    return dict(month=month, cash=cash["s"], cash_count=cash["n"], outstanding=outstanding, credits=credits,
                open=open_count, awaiting=awaiting, counts=counts)


# ---------------------------------------------------------------- Archive | الأرشيف
COMPANY_PHRASES = ["لكل المشاريع", "لجميع المشاريع", "كل المشاريع", "اعتمد هالطريقة", "دايماً", "دائما", "دايما",
                   "all projects", "every project", "always", "company rule", "from now on"]
PROJECT_PHRASES = ["لهالمشروع بس", "لهذا المشروع فقط", "بس لهالمشروع", "هالمشروع بس", "لهالمشروع", "this project only",
                   "only this project", "only for this project", "just this project", "for this job only"]
CLIENT_PHRASES = ["لهالعميل", "لهذا العميل", "هالزبون", "لهالزبون", "for this client", "this client only", "client template",
                  "for this contractor"]


def detect_rule_scope(text, has_project, has_client):
    """Guess the rule scope from Jalal's own wording. The form always lets him override it."""
    t = normalise_search(text)
    def hit(phrases):
        return any(normalise_search(p) in t for p in phrases)
    if hit(PROJECT_PHRASES) and has_project:
        return "project", "the wording says it is for this project only"
    if hit(CLIENT_PHRASES) and has_client:
        return "client", "the wording names this client"
    if hit(COMPANY_PHRASES):
        return "company", "the wording says to use it for all projects"
    if has_project:
        return "project", "UNCLEAR"
    return "company", "UNCLEAR"


def applicable_rules(db, project):
    """Active rules that apply to a project, with the reason each applies."""
    rows = db.execute("""SELECT r.*, rv.text, rv.original_wording, rv.created_at AS rev_at, c.name AS client_name
                         FROM rules r JOIN rule_revisions rv ON rv.rule_id = r.id AND rv.revision = r.current_revision
                         LEFT JOIN clients c ON c.id = r.client_id
                         WHERE r.active = 1 AND r.status = 'approved' AND (r.scope = 'company'
                               OR (r.scope = 'client' AND r.client_id = ?)
                               OR (r.scope = 'project' AND r.project_id = ?))
                         ORDER BY CASE r.scope WHEN 'project' THEN 0 WHEN 'client' THEN 1 ELSE 2 END, r.id""",
                      (project["client_id"], project["id"])).fetchall()
    scope = set(project_scope(project))
    out = []
    for r in rows:
        if r["trade"] and r["trade"] not in scope:
            continue
        if r["scope"] == "company":
            why = ("Company rule: applies to every project", "قاعدة عامة: تنطبق على كل المشاريع")
        elif r["scope"] == "client":
            why = (f"Client rule for {r['client_name']}", f"قاعدة خاصة بالعميل {r['client_name']}")
        else:
            why = ("Project-only rule for this job", "قاعدة لهذا المشروع فقط")
        if r["trade"]:
            why = (why[0] + f" ({scope_label(r['trade'])} is in scope)", why[1] + f" ({scope_label(r['trade'], 'ar')} ضمن النطاق)")
        d = dict(r)
        d["why"] = why
        out.append(d)
    return out


def rules_snapshot(db, project):
    return [dict(rule_id=r["id"], uid=r["uid"], revision=r["current_revision"], scope=r["scope"], text=r["text"])
            for r in applicable_rules(db, project)]


TOPICS = {
    "concrete": ("Concrete", "الخرسانة"), "rebar": ("Rebar", "الحديد"), "blockwork": ("Blockwork", "الطابوق"),
    "finishes": ("Finishes", "التشطيبات"), "measurement": ("Measurement", "طريقة الحصر"), "revit": ("Revit", "ريفت"),
    "client_excel": ("Client Excel", "إكسل العميل"), "quotations": ("Quotations", "العروض"),
    "delivery": ("Delivery", "التسليم"), "other": ("Other", "أخرى"),
}
RULE_SCOPES = {"company": ("Company rule", "قاعدة عامة"), "client": ("Client rule", "قاعدة للعميل"),
               "project": ("Project only", "لهالمشروع بس")}
RULE_STATUS = {"approved": ("Approved", "معتمد"), "draft": ("Draft - scope unclear", "مسودة - النطاق غير واضح"),
               "example": ("Example - confirm wording", "مثال - بحاجة تأكيد")}
