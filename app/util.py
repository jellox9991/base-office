"""Parsing and formatting helpers: KWD (fils), metres (mm), Kuwait time."""
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

KUWAIT_TZ = timezone(timedelta(hours=3), "Asia/Kuwait")  # Kuwait has no daylight saving

AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,")


def now_kw():
    return datetime.now(KUWAIT_TZ)


def now_iso():
    return now_kw().isoformat(timespec="seconds")


def today_kw():
    return now_kw().date()


def normalise_number_text(text):
    if text is None:
        return ""
    return str(text).translate(AR_DIGITS).replace(",", "").replace(" ", "").strip()


def parse_kwd(text):
    """'30', '30.000', '٣٠' -> fils int. Blank -> None. Raises ValueError for junk or >3 decimals."""
    t = normalise_number_text(text)
    if t == "":
        return None
    try:
        d = Decimal(t)
    except InvalidOperation:
        raise ValueError("Enter an amount in KWD, for example 30.000")
    if d.as_tuple().exponent < -3:
        raise ValueError("KWD has at most three decimals (fils)")
    return int((d * 1000).to_integral_value(ROUND_HALF_UP))


def fmt_kwd(fils, blank="—"):
    if fils is None:
        return blank
    sign = "-" if fils < 0 else ""
    fils = abs(int(fils))
    return f"{sign}{fils // 1000:,}.{fils % 1000:03d}"


def parse_m(text):
    """Metres text ('+4.600', '-3.4', '0') -> int mm. Blank -> None (distinct from 0)."""
    t = normalise_number_text(text).replace("+", "")
    if t == "":
        return None
    try:
        d = Decimal(t)
    except InvalidOperation:
        raise ValueError(f"'{text}' is not a number in metres")
    return int((d * 1000).to_integral_value(ROUND_HALF_UP))


def fmt_m(mm, signed=False, blank=""):
    if mm is None:
        return blank
    sign = "-" if mm < 0 else ("+" if signed and mm > 0 else ("±" if signed else ""))
    mm = abs(int(mm))
    return f"{sign}{mm // 1000}.{mm % 1000:03d}"


def parse_float(text):
    t = normalise_number_text(text)
    if t == "":
        return None
    try:
        return float(t)
    except ValueError:
        raise ValueError(f"'{text}' is not a number")


def parse_int(text):
    t = normalise_number_text(text)
    if t == "":
        return None
    try:
        return int(t)
    except ValueError:
        raise ValueError(f"'{text}' is not a whole number")


def parse_date(text):
    t = normalise_number_text(text) if text else ""
    if not t:
        return None
    try:
        return date.fromisoformat(t).isoformat()
    except ValueError:
        raise ValueError("Use the date picker (YYYY-MM-DD)")


def fmt_date(iso, blank=""):
    if not iso:
        return blank
    try:
        d = date.fromisoformat(iso[:10])
    except ValueError:
        return iso
    return d.strftime("%d/%m/%Y")


def fmt_dt(iso):
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    return dt.astimezone(KUWAIT_TZ).strftime("%d/%m/%Y %H:%M")


def wa_link(phone):
    digits = "".join(c for c in normalise_number_text(phone or "") if c.isdigit())
    if not digits:
        return None
    if len(digits) == 8:          # local Kuwait mobile
        digits = "965" + digits
    return "https://wa.me/" + digits
