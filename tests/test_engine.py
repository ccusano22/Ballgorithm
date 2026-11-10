"""Synthetic check of the learning loop: one source is consistently better for one team."""
import random, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "model"))
import engine as E

# cap_weights sanity
w = E.cap_weights({"a": 10, "b": 1, "c": 1, "d": 1, "e": 1, "f": 1})
assert abs(sum(w.values()) - 1) < 1e-9 and max(w.values()) <= 0.45 + 1e-9 and min(w.values()) >= 0.10 - 1e-9, w
print("cap ok", {k: round(v, 3) for k, v in w.items()})

random.seed(7)
s = E.new_state("test")
noise = {"torvik": 9, "hasla": 9, "espn": 12, "dratings": 10, "massey": 9, "cbbd": 11}
for day in range(40):
    games = []
    for i in range(10):
        home = "Alpha" if i == 0 else f"T{day}-{i}h"
        away = f"T{day}-{i}a"
        games.append({"id": f"{day}-{i}", "away": away, "home": home, "away_conf": "X", "home_conf": "X", "neutral": False,
                      "sources": {src: {"margin": None} for src in E.SOURCES}, "market": {"spread_home": None}})
    truths = {}
    for g in games:
        truth = random.gauss(4, 8)
        truths[g["id"]] = truth
        for src in E.SOURCES:
            sd = noise[src]
            if g["home"] == "Alpha":
                sd = 3 if src == "torvik" else 14   # Torvik reads Alpha far better
            g["sources"][src]["margin"] = truth + random.gauss(0, sd)
            tsd = 3 if (g["home"] == "Alpha" and src == "hasla") else 12
            g["sources"][src]["total"] = 145 + random.gauss(0, tsd)
        g["market"]["spread_home"] = round(-(truth + random.gauss(0, 8)) * 2) / 2
    E.build_slate(s, f"d{day}", games)
    res = {}
    for g in games:
        act = round(truths[g["id"]] + random.gauss(0, 2))
        if act == 0: act = 1
        res[g["id"]] = {"away_pts": 70, "home_pts": 75 + act}  # total = 145 + act
    E.grade(s, res)

wa, basis, est = E.team_weights(s, "Alpha")
print("Alpha", basis, {k: round(v, 3) for k, v in wa.items()}, est)
assert wa["torvik"] == max(wa.values()) and wa["torvik"] <= 0.45 + 1e-9
wt, tb, test = E.team_weights(s, "Alpha", "total")
print("Alpha totals", tb, {k: round(v, 3) for k, v in wt.items()})
assert wt["hasla"] == max(wt.values()) and wa["hasla"] < 0.2, (wt, wa)
wb, basis_b, _ = E.team_weights(s, "T39-3h")
print("new team", basis_b, {k: round(v, 3) for k, v in wb.items()})
print("model", s["model"]["n"], round(s["model"]["mae"], 2), s["model"]["sigma"], "records", s["records"])
print("shifts logged", len(s["log"]), "| market bench", s["bench"]["market"])
assert E.result_label(7, 6.6).startswith("Won by 7, on our") and E.result_label(-3, 6.6) == "Lost by 3"
assert s["records"]["hammer"]["w"] + s["records"]["hammer"]["l"] + s["records"]["hammer"]["p"] == 40, s["records"]["hammer"]
# "THAT DAWG" book: one entry per graded team-game, and the list is ranked by shrunk score
assert all(t["dawg"]["n"] == t["n"] for t in s["teams"].values())
E.refresh_display(s)
board = s["display"]["dawgs"]
assert [r["score"] for r in board] == sorted((r["score"] for r in board), reverse=True) and all(r["score"] > 0 for r in board)
d = E.team(s, "Dawgtown")
E.dawg_add(d, 12.0, 20, "Opp", "2026-11-10")   # won by 20, beat our number by 12: a whoopin'
E.dawg_add(d, 12.0, 8, "Opp2", "2026-11-11")    # beat our number but only won by 8: not one
assert d["dawg"]["whoop"] == 1 and d["dawg"]["beat"] == 2 and d["dawg"]["best"]["opp"] == "Opp"
print("dawg ok", board[:1])
print("all checks pass")
