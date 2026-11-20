"""Empirical-Bayes Momentum Index, walk-forward.

For team i on date d (using only games before d):
  game score GS_k = i's margin + opponent's current rating - HCA*(i home ? 1 : i away ? -1 : 0)
                    (points vs an average D1 team on a neutral floor, opponents re-rated as the season goes)
  raw momentum M = sum_k (w_k - 1/n) * GS_k      w = recency weights, half-life H games, sum to 1
                 = recency-weighted level minus season-average level
  noise SE^2    = sigma^2 * sum_k (w_k - 1/n)^2  (what M would scatter by if the team never changed)
  true-momentum variance tau^2 from the cross-section (method of moments), then
  shrunk MI     = M * tau^2 / (tau^2 + SE^2),  posterior SD = sqrt(tau^2 SE^2 / (tau^2 + SE^2))
  P(hot)        = Phi(MI / posterior SD)
"""
import os, sys, math, collections, datetime as dt, json
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import load

SIG2 = 11.4 ** 2
HCA_VAR = float(os.environ.get("HCA_VAR", "0.05"))   # what model/momentum.py uses
Phi = lambda z: 0.5 * (1 + math.erf(z / math.sqrt(2)))

def static_snapshots(games, teams, R=11.0**2, S0=9.0**2, hca0=3.0, hca_var=HCA_VAR):
    """Static (all games equal) joint Kalman filter; returns {date: (theta copy, hca)} BEFORE that date's games."""
    idx = {t: i for i, t in enumerate(teams)}; n = len(teams)
    th = np.zeros(n + 1); th[n] = hca0
    P = np.eye(n + 1) * S0; P[n, n] = hca_var
    by_date = collections.defaultdict(list)
    for r in games: by_date[r["date"]].append(r)
    snaps = {}
    for d in sorted(by_date):
        snaps[d] = th.copy()
        for r in by_date[d]:
            i, j = idx[r["home"]], idx[r["away"]]
            h = 0.0 if r["neutral"] else 1.0
            Hp = P[:, i] - P[:, j] + h * P[:, n]
            S = Hp[i] - Hp[j] + h * Hp[n] + R
            K = Hp / S
            th += K * (r["hs"] - r["as"] - (th[i] - th[j] + h * th[n]))
            P -= np.outer(K, Hp)
    snaps["end"] = th.copy()
    return snaps, idx

def weights(n, H):
    if H is None:                       # last-5 window, for comparison
        w = np.zeros(n); w[-min(5, n):] = 1.0
    else:
        w = 0.5 ** ((n - 1 - np.arange(n)) / H)
    return w / w.sum()

def raw_momentum(past, th, idx, H):
    """past: list of (opp, site, margin) oldest first. Returns (M, SE2, n)."""
    n = len(past)
    hca = th[-1]
    gs = np.array([m + th[idx[o]] - s * hca for o, s, m in past])
    c = weights(n, H) - 1.0 / n
    return float(c @ gs), float(SIG2 * (c @ c)), n

def walk(season, H, tau2_fixed=None, min_games=5):
    games, teams = load(season)
    snaps, idx = static_snapshots(games, teams)
    past = collections.defaultdict(list)
    by_date = collections.defaultdict(list)
    for r in games: by_date[r["date"]].append(r)
    rows, tau_track = [], []
    for d in sorted(by_date):
        th = snaps[d]
        # cross-section for tau^2 today
        mom = {}
        for t, p in past.items():
            if len(p) >= min_games:
                mom[t] = raw_momentum(p, th, idx, H)
        if mom:
            Ms = np.array([v[0] for v in mom.values()]); SEs = np.array([v[1] for v in mom.values()])
            tau2_today = max(0.0, float(np.mean(Ms ** 2) - np.mean(SEs)))
            tau_track.append((d, tau2_today, len(mom)))
        tau2 = tau2_fixed if tau2_fixed is not None else (tau2_today if mom else 0.0)
        for r in by_date[d]:
            h = 0.0 if r["neutral"] else 1.0
            exp = th[idx[r["home"]]] - th[idx[r["away"]]] + h * th[-1]
            e = r["hs"] - r["as"] - exp                     # next-game residual vs static number (home view)
            sides = []
            for t in (r["home"], r["away"]):
                if t in mom:
                    M, SE2, n = mom[t]
                    k = tau2 / (tau2 + SE2) if tau2 > 0 else 0.0
                    mi = k * M; psd = math.sqrt(tau2 * SE2 / (tau2 + SE2)) if tau2 > 0 else 0.0
                    sides.append((M, SE2, mi, psd))
                else:
                    sides.append(None)
            rows.append({"date": d, "e": e, "h": sides[0], "a": sides[1]})
        for r in by_date[d]:
            s = 0 if r["neutral"] else 1
            past[r["home"]].append((r["away"], s, r["hs"] - r["as"]))
            past[r["away"]].append((r["home"], -s, r["as"] - r["hs"]))
    return rows, tau_track, snaps, idx, past

def evaluate(rows, label):
    ev = [r for r in rows if r["h"] and r["a"] and (r["date"][5:] >= "12-01" or r["date"][5:] < "07-01")]
    y = np.array([r["e"] for r in ev])
    for nm, k in (("raw M", 0), ("shrunk MI", 2)):
        x = np.array([r["h"][k] - r["a"][k] for r in ev])
        if not np.any(x):
            print(f"  {label} {nm}: all zero"); continue
        b = x @ y / (x @ x); res = y - b * x; se = math.sqrt(res @ res / (len(x) - 1) / (x @ x))
        gain = np.mean(abs(y)) - np.mean(abs(y - x))       # MAE change if you add the index to the number as-is
        print(f"  {label} {nm:9s}: slope {b:+.3f} (se {se:.3f}, t {b/se:+.1f}), sd {x.std():.2f}, MAE change adding it: {-gain:+.4f}")
    return ev

if __name__ == "__main__":
    H = None if sys.argv[1] == "last5" else float(sys.argv[1])
    for season in ["2024-25", "2025-26"]:
        rows, tt, *_ = walk(season, H)
        late = [t for d, t, n in tt if d[5:] >= "12-01" or d[5:] < "07-01"]
        print(f"{season} H={sys.argv[1]}: tau (sd of true momentum) median {math.sqrt(np.median(late)):.2f}, "
              f"Dec {math.sqrt(np.median([t for d,t,n in tt if d[5:7]=='12'])):.2f}, Feb {math.sqrt(np.median([t for d,t,n in tt if d[5:7]=='02'])):.2f}")
        evaluate(rows, season)
