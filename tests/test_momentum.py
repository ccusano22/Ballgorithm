"""Synthetic checks of the Momentum Index.

1. A league where every team's strength wanders a little (about as much as real teams do): Hot teams should
   really be better than their season average, Cold teams worse, and Steady teams about even.
2. One team that gets 8 points better halfway through, one that gets 8 worse: they should come out Hot and Cold.
3. Haslametrics ratings.xml parsing, including the -999 'no number' code.
"""
import datetime, os, random, statistics, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "model"))
import momentum as M


def season(shift=None, drift=0.0, seed=3, n_teams=60, days=126, every=4):
    """drift: random-walk SD per day of each team's true strength. Returns games, teams, true-momentum by team."""
    rnd = random.Random(seed)
    teams = [f"T{i:02d}" for i in range(n_teams)]
    lvl = {t: rnd.gauss(0, 8) for t in teams}
    path = {t: [] for t in teams}
    d0 = datetime.date(2025, 11, 3)
    games, gid = [], 0
    for d in range(days):
        for t in teams:
            lvl[t] += rnd.gauss(0, drift)
            if shift and t in shift and d == days // 2:
                lvl[t] += shift[t]
        if d % every:
            continue
        day = d0 + datetime.timedelta(d)
        order = teams[:]; rnd.shuffle(order)
        for a, h in zip(order[0::2], order[1::2]):
            path[a].append(lvl[a]); path[h].append(lvl[h])
            m = lvl[h] - lvl[a] + 3.0 + rnd.gauss(0, 11)
            hs = 70 + round(m / 2); as_ = hs - round(m) or hs - 1
            games.append({"id": str(gid), "date": day.isoformat(), "home": h, "away": a, "neutral": False, "hs": hs, "as": as_})
            gid += 1
    true_mom = {t: lvl[t] - statistics.mean(path[t]) for t in teams}   # now vs its own season average
    return games, teams, true_mom


def index_all(games, teams):
    rating, hca = M.static_ratings(games, teams)
    logs = {t: [] for t in teams}
    for g in games:
        logs[g["home"]].append({"date": g["date"], "opp": g["away"], "site": 1, "margin": g["hs"] - g["as"]})
        logs[g["away"]].append({"date": g["date"], "opp": g["home"], "site": -1, "margin": g["as"] - g["hs"]})
    return {t: M.team_index(logs[t], rating, hca) for t in teams}, hca


# 1. a league that drifts like a real one (about 1 point per month): tags should track true momentum
true_by_tag = {}
for seed in range(4):
    games, teams, true_mom = season(drift=0.2, seed=seed)
    res, hca = index_all(games, teams)
    assert 2.0 < hca < 4.0, hca
    for t, r in res.items():
        true_by_tag.setdefault(r["tag"], []).append(true_mom[t])
avg = {k: round(statistics.mean(v), 2) for k, v in true_by_tag.items()}
print("drifting league, true momentum by tag:", avg, {k: len(v) for k, v in true_by_tag.items()})
assert avg["Hot"] > 0.5 and avg["Cold"] < -0.5 and abs(avg["Steady"]) < 0.5, avg

# 2. one team gets 8 points better halfway through, one gets 8 worse, over 6 simulated seasons.
# A 15-game half-life catches a sudden jump but understates it (the backtest preferred slow windows: most real
# change is gradual), and 11 points of game noise hides it now and then. Sudden changes such as injuries are
# what the board's availability notes are for.
hot = cold = 0; r_ranks, f_ranks = [], []
for seed in range(6):
    games, teams, _ = season(shift={"T05": +8.0, "T07": -8.0}, seed=seed)
    res, _ = index_all(games, teams)
    assert 0 < res["T05"]["index"] < res["T05"]["form"] or res["T05"]["index"] <= 0, "the index must shrink raw form"
    rank = sorted(res, key=lambda t: -res[t]["index"])
    r_ranks.append(rank.index("T05") + 1); f_ranks.append(len(rank) - rank.index("T07"))
    hot += res["T05"]["tag"] in ("Hot", "Warming"); cold += res["T07"]["tag"] in ("Cold", "Cooling")
print(f"8-point swing: riser Hot/Warming {hot}/6 (ranks {r_ranks}), faller Cold/Cooling {cold}/6 (ranks from bottom {f_ranks})")
assert hot >= 5 and cold >= 5, (hot, cold)
assert statistics.median(r_ranks) <= 10 and statistics.median(f_ranks) <= 10, (r_ranks, f_ranks)

# 3. too few games -> no index
few = M.team_index([{"date": "2025-11-03", "opp": "T01", "site": 1, "margin": 5}], {"T01": 0.0}, 3.0)
assert few["index"] is None and few["tag"] is None

# 4. Haslametrics XML
xml = ('<ratings><dt md="11/19/2026"/>'
       '<mr t="Duke" w="3" l="2" mom="1.234" mmo="0.5" mmd="0.734" inc="-2.1"/>'
       '<mr t="Houston" w="5" l="0" mom="-999" mmo="-999" mmd="-999" inc="999"/></ratings>')
hz = M.hasla_xml(xml)
assert hz["Duke"]["mom"] == 1.234 and hz["Duke"]["mom_def"] == 0.734, hz
assert hz["Houston"]["mom"] is None and hz["Houston"]["inconsistency"] is None, hz
print("hasla xml ok", hz)
print("momentum tests passed")
