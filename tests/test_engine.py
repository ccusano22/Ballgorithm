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
noise = {"kenpom": 9, "torvik": 9, "hasla": 9, "espn": 12}
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
print("all checks pass")
