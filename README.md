# gold-alarmname: Gold Zone Alarm
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
          def push(t, m):
              req = urllib.request.Request(NTFY, data=m.encode(),
                  headers={"Title": t, "Priority": "high", "Tags": "rotating_light"}, method="POST")
              urllib.request.urlopen(req, timeout=15); print("PUSHED:", t)
          if os.environ.get("EVENT") == "workflow_dispatch":
              push("GitHub gold alarm OK", f"Test worked. Gold {price:.1f}. Cloud alarm live 24/7 (Mac can be off).")
          else:
              for d, lo, hi, label in [("LONG",3959,3967,"3959-3967 demand"),("SHORT",4028,4042,"4028-4042 supply"),("SHORT",4048,4052,"4050 supply")]:
                  if lo-3 <= price <= hi+3:
                      push("GOLD zone hit", f"Gold {price:.1f} hit the {d} zone {label} - open Claude to confirm."); break
              else:
                  print("no zone near - no alert")
          PY
