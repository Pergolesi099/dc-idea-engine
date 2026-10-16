"""Equal-dollar sizing for the DC book (autopilot SCREEN/STAGE).

usage: python autopilot/size.py in.json out.json

in.json:  {"nav_eur": 15000, "eurusd": 1.16, "leverage": 2.0,
           "pairs": [{"long": "AAA", "long_px": 50.1, "short": "BBB", "short_px": 20.3, ...}, ...]}
out.json: same pairs with long_qty / short_qty / long_usd / short_usd, plus "sizing" and "checks".

Rule (Dean, weekly-dc-portfolio v2): per-leg budget = NAV_USD x leverage / (2 x n_pairs);
shares floored against it, so cost never exceeds the budget. Stops are reference only: 25%.
"""
import json
import math
import sys


def size(d):
    pairs = d["pairs"]
    n = len(pairs)
    if n == 0:
        raise SystemExit("no pairs to size")
    nav_usd = d["nav_eur"] * d["eurusd"]
    lev = d["leverage"]
    leg = nav_usd * lev / (2 * n)
    out, dropped = [], []
    for p in pairs:
        q = dict(p)
        q["long_qty"] = math.floor(leg / p["long_px"])
        q["short_qty"] = math.floor(leg / p["short_px"])
        if q["long_qty"] == 0 or q["short_qty"] == 0:
            dropped.append({**p, "reason": "zero shares at leg budget"})
            continue
        q["long_usd"] = round(q["long_qty"] * p["long_px"], 2)
        q["short_usd"] = round(q["short_qty"] * p["short_px"], 2)
        q["long_stop"] = round(p["long_px"] * 0.75, 2)
        q["short_stop"] = round(p["short_px"] * 1.25, 2)
        out.append(q)
    L = sum(p["long_usd"] for p in out)
    S = sum(p["short_usd"] for p in out)
    gross = L + S
    skew = (L - S) / gross if gross else 0.0
    cap = nav_usd * lev / (2 * max(len(out), 1)) * 1.15
    checks = {
        "net_skew_pct": round(skew * 100, 2),
        "skew_ok": abs(skew) <= 0.10,
        "max_leg_usd": round(max([p["long_usd"] for p in out] + [p["short_usd"] for p in out], default=0), 2),
        "leg_cap_ok": all(p["long_usd"] <= cap and p["short_usd"] <= cap for p in out),
        "gross_usd": round(gross, 2),
        "gross_x_nav": round(gross / nav_usd, 3) if nav_usd else None,
    }
    checks["status"] = "OK" if checks["skew_ok"] and checks["leg_cap_ok"] else "CHECK"
    if dropped and out:  # re-size the survivors on the smaller n
        again = size({**d, "pairs": [{k: v for k, v in p.items() if not k.startswith(("long_q", "short_q", "long_u", "short_u", "long_s", "short_s"))} for p in out]})
        again["dropped"] = dropped + again["dropped"]
        return again
    return {**d, "pairs": out, "dropped": dropped,
            "sizing": {"rule": "equal_dollar_per_leg", "nav_usd": round(nav_usd, 2),
                       "leg_budget_usd": round(leg, 2), "n_pairs": len(out)},
            "checks": checks}


if __name__ == "__main__":
    res = size(json.load(open(sys.argv[1])))
    json.dump(res, open(sys.argv[2], "w"), indent=1)
    c = res["checks"]
    print(f"{res['sizing']['n_pairs']} pairs, leg ${res['sizing']['leg_budget_usd']:,.0f}, "
          f"gross {c['gross_x_nav']}x NAV, skew {c['net_skew_pct']}%, {c['status']}, dropped {len(res['dropped'])}")
