"""Email drafts: Send Quotation, Submit BOQ, Follow Up on Payment (Arabic or English).

Nothing is marked as sent when a draft is opened. A draft is logged only when Jalal confirms it was
sent (or when the optional SMTP send succeeds).
"""
import mimetypes
import os
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from .util import fmt_kwd

KINDS = {
    "quotation": ("Send Quotation", "إرسال عرض السعر"),
    "boq": ("Submit BOQ", "تسليم جدول الكميات"),
    "payment": ("Follow Up on Payment", "متابعة الدفعة"),
}


def draft(kind, lang, settings, project, client, money=None, quotation=None, scope_text=""):
    company = settings.get("company_name", "BASE Design")
    email = settings.get("company_email", "")
    contact = client["contact_person"] or client["name"]
    name, no = project["name"], project["number"]
    sig_en = f"\n\nBest regards,\n{company}\n{email}"
    sig_ar = f"\n\nمع التحية،\n{settings.get('company_name_ar') or company}\n{email}"
    if kind == "quotation":
        qno = f"{quotation['number']} Rev {quotation['revision']}" if quotation else ""
        total = fmt_kwd(quotation["_total"]) if quotation else ""
        if lang == "ar":
            subject = f"عرض سعر {qno} - {name} ({no})"
            body = (f"مرحبا {contact}،\n\nمرفق عرض السعر لمشروع {name} (رقم {no}).\n"
                    f"الأتعاب: {total} د.ك" + (f"، ومدة التسليم {quotation['delivery_period']}" if quotation and quotation['delivery_period'] else "") +
                    ".\n\nإذا في أي استفسار أو تعديل خبرني، وبس توافق منبلش بالشغل." + sig_ar)
        else:
            subject = f"Quotation {qno} - {name} ({no})"
            body = (f"Dear {contact},\n\nPlease find attached our quotation for {name} (project {no}).\n"
                    f"Fee: KWD {total}" + (f", delivery period: {quotation['delivery_period']}" if quotation and quotation['delivery_period'] else "") +
                    ".\n\nLet me know if you have any questions or changes. We can start as soon as you confirm." + sig_en)
    elif kind == "boq":
        if lang == "ar":
            subject = f"تسليم جدول الكميات - {name} ({no})"
            body = (f"مرحبا {contact}،\n\nمرفق جدول الكميات لمشروع {name} (رقم {no})"
                    + (f" - {scope_text}" if scope_text else "") +
                    ".\nراجعوه وإذا في أي ملاحظة خبروني لنعدّلها." + sig_ar)
        else:
            subject = f"BOQ submission - {name} ({no})"
            body = (f"Dear {contact},\n\nPlease find attached the BOQ for {name} (project {no})"
                    + (f" - {scope_text}" if scope_text else "") +
                    ".\nPlease review it and let me know if you have any comments." + sig_en)
    else:
        fee, rec, bal = (fmt_kwd(money["fee"]), fmt_kwd(money["received"]), fmt_kwd(money["balance"])) if money else ("", "", "")
        if lang == "ar":
            subject = f"متابعة الدفعة - {name} ({no})"
            body = (f"مرحبا {contact}،\n\nإن شاء الله تكون بخير. حبيت ذكّرك بخصوص مشروع {name} (رقم {no}):\n\n"
                    f"الأتعاب المتفق عليها: {fee} د.ك\nالمبلغ المستلم: {rec} د.ك\nالمبلغ المتبقي: {bal} د.ك\n\n"
                    "إذا بتقدر تحوّل المبلغ المتبقي بأقرب وقت بكون ممنونك، وإذا في شي مو واضح خبرني." + sig_ar)
        else:
            subject = f"Payment follow-up - {name} ({no})"
            body = (f"Dear {contact},\n\nI hope you are well. A friendly reminder about {name} (project {no}):\n\n"
                    f"Agreed fee: KWD {fee}\nAmount received: KWD {rec}\nOutstanding balance: KWD {bal}\n\n"
                    "I would appreciate it if the balance could be transferred at your earliest convenience." + sig_en)
    return dict(to=client["email"] or "", subject=subject, body=body)


def build_eml(settings, to, subject, body, attachments):
    """attachments: list of (filename, path). Opens in Outlook / Mail as an unsent draft with files attached."""
    msg = EmailMessage()
    msg["From"] = f"{settings.get('company_name', 'BASE Design')} <{settings.get('company_email', '')}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="basedesign.local")
    msg["X-Unsent"] = "1"
    msg.set_content(body)
    for filename, path in attachments:
        ctype, _ = mimetypes.guess_type(filename)
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        with open(path, "rb") as fh:
            msg.add_attachment(fh.read(), maintype=maintype, subtype=subtype, filename=filename)
    return msg


def smtp_configured():
    return bool(os.environ.get("BASE_SMTP_HOST") and os.environ.get("BASE_SMTP_USER") and os.environ.get("BASE_SMTP_PASSWORD"))


def smtp_send(msg):
    del msg["X-Unsent"]
    host = os.environ["BASE_SMTP_HOST"]
    port = int(os.environ.get("BASE_SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls()
        s.login(os.environ["BASE_SMTP_USER"], os.environ["BASE_SMTP_PASSWORD"])
        s.send_message(msg)
