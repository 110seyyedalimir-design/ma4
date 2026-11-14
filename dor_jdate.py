"""Tehran time + Jalali (Persian) calendar helpers, no external dependency besides tzdata."""
import datetime as _dt
import re

try:
    from zoneinfo import ZoneInfo
    TEHRAN = ZoneInfo("Asia/Tehran")
except Exception:  # pragma: no cover
    TEHRAN = _dt.timezone(_dt.timedelta(hours=3, minutes=30))

FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
TO_EN = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def fa(x) -> str:
    return str(x).translate(FA_DIGITS)


def g2j(gy, gm, gd):
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + (365 * gy) + ((gy2 + 3) // 4) - ((gy2 + 99) // 100) + ((gy2 + 399) // 400) + gd + g_d_m[gm - 1]
    jy = -1595 + (33 * (days // 12053))
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + (days // 31)
        jd = 1 + (days % 31)
    else:
        jm = 7 + ((days - 186) // 30)
        jd = 1 + ((days - 186) % 30)
    return jy, jm, jd


def j2g(jy, jm, jd):
    jy += 1595
    days = -355668 + (365 * jy) + ((jy // 33) * 8) + (((jy % 33) + 3) // 4) + jd
    days += (jm - 1) * 31 if jm < 7 else ((jm - 7) * 30) + 186
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    leap = (gy % 4 == 0 and gy % 100 != 0) or (gy % 400 == 0)
    sal_a = [0, 31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 0
    while gm < 13 and gd > sal_a[gm]:
        gd -= sal_a[gm]
        gm += 1
    return gy, gm, gd


def tehran(ts: float) -> _dt.datetime:
    return _dt.datetime.fromtimestamp(ts, TEHRAN)


def jalali(ts: float, time=True) -> str:
    d = tehran(ts)
    jy, jm, jd = g2j(d.year, d.month, d.day)
    s = f"{jy}/{jm:02d}/{jd:02d}"
    if time:
        s += f" {d.hour:02d}:{d.minute:02d}"
    return fa(s)


def greg(ts: float, time=True) -> str:
    d = tehran(ts)
    return d.strftime("%Y-%m-%d %H:%M" if time else "%Y-%m-%d")


def both(ts: float) -> str:
    return f"{jalali(ts)}  ({greg(ts)})"


def parse_date(text: str):
    """Parses '1405/08/15', '1405-8-15 18:30', '2026-11-06' (Tehran time). Returns unix ts or None.
    Without a time, the end of that day (23:59) is used."""
    s = (text or "").translate(TO_EN).strip()
    m = re.match(r"^(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})(?:[ T]+(\d{1,2}):(\d{2}))?$", s)
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hh, mm = (int(m.group(4)), int(m.group(5))) if m.group(4) else (23, 59)
    try:
        if y < 1700:
            y, mo, d = j2g(y, mo, d)
        dt = _dt.datetime(y, mo, d, hh, mm, tzinfo=TEHRAN)
    except Exception:
        return None
    return int(dt.timestamp())


def ago(ts: float, now: float) -> str:
    s = max(0, int(now - ts))
    if s < 60:
        return "لحظاتی پیش"
    if s < 3600:
        return fa(s // 60) + " دقیقه پیش"
    if s < 86400:
        return fa(s // 3600) + " ساعت پیش"
    return fa(s // 86400) + " روز پیش"


def left(sec: float) -> str:
    """Human remaining time: «۲۹ روز و ۵ ساعت» / «۳ ساعت و ۱۰ دقیقه» / «۱۲ دقیقه»."""
    s = max(0, int(sec))
    d, h, m = s // 86400, (s % 86400) // 3600, (s % 3600) // 60
    if d:
        return fa(d) + " روز" + (f" و {fa(h)} ساعت" if h and d < 7 else "")
    if h:
        return fa(h) + " ساعت" + (f" و {fa(m)} دقیقه" if m else "")
    return fa(max(1, m)) + " دقیقه"


def dur(sec: int) -> str:
    if sec % 86400 == 0:
        return fa(sec // 86400) + " روز"
    if sec % 3600 == 0:
        return fa(sec // 3600) + " ساعت"
    return fa(round(sec / 60)) + " دقیقه"
