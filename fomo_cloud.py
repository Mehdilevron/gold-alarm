#!/usr/bin/env python3
"""Fomo coin watcher: screens fresh tokens, filters likely scams, pushes to phone via ntfy."""
import json, os, subprocess, time, urllib.request, urllib.error

NTFY_URL = "https://ntfy.sh/fomo-coin-alerts-m4x9q2"
STATE = "fomo_seen.json"
POLL_SECONDS = 180

MIN_MCAP = 100_000
MAX_MCAP = 50_000_000
MIN_LIQ = 25_000
MIN_VOL24 = 50_000
MIN_AGE_MIN = 30
MAX_AGE_HOURS = 72
MIN_TXNS_24 = 150


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "fomo-watch/1.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode())


def push(title, body, tags="chart_with_upwards_trend", img=None, click=None):
    data = body.encode("utf-8")
    safe = (title.replace("—", "-").replace("–", "-")
            .encode("ascii", "replace").decode("ascii"))
    hdrs = {"Title": safe, "Tags": tags, "Priority": "high"}
    if img:
        hdrs["Icon"] = img
        hdrs["Attach"] = img
    if click:
        hdrs["Click"] = click
    req = urllib.request.Request(NTFY_URL, data=data, headers=hdrs)
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as e:
        print("push failed:", e, flush=True)
    notify_mac(safe, body)


def notify_mac(title, body):
    """Native macOS banner in Notification Centre, so it lands on the Mac too."""
    def esc(s):
        return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " · ")
    script = (f'display notification "{esc(body)[:230]}" '
              f'with title "FOMO" subtitle "{esc(title)[:90]}" sound name "Submarine"')
    if not os.path.exists("/usr/bin/osascript"):
        return
    try:
        subprocess.run(["/usr/bin/osascript", "-e", script], timeout=10,
                       capture_output=True)
    except Exception as e:
        print("mac notify failed:", e, flush=True)


def load_seen():
    try:
        with open(STATE) as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_seen(seen):
    with open(STATE, "w") as f:
        json.dump(sorted(seen)[-4000:], f)


def candidates():
    out = []
    for ep in ("https://api.dexscreener.com/token-boosts/latest/v1",
               "https://api.dexscreener.com/token-boosts/top/v1"):
        try:
            for item in get(ep):
                out.append((item.get("chainId"), item.get("tokenAddress")))
        except Exception as e:
            print("feed error:", e, flush=True)
    return [x for x in dict.fromkeys(out) if x[0] and x[1]]


def score(p):
    """Return (ok, reasons, metrics) after scam screening."""
    liq = (p.get("liquidity") or {}).get("usd") or 0
    mcap = p.get("marketCap") or p.get("fdv") or 0
    vol = (p.get("volume") or {}).get("h24") or 0
    ch = p.get("priceChange") or {}
    txns = p.get("txns") or {}
    t24 = (txns.get("h24") or {})
    buys, sells = t24.get("buys") or 0, t24.get("sells") or 0
    created = p.get("pairCreatedAt")
    age_min = (time.time() * 1000 - created) / 60000 if created else None

    bad = []
    if mcap < MIN_MCAP: bad.append("mcap too small")
    if mcap > MAX_MCAP: bad.append("mcap too large")
    if liq < MIN_LIQ: bad.append("thin liquidity")
    if vol < MIN_VOL24: bad.append("low volume")
    if mcap and liq / mcap < 0.02: bad.append("liquidity/mcap ratio")
    if buys + sells < MIN_TXNS_24: bad.append("few trades")
    if sells and buys / max(sells, 1) > 20: bad.append("no sells - honeypot risk")
    if age_min is not None and age_min < MIN_AGE_MIN: bad.append("too new")
    if age_min is not None and age_min > MAX_AGE_HOURS * 60: bad.append("too old")

    pop = 0
    if (ch.get("h1") or 0) > 8: pop += 1
    if (ch.get("h6") or 0) > 20: pop += 1
    if vol > max(mcap * 0.5, 250_000): pop += 1
    if liq > 80_000: pop += 1
    if buys > sells * 1.25: pop += 1

    metrics = dict(mcap=mcap, liq=liq, vol=vol, h1=ch.get("h1"), h6=ch.get("h6"),
                   buys=buys, sells=sells, age_h=round(age_min / 60, 1) if age_min else None,
                   pop=pop)
    return (not bad and pop >= 3), bad, metrics


def fmt(n):
    if not n: return "0"
    for u, d in (("M", 1e6), ("K", 1e3)):
        if abs(n) >= d: return f"{n/d:.1f}{u}"
    return f"{n:.0f}"


def main():
    seen = load_seen()
    while True:
        try:
            for chain, addr in candidates():
                key = f"{chain}:{addr}"
                if key in seen:
                    continue
                try:
                    data = get(f"https://api.dexscreener.com/token-pairs/v1/{chain}/{addr}")
                except Exception:
                    continue
                pairs = data if isinstance(data, list) else data.get("pairs") or []
                if not pairs:
                    continue
                p = max(pairs, key=lambda x: ((x.get("liquidity") or {}).get("usd") or 0))
                ok, bad, m = score(p)
                seen.add(key)
                sym = (p.get("baseToken") or {}).get("symbol") or addr[:6]
                if ok:
                    px = float(p.get("priceUsd") or 0)
                    zone_lo, zone_hi = px * 0.92, px * 1.02
                    invalid = px * 0.80
                    t1, t2 = px * 1.3, px * 1.8
                    def pr(v):
                        return f"{v:.8f}".rstrip("0") if v < 0.01 else f"{v:,.4f}"
                    h1, h6 = m['h1'] or 0, m['h6'] or 0
                    trend = ("🟢 impulse up" if h1 > 10 and h6 > 20 else
                             "🟡 cooling after run" if h6 > 20 and h1 < 0 else
                             "🟢 steady bid" if h1 > 0 else "🔴 fading")
                    press = ("🟢 buyers in control" if m['buys'] > m['sells'] * 1.3 else
                             "🔴 sellers in control" if m['sells'] > m['buys'] * 1.3 else
                             "🟡 balanced")
                    depth = f"{(m['liq']/m['mcap']*100):.1f}% of mcap in liquidity"
                    img = ((p.get("info") or {}).get("imageUrl") or "")
                    body = (f"NOW   {pr(px)}   ({m['h1']}% 1h)\n"
                            f"\n"
                            f"BUY   {pr(zone_lo)}  to  {pr(zone_hi)}\n"
                            f"CUT   {pr(invalid)}\n"
                            f"\n"
                            f"SELL  1/3 at {pr(t1)}\n"
                            f"SELL  1/3 at {pr(t2)}\n"
                            f"RUN   rest to {pr(px*2.5)}\n"
                            f"\n"
                            f"{trend}   {press}\n"
                            f"Mcap {fmt(m['mcap'])}   Liq {fmt(m['liq'])}   Vol {fmt(m['vol'])}\n"
                            f"{p.get('url','')}")
                    push(f"{sym}  {fmt(m['mcap'])} mcap", body, "fire", img, p.get("url"))
                    print("ALERT", key, m, flush=True)
                else:
                    print("skip", key, bad, flush=True)
            save_seen(seen)
        except Exception as e:
            print("loop error:", e, flush=True)
        if os.environ.get("ONESHOT"):
            return
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
