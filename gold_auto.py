#!/usr/bin/env python3
"""Cloud gold watcher — AMN style: 30m structure (BOS + sweep + zone),
5m zone tap entry, 1:3 target. Pushes to ntfy topic gold-alarm.
Runs one check per invocation (GitHub Actions cron)."""
import json, os
import pandas as pd, yfinance as yf, urllib.request

TOPIC = "https://ntfy.sh/gold-alarm"
STATE = "state.json"
RR = 3.0
SWING = 3

# Supply zones read off the OANDA:XAUUSD 4h chart on 25 Oct 2026.
# Spot prices; OFFSET converts them to GC=F futures (set via env if it drifts).
FIXED_SUPPLY = [
    (4134.86, 4149.59),
    (4147.92, 4163.22),
    (4190.93, 4208.78),
    (4210.93, 4231.96),
    (4306.34, 4325.25),
    (4388.45, 4413.85),
    (4428.25, 4449.77),
    (4498.29, 4523.57),
    (4618.77, 4645.19),
]
FIXED_DEMAND = []
OFFSET = float(os.environ.get("GOLD_SPOT_OFFSET", "0"))


def check_fixed(ltf, st):
    """Tap into a charted zone, then close back out of it, on the 5m."""
    bar = ltf.iloc[-1]
    hi, lo, cl = float(bar["High"]), float(bar["Low"]), float(bar["Close"])
    fired = False
    for low, high in FIXED_SUPPLY:
        lo_a, hi_a = low + OFFSET, high + OFFSET
        key = f"fx-supply:{round(low,2)}"
        if hi >= lo_a and cl < lo_a and st.get(key) != "hit":
            push("GOLD — SUPPLY ZONE",
                 f"Tapped {low:.2f}-{high:.2f} and closed back below.\n"
                 f"Price {cl:.2f}\nWaiting on BOS before acting.")
            st[key] = "hit"; fired = True
    for low, high in FIXED_DEMAND:
        lo_a, hi_a = low + OFFSET, high + OFFSET
        key = f"fx-demand:{round(high,2)}"
        if lo <= hi_a and cl > hi_a and st.get(key) != "hit":
            push("GOLD — DEMAND ZONE",
                 f"Tapped {low:.2f}-{high:.2f} and closed back above.\n"
                 f"Price {cl:.2f}\nWaiting on BOS before acting.")
            st[key] = "hit"; fired = True
    return fired


def push(title, body):
    req = urllib.request.Request(TOPIC, data=body.encode(),
        headers={"Title": title, "Priority": "high", "Tags": "moneybag"})
    urllib.request.urlopen(req, timeout=20)


def fetch(interval, period):
    d = yf.download("GC=F", period=period, interval=interval, auto_adjust=False, progress=False)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    return d.dropna()


def structure(d):
    h, l, c, o = d["High"].values, d["Low"].values, d["Close"].values, d["Open"].values
    n = len(d)
    lph = lpl = None
    bias = 0
    zone = None
    for i in range(SWING, n - SWING):
        if h[i] == h[i-SWING:i+SWING+1].max(): lph = h[i]
        if l[i] == l[i-SWING:i+SWING+1].min(): lpl = l[i]
        if lph is not None and c[i] > lph and c[i-1] <= lph:
            bias = 1
            swept = l[max(0, i-SWING*2):i+1].min() < (lpl if lpl else 1e9)
            k = next((j for j in range(1, 21) if c[i-j] < o[i-j]), 1)
            zone = ("demand", float(max(o[i-k], c[i-k])), float(l[i-k]), bool(swept))
            lph = None
        if lpl is not None and c[i] < lpl and c[i-1] >= lpl:
            bias = -1
            swept = h[max(0, i-SWING*2):i+1].max() > (lph if lph else -1e9)
            k = next((j for j in range(1, 21) if c[i-j] > o[i-j]), 1)
            zone = ("supply", float(h[i-k]), float(min(o[i-k], c[i-k])), bool(swept))
            lpl = None
    ema200 = pd.Series(c).ewm(span=200).mean().values[-1]
    return bias, zone, bool(c[-1] > ema200)


def load():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}


def main():
    htf = fetch("30m", "60d")
    ltf = fetch("5m", "5d")
    if len(htf) < 60 or len(ltf) < 5:
        print("not enough data"); return
    st_fixed = load()
    if check_fixed(ltf, st_fixed):
        json.dump(st_fixed, open(STATE, "w"))
    bias, zone, above = structure(htf)
    if zone is None:
        print("no zone"); return
    side, top, bot, swept = zone
    bar = ltf.iloc[-1]
    hi, lo, cl, op = float(bar["High"]), float(bar["Low"]), float(bar["Close"]), float(bar["Open"])
    st = load()
    key = f"{side}:{round(top,2)}:{round(bot,2)}"
    if st.get("last") == key:
        print("already alerted on this zone"); return

    fired = None
    if side == "demand" and bias == 1 and above and swept and bot <= lo <= top and cl > op:
        e, s = cl, bot
        t = e + (e - s) * RR
        push("GOLD BUY", f"Entry {e:.2f}\nStop {s:.2f}\nTarget {t:.2f}\nAMN 30m structure, 5m tap, 1:3")
        fired = "buy"
    elif side == "supply" and bias == -1 and not above and swept and bot <= hi <= top and cl < op:
        e, s = cl, top
        t = e - (s - e) * RR
        push("GOLD SELL", f"Entry {e:.2f}\nStop {s:.2f}\nTarget {t:.2f}\nAMN 30m structure, 5m tap, 1:3")
        fired = "sell"

    if fired:
        st["last"] = key
        json.dump(st, open(STATE, "w"))
        print("alert sent:", fired)
    else:
        print("no setup; price", cl, "bias", bias, "zone", side, top, bot)


if __name__ == "__main__":
    main()
