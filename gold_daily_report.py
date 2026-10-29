#!/usr/bin/env python3
"""Daily gold report -> phone push, one message, every morning.

Pure price action, no indicators:
  - where price is now, and the day's and week's extremes
  - Asian, London and New York session high/low from the last 24 hours
  - previous day and previous week high/low
  - 30-minute market structure (higher highs / lower lows, change of character)
  - the AMD ranges that are live today and what would trigger a setup
  - the USD releases due today, listed inside the report only

Runs in GitHub Actions so the Mac can be off.
"""
import datetime, urllib.request, json
import pandas as pd, yfinance as yf

TOPIC = "https://ntfy.sh/claude-mt5-alerts-7f3k2"
SWING = 3
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")
CAL_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
KEYWORDS = ("CPI", "PPI", "FOMC", "Federal Funds", "Non-Farm", "Nonfarm",
            "Unemployment", "Powell", "PCE", "GDP", "Retail Sales", "Jobless",
            "ADP", "ISM", "Payroll", "Rate", "Inflation", "Treasury",
            "Consumer Confidence", "Durable Goods", "Beige Book")

# session windows in Dubai time (UTC+4)
SESSIONS = {"ASIA": (3, 11), "LONDON": (11, 16), "NEW YORK": (16, 23)}


def push(title, body, priority="default"):
    req = urllib.request.Request(TOPIC, data=body.encode("utf-8"),
        headers={"Title": title, "Priority": priority, "Tags": "chart"})
    urllib.request.urlopen(req, timeout=20)


def fetch(interval, period):
    d = yf.download("GC=F", period=period, interval=interval,
                    auto_adjust=False, progress=False)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    return d.dropna()


def fetch_t(ticker, interval, period):
    d = yf.download(ticker, period=period, interval=interval,
                    auto_adjust=False, progress=False)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    return d.dropna()


def structure(d):
    h, l, c = d["High"].values, d["Low"].values, d["Close"].values
    hs, ls = [], []
    for i in range(SWING, len(d) - SWING):
        if h[i] == max(h[i - SWING:i + SWING + 1]): hs.append((i, h[i]))
        if l[i] == min(l[i - SWING:i + SWING + 1]): ls.append((i, l[i]))
    bias, choch = "unclear", None
    if len(hs) >= 2 and len(ls) >= 2:
        up = hs[-1][1] > hs[-2][1] and ls[-1][1] > ls[-2][1]
        dn = hs[-1][1] < hs[-2][1] and ls[-1][1] < ls[-2][1]
        bias = "bullish" if up else "bearish" if dn else "ranging"
        if bias == "bullish" and c[-1] < ls[-1][1]:
            choch = f"change of character down through {ls[-1][1]:.1f}"
        if bias == "bearish" and c[-1] > hs[-1][1]:
            choch = f"change of character up through {hs[-1][1]:.1f}"
    last_h = hs[-1][1] if hs else None
    last_l = ls[-1][1] if ls else None
    return bias, choch, last_h, last_l


def sessions_from(d30):
    """session high/low over the last 24 hours, Dubai clock"""
    idx = d30.index.tz_convert("Asia/Dubai") if d30.index.tz is not None \
        else d30.index.tz_localize("UTC").tz_convert("Asia/Dubai")
    d = d30.copy(); d.index = idx
    cut = idx.max() - pd.Timedelta(hours=26)
    d = d[d.index >= cut]
    out = {}
    for name, (s, e) in SESSIONS.items():
        m = d[(d.index.hour >= s) & (d.index.hour < e)]
        if len(m):
            out[name] = (float(m["High"].max()), float(m["Low"].min()))
    return out


def news_today():
    try:
        req = urllib.request.Request(CAL_URL, headers={"User-Agent": UA})
        data = json.load(urllib.request.urlopen(req, timeout=25))
    except Exception:
        return ["calendar feed unavailable"]
    today = datetime.datetime.utcnow().date()
    out = []
    for e in data:
        if e.get("country") != "USD": continue
        if e.get("impact") not in ("High", "Medium"): continue
        if not any(k.lower() in e.get("title", "").lower() for k in KEYWORDS): continue
        try:
            ts = datetime.datetime.fromisoformat(e["date"])
        except Exception:
            continue
        local = ts.astimezone(datetime.timezone(datetime.timedelta(hours=4)))
        if local.date() != (datetime.datetime.now(
                datetime.timezone(datetime.timedelta(hours=4)))).date():
            continue
        out.append(f"  {local.strftime('%H:%M')}  {e['title']} ({e['impact']})")
    return out or ["  nothing of consequence"]


def main():
    d30 = fetch("30m", "10d")
    d1d = fetch("1d", "3mo")
    price = float(d30["Close"].iloc[-1])

    bias, choch, lh, ll = structure(d30)
    sess = sessions_from(d30)

    pd_h, pd_l = float(d1d["High"].iloc[-2]), float(d1d["Low"].iloc[-2])
    wk = d1d.tail(10)
    pw = wk.iloc[-6:-1]
    pw_h, pw_l = float(pw["High"].max()), float(pw["Low"].min())

    L = []
    L.append(f"Gold {price:.1f}")
    L.append(f"30m structure: {bias}")
    if choch: L.append(f"  {choch}")
    if lh and ll: L.append(f"  last swing high {lh:.1f}, last swing low {ll:.1f}")
    L.append("")
    L.append("SESSIONS, last 24h")
    for name in ("ASIA", "LONDON", "NEW YORK"):
        if name in sess:
            hi, lo = sess[name]
            L.append(f"  {name}  {lo:.1f} - {hi:.1f}   (mid {(hi+lo)/2:.1f})")
    L.append("")
    L.append(f"Previous day  {pd_l:.1f} - {pd_h:.1f}")
    L.append(f"Previous week {pw_l:.1f} - {pw_h:.1f}")
    L.append("")
    L.append("AMD, what I am waiting for")
    for name in ("ASIA", "LONDON", "NEW YORK"):
        if name in sess:
            hi, lo = sess[name]
            L.append(f"  {name}: sweep above {hi:.1f} then a 5m close back under "
                     f"= short; sweep below {lo:.1f} then a 5m close back over = long")
    L.append("")
    L.append("MARKET AROUND GOLD")
    for label, tick in (("DXY", "DX-Y.NYB"), ("US 10Y", "^TNX"),
                        ("S&P 500", "^GSPC"), ("Oil WTI", "CL=F"),
                        ("Silver", "SI=F"), ("Bitcoin", "BTC-USD")):
        try:
            m = fetch_t(tick, "1d", "7d")
            c, p = float(m["Close"].iloc[-1]), float(m["Close"].iloc[-2])
            chg = (c - p) / p * 100
            L.append(f"  {label}  {c:,.2f}  {chg:+.2f}%")
        except Exception:
            L.append(f"  {label}  unavailable")
    L.append("")
    L.append("USD releases today")
    L += news_today()
    L.append("")
    L.append("Setups come as their own push the moment the 5-minute candle "
             "closes back inside the range.")

    push(f"GOLD DAILY REPORT  {datetime.date.today().isoformat()}",
         "\n".join(L), priority="default")
    print("\n".join(L))


if __name__ == "__main__":
    main()
