name: Gold Zone Alarm
on:
  schedule:
    - cron: '*/15 * * * *'
  workflow_dispatch:
jobs:
  check-gold:
    runs-on: ubuntu-latest
    steps:
      - name: Check gold price and alert phone
        env:
          EVENT: ${{ github.event_name }}
        run: |
          python3 - <<'PY'
          import os, json, urllib.request
          NTFY = "https://ntfy.sh/claude-mt5-alerts-7f3k2"
          with urllib.request.urlopen("https://api.gold-api.com/price/XAU", timeout=15) as r:
              price = float(json.load(r)["price"])
          print("gold:", price)
          def push(title, msg, prio="high"):
              req = urllib.request.Request(NTFY, data=msg.encode(),
                  headers={"Title": title, "Priority": prio, "Tags": "rotating_light"}, method="POST")
              urllib.request.urlopen(req, timeout=15); print("PUSHED:", title)
          zones = [
              ("LONG",  3959, 3967, 3962, 3948, 4018, 4036),
              ("SHORT", 4028, 4042, 4036, 4050, 3998, 3962),
              ("SHORT", 4048, 4052, 4050, 4065, 3982, 3920),
          ]
          if os.environ.get("EVENT") == "workflow_dispatch":
              push("GitHub gold alarm OK", f"Test worked. Gold {price:.1f}. Cloud alarm live 24/7 (Mac can be off).")
          else:
              for d, lo, hi, entry, sl, tp1, tp2 in zones:
                  if lo - 3 <= price <= hi + 3:
                      side = "BUY" if d == "LONG" else "SELL"
                      push(f"GOLD {d} {int(lo)}-{int(hi)}",
                           f"Gold {price:.1f} at {d} zone.\n{side}: entry {entry} | SL {sl} | TP1 {tp1} | TP2 {tp2}  (~1:3 R:R)\nWait for rejection/sweep. Levels are guides, not advice."); break
              else:
                  print("no zone within 3 pts - no alert")
          PY
