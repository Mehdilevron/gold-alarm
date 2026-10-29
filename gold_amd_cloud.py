#!/usr/bin/env python3
"""Cloud AMD watcher (Power of 3) on ALL sessions — runs with the Mac off.

For each session that has finished today (Dubai clock) its own high and low
become the accumulation range. If a later 5-minute candle trades beyond that
range (manipulation) and then a 5-minute candle CLOSES BACK INSIDE it, the
setup is pushed immediately — entry, stop beyond the sweep wick, three targets.

The midpoint is confirmation only, never the trigger.

State is kept in amd_state.json and committed back by the workflow so each
setup fires once.
"""
import datetime as dt, json, os, urllib.request
import pandas as pd, yfinance as yf

TOPIC = "https://ntfy.sh/claude-mt5-alerts-7f3k2"
STATE = "amd_state.json"
DUBAI = dt.timezone(dt.timedelta(hours=4))

SESSIONS = {"ASIA": (3, 11), "LONDON": (11, 16), "NEW YORK": (16, 23)}
MIN_RANGE = 6.0
SWEEP_MIN = 1.0
BUFFER = 2.0
LOOKBACK_BARS = 24           # a sweep must be this recent to still count

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")
CAL_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
KEYWORDS = ("CPI", "PPI", "FOMC", "Federal Funds", "Non-Farm", "Nonfarm",
            "Unemployment", "Powell", "PCE", "GDP", "Retail Sales", "Jobless",
            "ADP", "ISM", "Payroll", "Rate", "Inflation", "Treasury",
            "Consumer Confidence", "Durable Goods", "Beige Book")
LOCK_PRE, LOCK_POST = 30, 20


def push(title, body, priority="high"):
    req = urllib.request.Request(TOPIC, data=body.encode("utf-8"),
        headers={"Title": title, "Priority": priority, "Tags": "chart"})
    try: urllib.request.urlopen(req, timeout=20)
    except Exception as e: print("push error:", e)


def bars(interval, period):
    d = yf.download("GC=F", period=period, interval=interval,
                    auto_adjust=False, progress=False)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    d = d.dropna()
    idx = d.index.tz_convert(DUBAI) if d.index.tz is not None \
        else d.index.tz_localize("UTC").tz_convert(DUBAI)
    d.index = idx
    return d


def news_lock(now):
    try:
        req = urllib.request.Request(CAL_URL, headers={"User-Agent": UA})
        data = json.load(urllib.request.urlopen(req, timeout=25))
    except Exception:
        return None
    for e in data:
        if e.get("country") != "USD": continue
        if e.get("impact") not in ("High", "Medium"): continue
        if not any(k.lower() in e.get("title", "").lower() for k in KEYWORDS): continue
        try: ts = dt.datetime.fromisoformat(e["date"])
        except Exception: continue
        mins = (ts.timestamp() - now) / 60.0
        if -LOCK_POST <= mins <= LOCK_PRE:
            return e["title"]
    return None


def main():
    d5 = bars("5m", "5d")
    if len(d5) < 20:
        print("not enough 5m data"); return
    today = d5.index.max().date()
    now_ts = dt.datetime.now(DUBAI).timestamp()

    st = json.load(open(STATE)) if os.path.exists(STATE) else {}
    if st.get("day") != str(today):
        st = {"day": str(today), "fired": []}

    day = d5[d5.index.date == today]
    last = d5.iloc[-1]                       # most recently closed 5m candle
    last_close = float(last["Close"])
    locked = news_lock(now_ts)
    changed = False

    for name, (s, e) in SESSIONS.items():
        win = day[(day.index.hour >= s) & (day.index.hour < e)]
        if not len(win):
            continue
        # the session must be finished for its range to be tradable
        if dt.datetime.now(DUBAI).hour < e:
            continue
        hi, lo = float(win["High"].max()), float(win["Low"].min())
        rng = hi - lo
        if rng < MIN_RANGE:
            continue
        mid = (hi + lo) / 2.0

        after = day[day.index > win.index.max()].tail(LOOKBACK_BARS)
        if len(after) < 2:
            continue

        swept_high = float(after["High"].max()) > hi + SWEEP_MIN
        swept_low = float(after["Low"].min()) < lo - SWEEP_MIN
        tag = f" (news: {locked})" if locked else ""

        if swept_high and last_close < hi:
            key = f"{name}-short"
            if key in st["fired"]: continue
            sweep_px = float(after["High"].max())
            sl = sweep_px + BUFFER
            tp1, tp2, tp3 = mid, lo, lo - rng
            rr = abs(tp2 - last_close) / max(abs(sl - last_close), 0.1)
            push(f"GOLD SHORT - {name} sweep rejected{tag}",
                 f"A: {name} accumulated {lo:.1f} - {hi:.1f}\n"
                 f"M: the high was swept to {sweep_px:.1f}\n"
                 f"D: a 5-minute candle has closed back INSIDE at {last_close:.1f}\n\n"
                 f"SHORT {last_close:.1f}  (or on a retest of {hi:.1f})\n"
                 f"SL {sl:.1f}  (above the sweep wick)\n"
                 f"TP1 {tp1:.1f}  TP2 {tp2:.1f}  TP3 {tp3:.1f}\n"
                 f"R:R to TP2  {rr:.1f}\n\n"
                 f"Entry is here, before the move. Losing the midpoint {mid:.1f} "
                 f"is confirmation only.\nInvalid if a 5m closes back above {hi:.1f}.")
            st["fired"].append(key); changed = True

        if swept_low and last_close > lo:
            key = f"{name}-long"
            if key in st["fired"]: continue
            sweep_px = float(after["Low"].min())
            sl = sweep_px - BUFFER
            tp1, tp2, tp3 = mid, hi, hi + rng
            rr = abs(tp2 - last_close) / max(abs(last_close - sl), 0.1)
            push(f"GOLD LONG - {name} sweep rejected{tag}",
                 f"A: {name} accumulated {lo:.1f} - {hi:.1f}\n"
                 f"M: the low was swept to {sweep_px:.1f}\n"
                 f"D: a 5-minute candle has closed back INSIDE at {last_close:.1f}\n\n"
                 f"LONG {last_close:.1f}  (or on a retest of {lo:.1f})\n"
                 f"SL {sl:.1f}  (below the sweep wick)\n"
                 f"TP1 {tp1:.1f}  TP2 {tp2:.1f}  TP3 {tp3:.1f}\n"
                 f"R:R to TP2  {rr:.1f}\n\n"
                 f"Entry is here, before the move. Reclaiming the midpoint {mid:.1f} "
                 f"is confirmation only.\nInvalid if a 5m closes back below {lo:.1f}.")
            st["fired"].append(key); changed = True

    json.dump(st, open(STATE, "w"))
    print("state:", st, "changed:", changed, "last close:", last_close)


if __name__ == "__main__":
    main()
