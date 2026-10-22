"""Noxin sleep screen: собирает данные и рисует BMP 480x800 для Xteink X4 Pro (CrossPoint).

Запуск:  python render.py            — живые данные из интернета
         python render.py --demo     — тестовые данные (без сети)
Результат: sleep.bmp и sleep_preview.png в текущей папке.
"""
import calendar, datetime as dt, json, math, os, sys, urllib.request
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont

TZ = ZoneInfo("Asia/Almaty")
LAT, LON, CITY = 51.17, 71.45, "Астана"
QUOTE = "Если не знаешь, к какой гавани плывёшь, ни один ветер не будет попутным."
AUTHOR = "— Сенека, «Письма к Луцилию», LXXI"
INSTAGRAM = "@sadykus"
MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль",
          "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


# ---------------- данные ----------------
def get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "noxin-screen/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def kind_from_code(c):
    if c is None: return None
    if c == 0: return "sun"
    if c in (1, 2): return "part"
    if c in (71, 73, 75, 77, 85, 86): return "snow"
    if 51 <= c <= 67 or 80 <= c <= 82 or c >= 95: return "rain"
    return "cloud"


def fetch_weather(monday, lat=LAT, lon=LON):
    """Open-Meteo: Пн–Вс текущей недели (прошедшие дни тоже, через past_days)."""
    url = ("https://api.open-meteo.com/v1/forecast"
           f"?latitude={lat}&longitude={lon}&timezone=Asia%2FAlmaty"
           "&daily=weather_code,temperature_2m_max,temperature_2m_min,wind_speed_10m_max"
           "&wind_speed_unit=ms&past_days=7&forecast_days=8")
    d = json.loads(get(url))["daily"]
    by_date = {d["time"][i]: i for i in range(len(d["time"]))}
    out = []
    for k in range(7):
        day = monday + dt.timedelta(days=k)
        i = by_date.get(day.isoformat())
        if i is None or d["temperature_2m_max"][i] is None:
            out.append((DAYS[k], day.day, None, None, None, None))
            continue
        out.append((DAYS[k], day.day, kind_from_code(d["weather_code"][i]),
                    round(d["temperature_2m_max"][i]), round(d["temperature_2m_min"][i]),
                    round(d["wind_speed_10m_max"][i])))
    return out


def fetch_rates(today):
    """Официальные курсы Нацбанка РК на сегодня."""
    xml = get(f"https://nationalbank.kz/rss/get_rates.cfm?fdate={today:%d.%m.%Y}")
    root = ET.fromstring(xml)
    rates = {}
    for it in root.iter("item"):
        code = (it.findtext("title") or "").strip()
        val = float((it.findtext("description") or "0").replace(",", "."))
        q = float(it.findtext("quant") or 1)
        rates[code] = val / q
    return rates


def fetch_btc():
    data = json.loads(get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"))
    return data["bitcoin"]["usd"]


def money(v):  # 452.12 -> "452,12 ₸"
    return f"{v:,.2f}".replace(",", " ").replace(".", ",") + " ₸"


def week_bounds():
    today = dt.datetime.now(TZ).date()
    return today, today - dt.timedelta(days=today.weekday())


def weather_for(lat, lon):
    """Погода Пн–Вс для точки; при ошибке — прочерки."""
    _, monday = week_bounds()
    try:
        return fetch_weather(monday, lat, lon)
    except Exception as e:
        print("WARN weather:", e, file=sys.stderr)
        return [(DAYS[k], (monday + dt.timedelta(days=k)).day, None, None, None, None) for k in range(7)]


def market():
    """Курсы НБ РК + BTC (общие для всех пользователей)."""
    today, _ = week_bounds()
    usd = eur = rub = btc = None
    try:
        r = fetch_rates(today)
        usd, eur, rub = r.get("USD"), r.get("EUR"), r.get("RUB")
    except Exception as e:
        print("WARN rates:", e, file=sys.stderr)
    try:
        btc = fetch_btc()
    except Exception as e:
        print("WARN btc:", e, file=sys.stderr)
    return dict(rates_date=f"{today:%d.%m}", usd=usd, eur=eur, rub=rub, btc=btc)


def geocode(name):
    """Название города → (lat, lon, имя по-русски) через Open-Meteo, или None."""
    from urllib.parse import quote as q
    data = json.loads(get(f"https://geocoding-api.open-meteo.com/v1/search?name={q(name)}&count=1&language=ru"))
    res = (data.get("results") or [None])[0]
    if not res:
        return None
    return res["latitude"], res["longitude"], res.get("name", name)


def collect(demo=False, lat=LAT, lon=LON, city=CITY):
    today, monday = week_bounds()
    if demo:
        wx = [("Пн", 19, "snow", 3, 0, 6), ("Вт", 20, "part", 4, 1, 7), ("Ср", 21, "cloud", 8, 2, 8),
              ("Чт", 22, "rain", 5, -2, 7), ("Пт", 23, "part", 0, -6, 5), ("Сб", 24, "sun", 2, -6, 4),
              ("Вс", 25, "part", 5, -4, 3)]
        return dict(today=dt.date(2026, 10, 22), monday=dt.date(2026, 10, 19), wx=wx, city=city,
                    rates_date="22.10", usd=452.12, eur=508.95, rub=5.41, btc=82771)
    errors = []
    try:
        wx = fetch_weather(monday, lat, lon)
    except Exception as e:
        errors.append(f"weather: {e}")
        wx = [(DAYS[k], (monday + dt.timedelta(days=k)).day, None, None, None, None) for k in range(7)]
    try:
        r = fetch_rates(today)
        usd, eur, rub = r.get("USD"), r.get("EUR"), r.get("RUB")
    except Exception as e:
        errors.append(f"rates: {e}")
        usd = eur = rub = None
    try:
        btc = fetch_btc()
    except Exception as e:
        errors.append(f"btc: {e}")
        btc = None
    for e in errors:
        print("WARN", e, file=sys.stderr)
    return dict(today=today, monday=monday, wx=wx, city=city, rates_date=f"{today:%d.%m}",
                usd=usd, eur=eur, rub=rub, btc=btc)


# ---------------- рисование ----------------
W, H = 480, 800
FONT_DIRS = ["/usr/share/fonts/truetype/dejavu/", os.path.join(os.path.dirname(__file__), "fonts/")]


def F(name, size):
    for d in FONT_DIRS:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    raise FileNotFoundError(name)


SERIF_I, SANS, SANS_B = "DejaVuSerif-Italic.ttf", "DejaVuSans.ttf", "DejaVuSans-Bold.ttf"
K, G1, GRAY, WH = 0, 85, 140, 255  # на этом e-ink светлее ~150 почти пропадает


def render(data, quote=QUOTE, author=AUTHOR, instagram=INSTAGRAM, out=None):
    """Рисует экран. Возвращает PIL.Image; если out задан — сохраняет out.bmp и out_preview.png."""
    img = Image.new("L", (W, H), WH)
    d = ImageDraw.Draw(img)
    tl = lambda t, f: d.textlength(t, font=f)
    M = 24

    def center(x0, x1, y, t, f, fill=K):
        d.text((x0 + (x1 - x0 - tl(t, f)) / 2, y), t, font=f, fill=fill)

    def wrap(text, font, width):
        lines, cur = [], ""
        for w in text.split():
            t = (cur + " " + w).strip()
            if tl(t, font) > width:
                lines.append(cur); cur = w
            else:
                cur = t
        return lines + [cur]

    def section(y, title):
        f = F(SANS_B, 15)
        d.text((M, y), title.upper(), font=f, fill=K)
        x = M + tl(title.upper(), f) + 10
        d.line([(x, y + 8), (W - M, y + 8)], fill=G1, width=1)

    # 1. фраза
    # длинная фраза — уменьшаем шрифт, чтобы влезла максимум в 4 строки
    for size in (24, 22, 20, 18, 16):
        fq = F(SERIF_I, size)
        lines = wrap(quote, fq, W - 2 * M)
        if len(lines) <= 4:
            break
    lines = lines[:5]
    step = int(size * 1.38)
    y = 26
    for ln in lines:
        d.text((M, y), ln, font=fq, fill=K); y += step
    if author:
        a = author if author.startswith("—") else "— " + author
        fa = F(SANS_B, 14)
        d.text((W - M - tl(a, fa), y + 4), a, font=fa, fill=K)
    y += 34

    # 2. календарь
    today, monday = data["today"], data["monday"]
    week_no = monday.isocalendar()[1]
    section(y, f"{MONTHS[today.month - 1]} {today.year} · текущая неделя {week_no}")
    y += 26
    cw = (W - 2 * M) / 7
    for i, n in enumerate(DAYS):
        center(M + i * cw, M + (i + 1) * cw, y, n, F(SANS_B, 15))
    y += 22
    week_days = {monday + dt.timedelta(days=k) for k in range(7)}
    weeks = calendar.Calendar(0).monthdatescalendar(today.year, today.month)
    ch = 40 if len(weeks) <= 5 else 34
    fn, fnb = F(SANS, 17), F(SANS_B, 17)
    for r, wk in enumerate(weeks):
        ry = y + r * ch
        cur = monday in wk
        if cur:
            d.rounded_rectangle([M - 4, ry, W - M + 4, ry + ch - 4], radius=8, fill=GRAY, outline=K, width=2)
        for c, day in enumerate(wk):
            if day.month != today.month:
                continue
            mx, my = M + c * cw + cw / 2, ry + (ch - 4) / 2
            f = fnb if (c >= 5 or cur) else fn
            t = str(day.day)
            bb = d.textbbox((0, 0), t, font=f)
            d.text((mx - (bb[2] + bb[0]) / 2, my - (bb[3] + bb[1]) / 2), t, font=f, fill=K)
    y += len(weeks) * ch + 22

    # 3. погода
    section(y, f"Погода · {data.get('city', CITY)}")
    y += 26

    def sun(cx, cy, r):
        for k in range(8):
            a = math.radians(k * 45)
            d.line([(cx + (r + 3) * math.cos(a), cy + (r + 3) * math.sin(a)),
                    (cx + (r + 7) * math.cos(a), cy + (r + 7) * math.sin(a))], fill=K, width=2)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=K)

    def cloud(cx, cy):
        boxes = ([cx - 15, cy - 3, cx + 1, cy + 11], [cx - 7, cy - 11, cx + 11, cy + 7],
                 [cx + 1, cy - 3, cx + 16, cy + 11])
        for b in boxes:
            d.ellipse([b[0] - 2, b[1] - 2, b[2] + 2, b[3] + 2], fill=K)
        d.rectangle([cx - 9, cy + 2, cx + 11, cy + 13], fill=K)
        for b in boxes:
            d.ellipse(b, fill=GRAY)
        d.rectangle([cx - 7, cy + 2, cx + 9, cy + 11], fill=GRAY)

    def flake(cx, cy):
        for k in range(3):
            a = math.radians(k * 60)
            d.line([(cx - 4 * math.cos(a), cy - 4 * math.sin(a)),
                    (cx + 4 * math.cos(a), cy + 4 * math.sin(a))], fill=K, width=2)

    sg = lambda v: ("+" if v > 0 else "") + str(v) + "°"
    fdw, fdt, fhi, flo, fw = F(SANS_B, 16), F(SANS_B, 13), F(SANS_B, 18), F(SANS_B, 15), F(SANS_B, 13)
    for i, (dn, dd, kind, hi, lo, wind) in enumerate(data["wx"]):
        x0, x1 = M + i * cw, M + (i + 1) * cw
        mx = (x0 + x1) / 2
        center(x0, x1, y, dn, fdw)
        center(x0, x1, y + 18, str(dd), fdt)
        iy = y + 52
        if kind is None:
            center(x0, x1, iy - 10, "—", F(SANS_B, 20))
            center(x0, x1, y + 80, "н/д", F(SANS_B, 13))
            continue
        if kind == "sun":
            sun(mx, iy, 8)
        elif kind == "part":
            sun(mx + 7, iy - 7, 6); cloud(mx - 3, iy + 2)
        elif kind == "snow":
            cloud(mx, iy - 3); flake(mx - 7, iy + 16); flake(mx + 7, iy + 16)
        elif kind == "rain":
            cloud(mx, iy - 3)
            for dx in (-7, 0, 7):
                d.line([(mx + dx, iy + 12), (mx + dx - 3, iy + 20)], fill=K, width=2)
        else:
            cloud(mx, iy)
        center(x0, x1, y + 76, sg(hi), fhi)
        center(x0, x1, y + 97, sg(lo), flo)
        center(x0, x1, y + 118, f"{wind} м/с", fw)
    y += 170

    # 4. курсы
    section(y, f"Курсы на {data['rates_date']} · Нацбанк РК")
    y += 26
    na = "—"
    rates = [("USD", money(data["usd"]) if data["usd"] else na),
             ("EUR", money(data["eur"]) if data["eur"] else na),
             ("RUB", money(data["rub"]) if data["rub"] else na),
             ("BTC", f"${data['btc']:,.0f}".replace(",", " ") if data["btc"] else na)]
    qw = (W - 2 * M) / 4
    for i, (code, val) in enumerate(rates):
        x0, x1 = M + i * qw, M + (i + 1) * qw
        center(x0, x1, y, code, F(SANS_B, 15))
        center(x0, x1, y + 18, val, F(SANS_B, 17))

    # 5. низ
    d.line([(M, H - 44), (W - M, H - 44)], fill=K, width=2)
    fs, fb = F(SANS_B, 15), F(SANS_B, 18)
    if instagram:
        d.text((M, H - 31), "для связи · Instagram", font=fs, fill=K)
        d.text((W - M - tl(instagram, fb), H - 33), instagram, font=fb, fill=K)
    else:
        d.text((M, H - 31), "Noxen Frame", font=fs, fill=K)

    if out:
        img.save(out + ".bmp")
        img.save(out + "_preview.png")
    return img


if __name__ == "__main__":
    render(collect(demo="--demo" in sys.argv), out="sleep")
    print("ok")
