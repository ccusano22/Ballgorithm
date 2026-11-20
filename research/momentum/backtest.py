"""Walk-forward backtest of momentum methods on two full ESPN seasons.

Rating model: every D1 team has a strength theta (points vs an average D1 team, neutral site).
  margin(home - away) = theta_h - theta_a + HCA*(not neutral) + noise(R)
Joint Kalman filter over all teams. Between game days each strength drifts: Var += Q * days.
  Q = 0  -> static rating (every game counts equally; equivalent to ridge regression)
  Q > 0  -> strengths are allowed to move, i.e. momentum is real
Every game is predicted from information strictly before its date.
"""
import os
import json, sys, math, collections, datetime as dt
import numpy as np

def load(season, path="bt/espn"):
    g = json.load(open(f"{path}/games-{season}.json"))
    c = collections.Counter()
    for r in g:
        c[r["home"]] += 1; c[r["away"]] += 1
    d1 = sorted(t for t, n in c.items() if n >= 15)
    games = [r for r in g if r["home"] in d1 and r["away"] in d1]
    games.sort(key=lambda r: (r["date"], r["id"]))
    return games, d1

def run(games, teams, Q, R=11.0**2, S0=9.0**2, cap=None, hca0=3.0, hca_var=1.0):
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    th = np.zeros(n + 1); th[n] = hca0            # last state = home-court advantage
    P = np.eye(n + 1) * S0; P[n, n] = hca_var
    out = []
    last = None
    by_date = collections.defaultdict(list)
    for r in games: by_date[r["date"]].append(r)
    for d in sorted(by_date):
        day = dt.date.fromisoformat(d)
        if last is not None and Q > 0:
            gap = (day - last).days
            P[np.arange(n), np.arange(n)] += Q * gap
        last = day
        todays = by_date[d]
        # predict all of today's games before any update
        preds = []
        for r in todays:
            i, j = idx[r["home"]], idx[r["away"]]
            h = 0.0 if r["neutral"] else 1.0
            mu = th[i] - th[j] + h * th[n]
            var = P[i, i] + P[j, j] - 2 * P[i, j] + h * h * P[n, n] + 2 * h * (P[i, n] - P[j, n]) + R
            preds.append((mu, var))
        for r, (mu, var) in zip(todays, preds):
            y = r["hs"] - r["as"]
            out.append({"id": r["id"], "date": d, "home": r["home"], "away": r["away"], "y": y,
                        "mu": mu, "var": var, "neutral": r["neutral"],
                        "th_h": th[idx[r["home"]]], "th_a": th[idx[r["away"]]]})
        for r in todays:
            i, j = idx[r["home"]], idx[r["away"]]
            h = 0.0 if r["neutral"] else 1.0
            y = r["hs"] - r["as"]
            if cap: y = max(-cap, min(cap, y))
            Hp = P[:, i] - P[:, j] + h * P[:, n]       # P H^T
            S = Hp[i] - Hp[j] + h * Hp[n] + R
            mu = th[i] - th[j] + h * th[n]
            K = Hp / S
            th += K * (y - mu)
            P -= np.outer(K, Hp)
    return out, th, P, idx

def score(out, start="12-01"):
    ev = [o for o in out if o["date"][5:] >= start or o["date"][5:] < "07-01"]
    e = np.array([o["y"] - o["mu"] for o in ev]); v = np.array([o["var"] for o in ev])
    ll = -0.5 * np.mean(np.log(2 * math.pi * v) + e * e / v)
    return {"n": len(ev), "mae": float(np.mean(abs(e))), "rmse": float(np.sqrt(np.mean(e * e))), "ll": float(ll),
            "su": float(np.mean([(o["y"] > 0) == (o["mu"] > 0) for o in ev if o["y"] != 0]))}

if __name__ == "__main__":
    for season in ["2024-25", "2025-26"]:
        games, teams = load(season)
        for Q in [0, 0.005, 0.01, 0.02, 0.04, 0.08, 0.16]:
            out, *_ = run(games, teams, Q)
            s = score(out)
            print(season, f"Q={Q:<6}", "sd/30d=%.2f" % math.sqrt(Q * 30), {k: round(v, 4) for k, v in s.items()}, flush=True)
