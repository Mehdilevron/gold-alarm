#!/usr/bin/env python3
"""Gold session engine — Asian / London / New York.

Two modes:
  open   -> session-open push: which session just opened, the levels that
            matter going in (Asian H/L, London H/L, prev day H/L, prev week
            H/L), where price sits inside them, and the plan.
  sweep  -> run every 5 minutes: push when price sweeps a session level and
            closes back inside it (the liquidity-grab trigger).

Pure price action. No API key. Pushes to ntfy topic gold-alarm.
"""
import json, os, sys, datetime as dt
import pandas as pd, yfinance as yf, urllib.request

TOPIC = "https://ntfy.sh/gold-alarm"
STATE = "session_state.json"

# UTC hour windows.  Asian wraps midnight.
ASIA = (23, 8)     # Tokyo/Sydney
LONDON = (7, 16)
NY = (12.5, 21)


def push(title, body, priority="high", tags="chart_with_upwards_trend"):
    req = urllib.request.Request(TOPIC, data=body.encode(),
        headers={"Title": title.encode("ascii","replace").decode(), "Priority": priority, "Tags": tags})
    urllib.request.urlopen(req, timeout=20)


def fetch(interval, period):
    d = yf.download("GC=F", period=period, interval=interval,
                    auto_adjust=False, progress=False)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    d = d.dropna()
    d.index = pd.to_datetime(d.index, utc=True)
    return d


def window(d, start, end, ref=None):
    """High/low of the most recent completed-or-running session window."""
    now = ref or d.index[-1]
    hrs = d.index.hour + d.index.minute / 60.0
    if start > end:  # wraps midnight
        mask = (hrs >= start) | (hrs < end)
    else:
        mask = (hrs >= start) & (hrs < end)
    sel = d[mask]
    if sel.empty:
        return None, None
    # keep only the latest contiguous block
    cutoff = now - dt.timedelta(hours=26)
    sel = sel[sel.index >= cutoff]
    if sel.empty:
        return None, None
    gaps = sel.index.to_series().diff() > pd.Timedelta(hours=3)
    block = sel[gaps[::-1].cumsum()[::-1] == 0] if gaps.any() else sel
    return float(block["High"].max()), float(block["Low"].min())


def levels():
    m15 = fetch("15m", "5d")
    day = fetch("1d", "30d")
    wk = fetch("1wk", "90d")
    out = {}
    ah, al = window(m15, *ASIA)
    lh, ll = window(m15, *LONDON)
    nh, nl = window(m15, *NY)
    out["Asian"] = (ah, al)
    out["London"] = (lh, ll)
    out["New York"] = (nh, nl)
    out["Prev day"] = (float(day["High"].values[-2]), float(day["Low"].values[-2]))
    out["Prev week"] = (float(wk["High"].values[-2]), float(wk["Low"].values[-2]))
    price = float(m15["Close"].values[-1])
    return price, out


def in_session(hrs, start, end):
    return (hrs >= start or hrs < end) if start > end else (start <= hrs < end)


def live_session(d):
    """Which session is open right now, and its high/low SO FAR."""
    now = dt.datetime.now(dt.timezone.utc)
    hrs = now.hour + now.minute / 60.0
    live = []
    for name, (s, e) in (("Asian", ASIA), ("London", LONDON), ("New York", NY)):
        if not in_session(hrs, s, e):
            continue
        start = now.replace(minute=0, second=0, microsecond=0,
                            hour=int(s)) + dt.timedelta(minutes=(s % 1) * 60)
        if s > e and hrs < e:          # wrapped past midnight
            start -= dt.timedelta(days=1)
        live.append((start, name))
    # the session that opened most recently is the one in control
    for start, name in sorted(live, reverse=True):
        sel = d[(d.index >= start) & (d.index <= now)]
        if sel.empty:
            continue
        return name, float(sel["High"].max()), float(sel["Low"].min()), start
    return None, None, None, None


def do_live(d, st):
    """Push when the OPEN session prints a new high or low."""
    name, hi, lo, start = live_session(d)
    if not name:
        return
    key = f"live:{name}:{start:%Y-%m-%d}"
    prev = st.get(key) or {}
    ph, pl = prev.get("h"), prev.get("l")
    if ph is None or pl is None:
        st[key] = {"h": hi, "l": lo}
        return
    if hi > ph + 3:
        push(f"NEW {name.upper()} HIGH",
             f"{name} session high is now {hi:.2f} (was {ph:.2f}).\n"
             f"Session low {lo:.2f} | range {hi-lo:.1f}\n"
             f"Liquidity sits above. Sweep then close back inside is the trigger.",
             tags="arrow_up")
        ph = hi
    if lo < pl - 3:
        push(f"NEW {name.upper()} LOW",
             f"{name} session low is now {lo:.2f} (was {pl:.2f}).\n"
             f"Session high {hi:.2f} | range {hi-lo:.1f}\n"
             f"Liquidity sits below. Sweep then close back inside is the trigger.",
             tags="arrow_down")
        pl = lo
    st[key] = {"h": ph, "l": pl}


def fmt(price, lv):
    lines = [f"Price {price:.2f}", ""]
    for k, (h, l) in lv.items():
        if h is None:
            continue
        lines.append(f"{k}: H {h:.2f} / L {l:.2f}  (range {h-l:.1f})")
    ah, al = lv["Asian"]
    if ah:
        pos = "above" if price > ah else "below" if price < al else "inside"
        lines += ["", f"Gold is {pos} the Asian range."]
    return "\n".join(lines)


def do_open(which):
    price, lv = levels()
    body = fmt(price, lv)
    plan = {
        "Asian": "Asian is the range builder. Mark its high and low; do not chase inside it.",
        "London": "London hunts the Asian range. Wait for a sweep of Asian high or low, a close back inside, then take the 30m break of structure back the other way.",
        "New York": "New York either continues London or reverses it. Watch the London high and low for the sweep; only trade the return to the zone after a change of character.",
    }[which]
    push(f"{which.upper()} OPEN - GOLD", body + "\n\n" + plan,
         tags="bell" if which != "Asian" else "night_with_stars")
    print(body)


def do_sweep():
    price, lv = levels()
    m5 = fetch("5m", "2d")
    if len(m5) < 3:
        print("no data"); return
    bar = m5.iloc[-1]
    hi, lo, cl = float(bar["High"]), float(bar["Low"]), float(bar["Close"])
    st = json.load(open(STATE)) if os.path.exists(STATE) else {}
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    if st.get("day") != stamp:
        st = {"day": stamp}
    for name, (h, l) in lv.items():
        if h is None:
            continue
        kh, kl = f"{name}-H", f"{name}-L"
        if hi > h and cl < h and st.get(kh) != 1:
            push(f"SWEEP — {name} HIGH",
                 f"{name} high {h:.2f} swept, closed back below at {cl:.2f}.\n"
                 f"Sell side: wait for the 30m change of character, then the "
                 f"return to supply. Stop above {hi:.2f}.")
            st[kh] = 1
        if lo < l and cl > l and st.get(kl) != 1:
            push(f"SWEEP — {name} LOW",
                 f"{name} low {l:.2f} swept, closed back above at {cl:.2f}.\n"
                 f"Buy side: wait for the 30m change of character, then the "
                 f"return to demand. Stop below {lo:.2f}.")
            st[kl] = 1
    do_live(m5, st)
    json.dump(st, open(STATE, "w"))
    print(fmt(price, lv))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "sweep"
    if mode in ("Asian", "London", "New York"):
        do_open(mode)
    else:
        do_sweep()
