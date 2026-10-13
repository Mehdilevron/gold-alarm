#!/usr/bin/env python3
"""
Autonomous gold structure engine.

Runs in GitHub Actions every 5 minutes with the user's Mac off.
Derives ALL structure itself from live bars -- nothing is hand-drawn:

  * multi-timeframe market structure (4H / 1H / 30m / 5m) via fractal pivots
  * premium / discount positioning inside the active dealing range
  * ICT order blocks (last opposing candle before a displacement that breaks structure)
  * fair value gaps (3-candle imbalance), unmitigated only
  * RTM supply / demand flips (a zone that failed and is now the opposite)
  * liquidity: equal highs / lows, session highs / lows, prior-day high / low
  * CRT: prior higher-timeframe candle range swept then reclaimed (Turtle Soup / PO3)
  * confirmation: sweep -> close back out of the zone -> 5m break of structure

Each candidate is SCORED. Only >= 70 is pushed to the phone.
Standing order, 13 Oct 2026: no noise, no news pushes, no zone-touch pings.
"""

import json
import os
import time
import datetime
import hashlib
import urllib.request

NTFY = "https://ntfy.sh/claude-mt5-alerts-7f3k2"
SPOT = "https://api.gold-api.com/price/XAU"
YF = ("https://query1.finance.yahoo.com/v8/finance/chart/GC=F"
      "?interval={iv}&range={rg}")
CAL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
STATE = "state.json"

THRESHOLD = 70          # the user's 70% rule
LOCK_PRE, LOCK_POST = 30, 20
UA = {"User-Agent": "Mozilla/5.0"}


# ----------------------------------------------------------------- data

def get_json(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def spot():
    return float(get_json(SPOT, 15)["price"])


def bars(interval, rng):
    q = get_json(YF.format(iv=interval, rg=rng))["chart"]["result"][0]
    ts, qq = q["timestamp"], q["indicators"]["quote"][0]
    out = []
    for t, o, h, l, c, v in zip(ts, qq["open"], qq["high"], qq["low"],
                                qq["close"], qq["volume"]):
        if None in (o, h, l, c):
            continue
        out.append({"t": t, "o": o, "h": h, "l": l, "c": c, "v": v or 0})
    return out


def resample(b5, minutes):
    """Aggregate 5m bars into higher timeframe buckets."""
    step = minutes * 60
    buckets = {}
    for b in b5:
        k = b["t"] - (b["t"] % step)
        g = buckets.get(k)
        if g is None:
            buckets[k] = {"t": k, "o": b["o"], "h": b["h"], "l": b["l"],
                          "c": b["c"], "v": b["v"]}
        else:
            g["h"] = max(g["h"], b["h"])
            g["l"] = min(g["l"], b["l"])
            g["c"] = b["c"]
            g["v"] += b["v"]
    return [buckets[k] for k in sorted(buckets)]


def shift(bs, off):
    """Yahoo quotes COMEX futures; move them onto the spot price."""
    return [{"t": b["t"], "v": b["v"],
             **{k: b[k] + off for k in ("o", "h", "l", "c")}} for b in bs]


# ------------------------------------------------------------ structure

def pivots(bs, n=2):
    """Fractal swing highs and lows."""
    hi, lo = [], []
    for i in range(n, len(bs) - n):
        w = bs[i - n:i + n + 1]
        if bs[i]["h"] == max(x["h"] for x in w):
            hi.append((i, bs[i]["h"], bs[i]["t"]))
        if bs[i]["l"] == min(x["l"] for x in w):
            lo.append((i, bs[i]["l"], bs[i]["t"]))
    return hi, lo


def bias(bs, n=2):
    """Read structure from the last swing sequence: up, down or range."""
    hi, lo = pivots(bs, n)
    if len(hi) < 2 or len(lo) < 2:
        return "range", None, None
    hh = hi[-1][1] > hi[-2][1]
    hl = lo[-1][1] > lo[-2][1]
    lh = hi[-1][1] < hi[-2][1]
    ll = lo[-1][1] < lo[-2][1]
    if hh and hl:
        return "up", hi[-1][1], lo[-1][1]
    if lh and ll:
        return "down", hi[-1][1], lo[-1][1]
    return "range", hi[-1][1], lo[-1][1]


def dealing_range(bs, look=120):
    w = bs[-look:]
    return max(x["h"] for x in w), min(x["l"] for x in w)


def eq_levels(bs, look=80, tol=0.0006):
    """Equal highs / lows -- resting liquidity."""
    w = bs[-look:]
    highs = sorted(x["h"] for x in w)
    lows = sorted(x["l"] for x in w)
    out = {"eqh": [], "eql": []}
    for key, arr in (("eqh", highs), ("eql", lows)):
        i = 0
        while i < len(arr) - 1:
            grp = [arr[i]]
            j = i + 1
            while j < len(arr) and abs(arr[j] - grp[0]) / grp[0] <= tol:
                grp.append(arr[j]); j += 1
            if len(grp) >= 3:
                out[key].append(sum(grp) / len(grp))
            i = j if j > i + 1 else i + 1
    return out


def order_blocks(bs, n=2, limit=6):
    """
    ICT order block: the last opposing candle before a displacement leg
    that breaks the most recent opposing pivot. Returns unmitigated ones.
    """
    hi, lo = pivots(bs, n)
    obs = []
    for i in range(n + 2, len(bs) - 1):
        # bullish: close above the prior swing high
        prior_h = [p[1] for p in hi if p[0] < i - 1]
        prior_l = [p[1] for p in lo if p[0] < i - 1]
        if prior_h and bs[i]["c"] > prior_h[-1] and bs[i]["c"] > bs[i]["o"]:
            for k in range(i - 1, max(i - 6, 0), -1):
                if bs[k]["c"] < bs[k]["o"]:
                    obs.append({"dir": "long", "lo": bs[k]["l"], "hi": bs[k]["h"],
                                "i": k, "kind": "OB"})
                    break
        if prior_l and bs[i]["c"] < prior_l[-1] and bs[i]["c"] < bs[i]["o"]:
            for k in range(i - 1, max(i - 6, 0), -1):
                if bs[k]["c"] > bs[k]["o"]:
                    obs.append({"dir": "short", "lo": bs[k]["l"], "hi": bs[k]["h"],
                                "i": k, "kind": "OB"})
                    break
    # drop anything price has already traded fully through after formation
    live = []
    for ob in obs:
        after = bs[ob["i"] + 1:]
        if not after:
            continue
        if ob["dir"] == "long" and min(x["l"] for x in after) < ob["lo"]:
            continue
        if ob["dir"] == "short" and max(x["h"] for x in after) > ob["hi"]:
            continue
        live.append(ob)
    # dedupe by zone, keep the freshest
    seen, out = [], []
    for ob in reversed(live):
        if any(abs(ob["lo"] - s["lo"]) < 1.5 and ob["dir"] == s["dir"] for s in seen):
            continue
        seen.append(ob); out.append(ob)
        if len(out) >= limit:
            break
    return out


def fvgs(bs, limit=6):
    """Unmitigated 3-candle fair value gaps."""
    out = []
    for i in range(2, len(bs)):
        a, c = bs[i - 2], bs[i]
        if c["l"] > a["h"]:
            out.append({"dir": "long", "lo": a["h"], "hi": c["l"], "i": i, "kind": "FVG"})
        if c["h"] < a["l"]:
            out.append({"dir": "short", "lo": c["h"], "hi": a["l"], "i": i, "kind": "FVG"})
    live = []
    for g in out:
        after = bs[g["i"] + 1:]
        if not after:
            continue
        mid = (g["lo"] + g["hi"]) / 2
        if g["dir"] == "long" and min(x["l"] for x in after) <= mid:
            continue
        if g["dir"] == "short" and max(x["h"] for x in after) >= mid:
            continue
        live.append(g)
    return live[-limit:]


def flips(bs, n=2, limit=4):
    """
    RTM flip: a zone that held as demand, failed, and now sits above price
    as supply (or the mirror). Taken from broken pivots.
    """
    hi, lo = pivots(bs, n)
    last = bs[-1]["c"]
    out = []
    for i, p, t in lo[-6:]:
        after = bs[i + 1:]
        if after and min(x["c"] for x in after) < p and last < p:
            out.append({"dir": "short", "lo": p, "hi": p * 1.0012, "i": i, "kind": "FLIP"})
    for i, p, t in hi[-6:]:
        after = bs[i + 1:]
        if after and max(x["c"] for x in after) > p and last > p:
            out.append({"dir": "long", "lo": p * 0.9988, "hi": p, "i": i, "kind": "FLIP"})
    return out[-limit:]


def crt(bs_htf, bs_ltf):
    """
    Candle Range Theory: the previous completed higher-timeframe candle is the
    range. Price sweeps one side of it and closes back inside -> the other side
    is the target. Returns a direction when that has actually happened.
    """
    if len(bs_htf) < 3:
        return None
    key = bs_htf[-2]
    cur = bs_htf[-1]
    if cur["h"] > key["h"] and cur["c"] < key["h"]:
        return {"dir": "short", "range": (key["l"], key["h"]), "swept": key["h"]}
    if cur["l"] < key["l"] and cur["c"] > key["l"]:
        return {"dir": "long", "range": (key["l"], key["h"]), "swept": key["l"]}
    return None


def bos5(b5, direction, look=12):
    """5m break of structure in the given direction."""
    if len(b5) < look + 1:
        return False, None
    last = b5[-1]
    w = b5[-look:-1]
    if direction == "long":
        sw = max(x["h"] for x in w)
        return last["c"] > sw, sw
    sw = min(x["l"] for x in w)
    return last["c"] < sw, sw


# ----------------------------------------------------------------- news

KEYWORDS = ("CPI", "PPI", "FOMC", "Federal Funds", "Non-Farm", "Nonfarm",
            "Unemployment", "Powell", "PCE", "GDP", "Retail Sales", "Jobless",
            "ADP", "ISM", "Payroll", "Rate", "Inflation", "Treasury",
            "Consumer Confidence", "Durable Goods", "Beige Book")


def news_lock():
    try:
        raw = get_json(CAL)
    except Exception as ex:
        print("calendar unreachable:", ex)
        return None
    now = time.time()
    for e in raw:
        t, c, i = e.get("title", ""), e.get("country", ""), e.get("impact", "")
        if not (i == "High" or (c == "USD" and i == "Medium"
                               and any(k.lower() in t.lower() for k in KEYWORDS))):
            continue
        try:
            ts = datetime.datetime.fromisoformat(e["date"].strip()).timestamp()
        except Exception:
            continue
        m = (ts - now) / 60.0
        if -LOCK_POST <= m <= LOCK_PRE:
            return t
    return None


# ------------------------------------------------------------- scoring

def score(zone, ctx):
    """
    Confluence score out of 100. The user's rule: only >= 70 is worth a phone.
    """
    pts, why = 0, []
    d = zone["dir"]

    if ctx["bias_4h"] == d:
        pts += 20; why.append("4H structure agrees")
    elif ctx["bias_4h"] != "range":
        pts -= 15; why.append("against 4H structure")

    if ctx["bias_1h"] == d:
        pts += 18; why.append("1H structure agrees")
    elif ctx["bias_1h"] != "range":
        pts -= 10; why.append("against 1H structure")

    kind = zone["kind"]
    pts += {"OB": 16, "FLIP": 16, "FVG": 11}.get(kind, 8)
    why.append({"OB": "ICT order block", "FLIP": "RTM supply/demand flip",
                "FVG": "fair value gap"}.get(kind, kind))

    if ctx["crt"] and ctx["crt"]["dir"] == d:
        pts += 20; why.append("CRT sweep of the prior candle range, closed back inside")

    # premium / discount
    hi, lo = ctx["range"]
    mid = (hi + lo) / 2
    z = (zone["lo"] + zone["hi"]) / 2
    if d == "long" and z < mid:
        pts += 10; why.append("discount half of the dealing range")
    elif d == "short" and z > mid:
        pts += 10; why.append("premium half of the dealing range")
    else:
        pts -= 8; why.append("wrong half of the range")

    # resting liquidity taken just beyond the zone
    liq = ctx["liq"]
    if d == "long" and any(zone["lo"] - 6 <= x <= zone["hi"] for x in liq["eql"]):
        pts += 14; why.append("equal lows swept beneath it")
    if d == "short" and any(zone["lo"] <= x <= zone["hi"] + 6 for x in liq["eqh"]):
        pts += 14; why.append("equal highs swept above it")

    if zone.get("swept"):
        pts += 12; why.append("zone already swept")
    if zone.get("reclaimed"):
        pts += 10; why.append("5m closed back out of it")
    if zone.get("bos"):
        pts += 20; why.append("5m break of structure confirmed")

    return max(0, min(100, pts)), why


# -------------------------------------------------------------- notify

def push(title, msg):
    req = urllib.request.Request(
        NTFY, data=msg.encode(),
        headers={"Title": title, "Priority": "high", "Tags": "rotating_light"},
        method="POST")
    urllib.request.urlopen(req, timeout=15)
    print("PUSHED:", title)


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:
        return {"sent": []}


def save_state(st):
    with open(STATE, "w") as f:
        json.dump(st, f, indent=1)


# ---------------------------------------------------------------- main

def main():
    price = spot()
    raw5 = bars("5m", "5d")
    if len(raw5) < 60:
        print("not enough 5m data - silent")
        return
    off = price - raw5[-1]["c"]
    b5 = shift(raw5, off)
    b30 = resample(b5, 30)
    b1h = resample(b5, 60)
    b4h = resample(b5, 240)
    print(f"price {price:.1f}  bars 5m={len(b5)} 30m={len(b30)} 1h={len(b1h)} 4h={len(b4h)}"
          f"  futures offset {off:+.1f}")

    lock = news_lock()
    if lock:
        print("news lock:", lock, "- silent")
        return

    bias_4h, _, _ = bias(b4h)
    bias_1h, _, _ = bias(b1h)
    bias_30, _, _ = bias(b30)
    hi, lo = dealing_range(b30)
    ctx = {"bias_4h": bias_4h, "bias_1h": bias_1h, "bias_30": bias_30,
           "range": (hi, lo), "liq": eq_levels(b30),
           "crt": crt(b1h, b5)}
    print(f"bias 4H={bias_4h} 1H={bias_1h} 30m={bias_30}  range {lo:.1f}-{hi:.1f}"
          f"  CRT={ctx['crt']['dir'] if ctx['crt'] else 'none'}")

    # every structure, every method, pooled
    zones = []
    for src, tag in ((b1h, "1H"), (b30, "30m")):
        for z in order_blocks(src) + fvgs(src) + flips(src):
            z = dict(z); z["tf"] = tag; zones.append(z)
    print(f"candidates: {len(zones)}")

    # confirmation state from the 5m
    look = b5[-10:]
    best = None
    for z in zones:
        if z["hi"] < z["lo"]:
            z["lo"], z["hi"] = z["hi"], z["lo"]
        width = z["hi"] - z["lo"]
        if width <= 0 or width > price * 0.006:
            continue
        # must be reachable, not 1% away
        if not (price * 0.985 <= (z["lo"] + z["hi"]) / 2 <= price * 1.015):
            continue
        z["swept"] = any(b["l"] <= z["hi"] and b["h"] >= z["lo"] for b in look[:-1])
        last = b5[-1]
        z["reclaimed"] = (last["c"] > z["hi"]) if z["dir"] == "long" else (last["c"] < z["lo"])
        ok, sw = bos5(b5, z["dir"])
        z["bos"] = bool(ok and z["swept"] and z["reclaimed"])
        z["swing"] = sw
        s, why = score(z, ctx)
        z["score"], z["why"] = s, why
        if best is None or s > best["score"]:
            best = z

    if not best:
        print("no reachable structure - nothing sent")
        return

    print(f"best: {best['dir']} {best['tf']} {best['kind']} "
          f"{best['lo']:.1f}-{best['hi']:.1f} score {best['score']} "
          f"swept={best['swept']} reclaimed={best['reclaimed']} bos={best['bos']}")

    if best["score"] < THRESHOLD or not best["bos"]:
        print(f"below the {THRESHOLD}% bar or unconfirmed - nothing sent")
        return

    entry = (best["lo"] + best["hi"]) / 2
    pad = max(2.5, (best["hi"] - best["lo"]) * 0.8)
    if best["dir"] == "long":
        sl = best["lo"] - pad
        t1, t2, t3 = entry + (entry - sl), entry + 2 * (entry - sl), hi
    else:
        sl = best["hi"] + pad
        t1, t2, t3 = entry - (sl - entry), entry - 2 * (sl - entry), lo
    rr = abs(t2 - entry) / abs(entry - sl)

    fp = hashlib.sha1(f"{best['dir']}{best['lo']:.0f}{best['hi']:.0f}"
                      f"{best['kind']}".encode()).hexdigest()[:12]
    st = load_state()
    if fp in st.get("sent", []):
        print("already alerted on this zone - silent")
        return

    push(f"[{best['score']}%] GOLD {best['dir'].upper()} {best['tf']} {best['kind']}",
         f"Zone {best['lo']:.1f}-{best['hi']:.1f}\n"
         f"ENTRY {entry:.1f}\nSL {sl:.1f}\n"
         f"TP1 {t1:.1f}\nTP2 {t2:.1f}\nTP3 {t3:.1f}\n"
         f"R:R to TP2 = 1:{rr:.1f}\n\n"
         f"Confluence {best['score']}%:\n- " + "\n- ".join(best["why"]) +
         f"\n\n5m BOS through {best['swing']:.1f}. Price now {price:.1f}.\n"
         f"Bias 4H {ctx['bias_4h']}, 1H {ctx['bias_1h']}.")

    st.setdefault("sent", []).append(fp)
    st["sent"] = st["sent"][-40:]
    save_state(st)


if __name__ == "__main__":
    main()
