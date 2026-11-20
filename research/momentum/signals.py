"""Which momentum signal predicts the NEXT game's residual (vs the static walk-forward rating)?

For each team, keep its residual history vs the pre-game static prediction (the analog of 'vs our number').
Signals computed before each game, for home and away; the test regresses next residual (home view) on
(signal_home - signal_away). Slope ~0 = noise, slope > 0 = it carries forward.
"""
import os
import sys, math, collections
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import load, run

QSTAR = 0.03

def ols(x, y):
    x = np.asarray(x); y = np.asarray(y)
    b = (x @ y) / (x @ x)                       # no intercept: signals are differences, symmetric
    res = y - b * x
    se = math.sqrt((res @ res) / (len(x) - 1) / (x @ x))
    r2 = 1 - (res @ res) / (y @ y)
    return b, se, r2

def analyze(season):
    games, teams = load(season)
    st, *_ = run(games, teams, 0.0)
    kf, *_ = run(games, teams, QSTAR)
    kfm = {o["id"]: o for o in kf}
    hist = collections.defaultdict(list)       # team -> [(date, resid vs static)]
    kmom = {}                                    # team -> latest (theta_kf - theta_static) before the game
    rows = []
    for o in st:
        k = kfm[o["id"]]
        e = o["y"] - o["mu"]                    # residual vs static pregame number, home view
        ek = o["y"] - k["mu"]                   # residual vs Kalman pregame number
        sig = {}
        for side, t in (("h", o["home"]), ("a", o["away"])):
            hs = hist[t]
            last5 = [r for _, r in hs[-5:]]
            sig[side] = {
                "n": len(hs),
                "last5": np.mean(last5) if last5 else 0.0,
                "flag": (1 if sum(r > 0 for r in last5) >= 4 else -1 if sum(r < 0 for r in last5) >= 4 else 0) if len(last5) == 5 else 0,
                "last3": np.mean([r for _, r in hs[-3:]]) if hs else 0.0,
                "ewma": (sum(r * 0.75 ** (len(hs) - 1 - m) for m, (_, r) in enumerate(hs)) /
                         sum(0.75 ** (len(hs) - 1 - m) for m in range(len(hs)))) if hs else 0.0,
                "kal": (k["th_h"] - o["th_h"]) if side == "h" else (k["th_a"] - o["th_a"]),
            }
        rows.append({"date": o["date"], "e": e, "ek": ek, "sig": sig})
        hist[o["home"]].append((o["date"], e))
        hist[o["away"]].append((o["date"], -e))
    ev = [r for r in rows if (r["date"][5:] >= "12-01" or r["date"][5:] < "07-01") and r["sig"]["h"]["n"] >= 5 and r["sig"]["a"]["n"] >= 5]
    print(f"\n{season}: {len(ev)} games (Dec 1 on, both teams 5+ prior games)")
    y = [r["e"] for r in ev]
    for name in ["flag", "last3", "last5", "ewma", "kal"]:
        x = [r["sig"]["h"][name] - r["sig"]["a"][name] for r in ev]
        b, se, r2 = ols(x, y)
        print(f"  {name:6s} slope {b:+.3f} (se {se:.3f}, t {b/se:+.1f})  R2 {r2*100:.2f}%  sd(signal) {np.std(x):.2f}")
    # board-style flag: when only one side is flagged hot/cold, what's the next residual from that team's view?
    for lab, f in (("4of5 HOT", 1), ("4of5 COLD", -1)):
        vals = []
        for r in ev:
            for side, s in (("h", 1), ("a", -1)):
                if r["sig"][side]["flag"] == f:
                    vals.append(s * r["e"])
        print(f"  {lab}: n={len(vals)}, mean next residual {np.mean(vals):+.2f} (se {np.std(vals)/math.sqrt(len(vals)):.2f}), beat number next game {np.mean([v>0 for v in vals])*100:.1f}%")
    # after the Kalman, is there any hot-hand left? residual vs Kalman on last5 residual-vs-static
    x = [r["sig"]["h"]["last5"] - r["sig"]["a"]["last5"] for r in ev]
    b, se, _ = ols(x, [r["ek"] for r in ev])
    print(f"  leftover: next resid vs KALMAN on last5 -> slope {b:+.3f} (se {se:.3f})")
    return ev

if __name__ == "__main__":
    for s in ["2024-25", "2025-26"]:
        analyze(s)
