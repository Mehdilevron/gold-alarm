#!/usr/bin/env python3
"""Full-market scan -> phone.

Two jobs in one script, chosen by argv:

  setups   every 15 minutes. For each pair: a 15-minute sweep of the recent
           range followed by a candle CLOSING BACK INSIDE it, in agreement with
           the H1 bias. Pushed immediately, one push per pair per direction per
           day. This is the AMD rule applied across the whole market — the entry
           is the close back inside, not a later confirmation.

  report   a single consolidated full-market report: every pair's H1 bias, the
           15-minute range it is working in, and the exact level that would
           trigger a long or a short. One message, no noise.

State in market_state.json, committed back by the workflow so nothing repeats.
"""
import sys, json, os, datetime as dt, urllib.request
import yfinance as yf

NTFY = "https://ntfy.sh/claude-mt5-alerts-7f3k2"
STATE = "market_state.json"
SWING = 12

SYMBOLS = {"GC=F": "XAUUSD", "SI=F": "XAGUSD", "BTC-USD": "BTCUSD",
           "EURUSD=X": "EURUSD", "GBPUSD=X": "GBPUSD", "USDJPY=X": "USDJPY",
           "AUDUSD=X": "AUDUSD", "USDCAD=X": "USDCAD", "NZDUSD=X": "NZDUSD",
           "USDCHF=X": "USDCHF", "EURJPY=X": "EURJPY", "GBPJPY=X": "GBPJPY",
           "EURGBP=X": "EURGBP", "AUDJPY=X": "AUDJPY", "CADJPY=X": "CADJPY",
           "CHFJPY=X": "CHFJPY", "EURAUD=X": "EURAUD", "GBPAUD=X": "GBPAUD",
           "EURCAD=X": "EURCAD", "AUDNZD=X": "AUDNZD", "DX-Y.NYB": "DXY"}


def notify(title, msg, prio="high"):
    req = urllib.request.Request(NTFY, data=msg.encode("utf-8"),
        headers={"Title": title, "Priority": prio, "Tags": "chart"}, method="POST")
    try: urllib.request.urlopen(req, timeout=20)
    except Exception as e: print("notify error:", e)


def bars(sym, iv, per):
    return yf.Ticker(sym).history(period=per, interval=iv).dropna()


def atr(df, n=14):
    h, l, c = df["High"], df["Low"], df["Close"].shift(1)
    tr = (h - l).combine((h - c).abs(), max).combine((l - c).abs(), max)
    return float(tr.rolling(n).mean().iloc[-1])


def bias(sym):
    h = bars(sym, "1h", "10d")
    if len(h) < 20: return "flat"
    cl = h["Close"].tail(20)
    m, last = float(cl.mean()), float(cl.iloc[-1])
    return "bullish" if last > m * 1.001 else "bearish" if last < m * 0.999 else "flat"


def load():
    today = str(dt.date.today())
    try:
        s = json.load(open(STATE))
        if s.get("day") == today: return s
    except Exception: pass
    return {"day": today, "fired": []}


def read_pair(sym):
    m = bars(sym, "15m", "5d")
    if len(m) < SWING + 4: return None
    m = m.iloc[:-1]                                  # closed candles only
    cur = m.iloc[-1]
    win = m.iloc[-(SWING + 2):-1]
    return {"m": m, "cur": cur,
            "lo": float(win["Low"].min()), "hi": float(win["High"].max()),
            "c": float(cur["Close"]), "atr": atr(m)}


def fmt(v, c):
    return f"{v:.1f}" if c > 50 else f"{v:.5f}"


def do_setups():
    st = load()
    for sym, pair in SYMBOLS.items():
        try:
            d = read_pair(sym)
            if not d: continue
            b = bias(sym)
            c, hi, lo, a = d["c"], d["hi"], d["lo"], d["atr"]
            cur = d["cur"]
            swept_low = float(cur["Low"]) < lo and c > lo
            swept_high = float(cur["High"]) > hi and c < hi

            if swept_low and b != "bearish":
                key = f"{pair}-long"
                if key in st["fired"]: continue
                stop = float(cur["Low"]) - a * 0.15
                r = c - stop
                notify(f"{pair} LONG - sweep rejected",
                       f"Swept {fmt(lo, c)} and closed back inside.\n\n"
                       f"Entry {fmt(c, c)}\nStop {fmt(stop, c)}\n"
                       f"TP1 {fmt(c + r, c)}  TP2 {fmt(c + 2*r, c)}  "
                       f"TP3 {fmt(c + 3*r, c)}\n"
                       f"H1 bias {b}. Entry is on this close, before the move.")
                st["fired"].append(key)
            elif swept_high and b != "bullish":
                key = f"{pair}-short"
                if key in st["fired"]: continue
                stop = float(cur["High"]) + a * 0.15
                r = stop - c
                notify(f"{pair} SHORT - sweep rejected",
                       f"Swept {fmt(hi, c)} and closed back inside.\n\n"
                       f"Entry {fmt(c, c)}\nStop {fmt(stop, c)}\n"
                       f"TP1 {fmt(c - r, c)}  TP2 {fmt(c - 2*r, c)}  "
                       f"TP3 {fmt(c - 3*r, c)}\n"
                       f"H1 bias {b}. Entry is on this close, before the move.")
                st["fired"].append(key)
            else:
                print(pair, "no setup", b)
        except Exception as e:
            print("error", pair, e)
    json.dump(st, open(STATE, "w"))


def do_report():
    lines = [f"Full market scan  {dt.datetime.now().strftime('%d %b %H:%M')} UTC", ""]
    for sym, pair in SYMBOLS.items():
        try:
            d = read_pair(sym)
            if not d:
                lines.append(f"{pair}  no data"); continue
            b = bias(sym)
            c, hi, lo = d["c"], d["hi"], d["lo"]
            lines.append(f"{pair}  {fmt(c, c)}  H1 {b}")
            lines.append(f"   range {fmt(lo, c)} - {fmt(hi, c)}")
            lines.append(f"   long if it sweeps {fmt(lo, c)} and closes back in; "
                         f"short if it sweeps {fmt(hi, c)} and closes back in")
        except Exception as e:
            lines.append(f"{pair}  error")
            print("error", pair, e)
    lines += ["", "Setups fire as their own push the moment a candle closes "
                  "back inside a swept range."]
    notify("FULL MARKET SCAN", "\n".join(lines), prio="default")
    print("\n".join(lines))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "setups"
    do_report() if mode == "report" else do_setups()
