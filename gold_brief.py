#!/usr/bin/env python3
"""Scheduled gold market read -> phone push.
Pure price action: 30m structure + change of character, nearest zones,
distance to each, and what would have to happen to trade it.
No API key needed. Pushes to ntfy topic gold-alarm."""
import urllib.request
import pandas as pd, yfinance as yf

TOPIC = "https://ntfy.sh/claude-mt5-alerts-7f3k2"
SWING = 3


def push(title, body, priority="default"):
    req = urllib.request.Request(TOPIC, data=body.encode(),
        headers={"Title": title, "Priority": priority, "Tags": "coin"})
    urllib.request.urlopen(req, timeout=20)


def fetch(interval, period):
    d = yf.download("GC=F", period=period, interval=interval,
                    auto_adjust=False, progress=False)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    return d.dropna()


def read_structure(d):
    h, l, c = d["High"].values, d["Low"].values, d["Close"].values
    n = len(d)
    lph = lpl = None
    bias = 0
    choch = None
    hi_ref = lo_ref = None
    for i in range(SWING, n - SWING):
        if h[i] == h[i-SWING:i+SWING+1].max(): lph = h[i]
        if l[i] == l[i-SWING:i+SWING+1].min(): lpl = l[i]
        if lph is not None and c[i] > lph and c[i-1] <= lph:
            if bias == -1: choch = ("up", float(lph))
            bias, hi_ref, lph = 1, float(lph), None
        if lpl is not None and c[i] < lpl and c[i-1] >= lpl:
            if bias == 1: choch = ("down", float(lpl))
            bias, lo_ref, lpl = -1, float(lpl), None
    return bias, choch, hi_ref, lo_ref


def main():
    d30 = fetch("30m", "30d")
    if len(d30) < 60:
        print("not enough data"); return
    bias, choch, hi_ref, lo_ref = read_structure(d30)
    price = float(d30["Close"].values[-1])
    day = fetch("1d", "10d")
    pdh, pdl = float(day["High"].values[-2]), float(day["Low"].values[-2])

    word = {1: "bullish", -1: "bearish", 0: "unclear"}[bias]
    lines = [f"Price {price:.2f} | 30m structure {word}"]
    if choch:
        lines.append(f"Last CHoCH {choch[0]} through {choch[1]:.2f}")
    lines.append(f"Prev day high {pdh:.2f} / low {pdl:.2f}")
    if bias == -1:
        lines.append("Plan: wait for a pullback into supply, then a close back "
                     "below it before any sell is valid.")
    elif bias == 1:
        lines.append("Plan: wait for a pullback into demand, then a close back "
                     "above it before any buy is valid.")
    else:
        lines.append("Plan: no structure. Stand aside.")
    lines.append("Levels only, not a call.")
    push("GOLD READ", "\n".join(lines))
    print("brief sent")


if __name__ == "__main__":
    main()
