"""BASE Design branded PDFs (quotation, payment receipt) in Arabic (RTL) or English."""
import os
from fpdf import FPDF
from .logic import scope_label
from .util import fmt_kwd, fmt_date

HERE = os.path.dirname(__file__)
FONT_DIR = os.path.join(HERE, "static", "fonts")       # Outfit + Tajawal, the same fonts as the BASE site
BRAND_LOGO = os.path.join(HERE, "static", "brand", "logo.png")   # white BASE logo with the red A, transparent
INK = (10, 10, 10)
TEAL = (249, 1, 48)        # brand red accent (name kept so the layout code stays unchanged)
GREY = (95, 95, 95)

T = {
    "quotation": ("QUOTATION", "عرض سعر"),
    "receipt": ("PAYMENT RECEIPT", "سند قبض"),
    "number": ("Quotation no.", "رقم العرض"),
    "rev": ("Revision", "المراجعة"),
    "date": ("Date", "التاريخ"),
    "valid": ("Valid until", "صالح حتى"),
    "to": ("To", "إلى"),
    "project": ("Project", "المشروع"),
    "project_no": ("Project no.", "رقم المشروع"),
    "location": ("Location", "الموقع"),
    "building": ("Building", "المبنى"),
    "scope": ("Scope of services", "نطاق الخدمات"),
    "desc": ("Description", "الوصف"),
    "amount": ("Amount (KWD)", "المبلغ (د.ك)"),
    "subtotal": ("Subtotal", "المجموع"),
    "discount": ("Discount", "الخصم"),
    "total": ("Total fee", "إجمالي الأتعاب"),
    "delivery": ("Delivery period", "مدة التسليم"),
    "terms": ("Payment terms", "شروط الدفع"),
    "exclusions": ("Exclusions", "الاستثناءات"),
    "notes": ("Notes", "ملاحظات"),
    "kwd": ("KWD", "د.ك"),
    "received_from": ("Received from", "استلمنا من"),
    "sum_of": ("The sum of", "مبلغ وقدره"),
    "method": ("Method", "طريقة الدفع"),
    "reference": ("Reference", "المرجع"),
    "for_project": ("For project", "عن مشروع"),
    "fee": ("Agreed fee", "الأتعاب المتفق عليها"),
    "paid_to_date": ("Received to date", "المستلم حتى تاريخه"),
    "balance": ("Outstanding balance", "المبلغ المتبقي"),
    "receipt_no": ("Receipt no.", "رقم السند"),
}


class BasePDF(FPDF):
    def __init__(self, lang, settings):
        super().__init__(format="A4")
        self.lang = lang
        self.rtl = lang == "ar"
        self.settings = settings
        self.add_font("ar", fname=os.path.join(FONT_DIR, "Tajawal-400.ttf"))
        self.add_font("ar", style="B", fname=os.path.join(FONT_DIR, "Tajawal-700.ttf"))
        self.add_font("en", fname=os.path.join(FONT_DIR, "Outfit-400.ttf"))
        self.add_font("en", style="B", fname=os.path.join(FONT_DIR, "Outfit-600.ttf"))
        self.add_font("thin", fname=os.path.join(FONT_DIR, "Outfit-300.ttf"))
        self.main = "ar" if self.rtl else "en"
        self.set_fallback_fonts(["en" if self.rtl else "ar"])
        self.set_text_shaping(use_shaping_engine=True)
        self.set_margins(16, 14, 16)
        self.set_auto_page_break(True, 24)

    def t(self, key):
        return T[key][1 if self.rtl else 0]

    def f(self, size=10, bold=False, color=INK):
        self.set_font(self.main, "B" if bold else "", size)
        self.set_text_color(*color)

    @property
    def align(self):
        return "R" if self.rtl else "L"

    def line_text(self, text, size=10, bold=False, color=INK, h=6, align=None):
        self.f(size, bold, color)
        self.multi_cell(0, h, text or "", align=align or self.align, new_x="LMARGIN", new_y="NEXT")

    def header(self):
        s = self.settings
        logo = s.get("_logo_path")
        if not (logo and os.path.exists(logo)):
            logo = BRAND_LOGO
        self.set_fill_color(*INK)
        self.rect(0, 0, self.w, 30, "F")
        self.set_fill_color(*TEAL)
        self.rect(0, 30, self.w, 0.8, "F")
        try:
            self.image(logo, x=16, y=6, h=18)     # the logo is Latin, so it sits on the left in both languages
        except Exception:
            self.set_xy(16, 9)
            self.set_font("thin", "", 20)
            self.set_text_color(255, 255, 255)
            self.cell(60, 10, "B A S E")
        self.set_font("thin", "", 7.5)
        self.set_text_color(255, 255, 255)
        self.set_char_spacing(2.2)
        self.set_xy(self.w - 16 - 90, 9)
        self.cell(90, 6, (s.get("company_name") or "BASE Design").upper(), align="R")
        self.set_char_spacing(0)
        self.set_font("en", "", 8.5)
        self.set_text_color(200, 200, 200)
        self.set_xy(self.w - 16 - 90, 15)
        self.cell(90, 6, s.get("company_email", ""), align="R")
        self.set_y(38)

    def footer(self):
        s = self.settings
        self.set_y(-18)
        self.set_draw_color(*TEAL)
        self.line(16, self.get_y(), self.w - 16, self.get_y())
        foot = s.get("quotation_footer_ar") if self.rtl else s.get("quotation_footer")
        self.f(8, False, GREY)
        self.cell(0, 6, f"{foot or ''}  |  {s.get('company_email', '')}", align="C", new_x="LMARGIN", new_y="NEXT")
        self.cell(0, 4, f"{self.page_no()}/{{nb}}", align="C")

    def kv(self, key, value):
        """Label / value pair on one row, mirrored for Arabic."""
        if value in (None, ""):
            return
        w_label = 42
        w_val = self.w - 32 - w_label
        y = self.get_y()
        if self.rtl:
            self.set_x(16 + w_val)
            self.f(10, True, GREY)
            self.cell(w_label, 7, self.t(key), align="R")
            self.set_xy(16, y)
            self.f(10)
            self.multi_cell(w_val, 7, str(value), align="R", new_x="LMARGIN", new_y="NEXT")
        else:
            self.set_x(16)
            self.f(10, True, GREY)
            self.cell(w_label, 7, self.t(key), align="L")
            self.f(10)
            self.multi_cell(w_val, 7, str(value), align="L", new_x="LMARGIN", new_y="NEXT")

    def title_bar(self, text):
        self.ln(2)
        if self.rtl:
            self.f(17, True, INK)
        else:
            self.set_font("thin", "", 17)
            self.set_text_color(*INK)
            self.set_char_spacing(2.5)
        self.cell(0, 10, text, align=self.align, new_x="LMARGIN", new_y="NEXT")
        self.set_char_spacing(0)
        self.set_draw_color(*TEAL)
        self.set_line_width(0.6)
        x0 = self.w - 16 - 18 if self.rtl else 16
        self.line(x0, self.get_y(), x0 + 18, self.get_y())    # short red rule, like the site headings
        self.set_line_width(0.2)
        self.ln(3)

    def section(self, key):
        self.ln(2)
        self.line_text(self.t(key), 11, True, INK, h=7)

    def money_table(self, items, subtotal, discount, total):
        W = self.w - 32
        wn, wa = 10, 38
        wd = W - wn - wa
        cols = [("#", wn, "C"), (self.t("desc"), wd, self.align), (self.t("amount"), wa, "R" if not self.rtl else "L")]
        if self.rtl:
            cols = list(reversed(cols))
        self.set_fill_color(*INK)
        self.f(10, True, (255, 255, 255))
        self.set_x(16)
        for label, w, al in cols:
            self.cell(w, 8, label, border=0, align="C", fill=True)
        self.ln(8)
        self.set_draw_color(220, 225, 230)
        for i, it in enumerate(items, 1):
            vals = {"#": str(i), self.t("desc"): it["description"], self.t("amount"): fmt_kwd(it["amount_fils"])}
            y0 = self.get_y()
            # measure height of the description
            self.f(10)
            lines = self.multi_cell(wd, 6, it["description"], dry_run=True, output="LINES")
            h = max(8, 6 * len(lines) + 2)
            if y0 + h > self.page_break_trigger:
                self.add_page()
                y0 = self.get_y()
            x = 16
            for label, w, al in cols:
                self.set_xy(x, y0)
                if label == self.t("desc"):
                    self.set_xy(x, y0 + 1)
                    self.multi_cell(w, 6, vals[label], align=al)
                else:
                    self.cell(w, h, vals[label], align=al)
                x += w
            self.set_y(y0 + h)
            self.line(16, self.get_y(), self.w - 16, self.get_y())
        def total_row(label, value, bold=False):
            self.f(10, bold)
            if self.rtl:
                self.set_x(16)
                self.cell(wa, 8, value, align="L")
                self.cell(wd + wn, 8, label, align="R", new_x="LMARGIN", new_y="NEXT")
            else:
                self.set_x(16)
                self.cell(wd + wn, 8, label, align="R")
                self.cell(wa, 8, value, align="R", new_x="LMARGIN", new_y="NEXT")
        if discount:
            total_row(self.t("subtotal"), fmt_kwd(subtotal))
            total_row(self.t("discount"), "-" + fmt_kwd(discount))
        self.set_fill_color(240, 240, 240)
        y = self.get_y()
        self.rect(16, y, W, 9, "F")
        total_row(f"{self.t('total')} ({self.t('kwd')})", fmt_kwd(total), bold=True)


def quotation_pdf(settings, q, client, project, items):
    lang = q["language"]
    pdf = BasePDF(lang, settings)
    pdf.alias_nb_pages()
    pdf.set_title(f"{q['number']} Rev {q['revision']}")
    pdf.set_author(settings.get("company_name", "BASE Design"))
    pdf.add_page()
    pdf.title_bar(pdf.t("quotation"))
    pdf.kv("number", q["number"])
    pdf.kv("rev", str(q["revision"]))
    pdf.kv("date", fmt_date(q["quote_date"]))
    if q["validity_days"]:
        from datetime import date, timedelta
        pdf.kv("valid", fmt_date((date.fromisoformat(q["quote_date"]) + timedelta(days=q["validity_days"])).isoformat()))
    pdf.ln(2)
    to = client["name"] + (f" - {client['contact_person']}" if client["contact_person"] else "")
    pdf.kv("to", to)
    if project:
        pdf.kv("project", project["name"])
        pdf.kv("project_no", project["number"])
    pdf.kv("location", q["location_text"])
    pdf.kv("building", q["building_description"])
    pdf.section("scope")
    subtotal = sum(i["amount_fils"] for i in items)
    total = subtotal - (q["discount_fils"] or 0)
    pdf.money_table(items, subtotal, q["discount_fils"] or 0, total)
    pdf.ln(3)
    for key, field in (("delivery", "delivery_period"), ("terms", "payment_terms"), ("exclusions", "exclusions"),
                       ("notes", "notes")):
        if q[field]:
            pdf.section(key)
            pdf.line_text(q[field], 10)
    return bytes(pdf.output())


def receipt_pdf(settings, payment, client, allocations, money_by_project, lang="ar"):
    pdf = BasePDF(lang, settings)
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.title_bar(pdf.t("receipt"))
    pdf.kv("receipt_no", f"R-{payment['id']:05d}")
    pdf.kv("date", fmt_date(payment["date_received"]))
    pdf.kv("received_from", payment["payer"] or client["name"])
    pdf.kv("sum_of", f"{fmt_kwd(payment['amount_fils'])} {pdf.t('kwd')}")
    pdf.kv("method", payment["method"])
    pdf.kv("reference", payment["reference"])
    for a in allocations:
        pdf.ln(2)
        m = money_by_project[a["project_id"]]
        pdf.kv("for_project", f"{a['number']} - {a['name']}: {fmt_kwd(a['amount_fils'])} {pdf.t('kwd')}")
        if m["fee"] is not None:
            pdf.kv("fee", f"{fmt_kwd(m['fee'])} {pdf.t('kwd')}")
            pdf.kv("paid_to_date", f"{fmt_kwd(m['received'])} {pdf.t('kwd')}")
            pdf.kv("balance", f"{fmt_kwd(m['balance'])} {pdf.t('kwd')}")
    return bytes(pdf.output())
