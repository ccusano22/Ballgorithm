"""EB momentum with the prior on the contrast derived from a random-walk strength (q pts^2 per day).
prior var V = q * sum_kl c_k c_l min(t_k, t_l)   (t in days since team's first game)
noise var  SE2 = sigma^2 * c'c ;  k = V/(V+SE2) ; MI = k*M ; psd = sqrt(k*SE2)
"""
import os
import sys, math, collections, datetime as dt, pickle
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import load
from eb_momentum import static_snapshots, Phi
SIG2 = 11.4 ** 2

def walk(season, H, min_games=5):
    games, teams = load(season)
    snaps, idx = static_snapshots(games, teams)
    past = collections.defaultdict(list)
    by_date = collections.defaultdict(list)
    for r in games: by_date[r["date"]].append(r)
    rows = []
    for d in sorted(by_date):
        th = snaps[d]; hca = th[-1]
        mom = {}
        for t, p in past.items():
            n = len(p)
            if n < min_games: continue
            gs = np.array([m + th[idx[o]] - s * hca for o, s, m, _ in p])
            w = 0.5 ** ((n - 1 - np.arange(n)) / H); w /= w.sum(); c = w - 1.0 / n
            tt = np.array([dd for *_, dd in p], float); tt -= tt[0]
            Mn = np.minimum.outer(tt, tt)
            mom[t] = (float(c @ gs), float(SIG2 * (c @ c)), float(c @ Mn @ c))
        for r in by_date[d]:
            h = 0.0 if r["neutral"] else 1.0
            e = r["hs"] - r["as"] - (th[idx[r["home"]]] - th[idx[r["away"]]] + h * hca)
            rows.append({"date": d, "e": e, "h": mom.get(r["home"]), "a": mom.get(r["away"])})
        day = dt.date.fromisoformat(d).toordinal()
        for r in by_date[d]:
            s = 0 if r["neutral"] else 1
            past[r["home"]].append((r["away"], s, r["hs"] - r["as"], day))
            past[r["away"]].append((r["home"], -s, r["as"] - r["hs"], day))
    return [r for r in rows if r["h"] and r["a"] and (r["date"][5:] >= "12-01" or r["date"][5:] < "07-01")], rows

def shr(side, q):
    M, SE2, Q = side; V = q * Q; k = V / (V + SE2) if V > 0 else 0.0
    return k * M, math.sqrt(k * SE2) if k > 0 else 1e-9

def stats(ev, q):
    y = np.array([r["e"] for r in ev]); x = np.array([shr(r["h"], q)[0] - shr(r["a"], q)[0] for r in ev])
    b = x @ y / (x @ x); res = y - b * x; se = math.sqrt(res @ res / (len(x) - 1) / (x @ x))
    return b, se, np.mean(abs(y - x)) - np.mean(abs(y)), x.std()

if __name__ == "__main__":
    os.makedirs("bt", exist_ok=True); data = {}
    for H in (8, 15, 25):
        for s in ("2024-25", "2025-26"):
            data[(H, s)] = walk(s, H)
    pickle.dump(data, open("bt/rw_cache.pkl", "wb"))
    qs = [0.01, 0.02, 0.03, 0.04, 0.06, 0.08, 0.12, 0.16]
    for H in (8, 15, 25):
        a, b_ = data[(H, "2024-25")][0], data[(H, "2025-26")][0]
        for lab, tr, te in (("fit 24-25 -> test 25-26", a, b_), ("fit 25-26 -> test 24-25", b_, a)):
            q = min(qs, key=lambda q: abs(stats(tr, q)[0] - 1))
            s1, s2 = stats(tr, q), stats(te, q)
            print(f"H={H:>2} {lab}: q={q} ({math.sqrt(q*30):.2f} pts/30d)  train slope {s1[0]:.2f}  TEST slope {s2[0]:.2f}±{s2[1]:.2f} (t {s2[0]/s2[1]:.1f}) MAE {s2[2]:+.4f} sd {s2[3]:.2f}")
        pool = a + b_; q = min(qs, key=lambda q: abs(stats(pool, q)[0] - 1)); s = stats(pool, q)
        print(f"H={H:>2} pooled q={q}: slope {s[0]:.2f}±{s[1]:.2f} (t {s[0]/s[1]:.1f}) MAE {s[2]:+.4f}")
