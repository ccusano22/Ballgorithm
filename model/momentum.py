"""Momentum Index: who is hot and who is cold, in points, with how sure we are.

Display only. Nothing here moves the blend.

How it works, every run, from every D1-vs-D1 final this season (ESPN):

1. Static rating. A joint Kalman filter (every game counted equally) rates each team in points vs an average
   D1 team on a neutral floor, plus home-court advantage.
2. Game scores. Each game becomes a performance number for each team:
       game score = the team's margin + the opponent's current rating - home edge (when the team was home)
   Old games get re-scored as opponents' ratings move, so beating a team that turned out good counts for more.
3. Raw form M. The recency-weighted average game score (half-life HALF_LIFE games) minus the season average.
   That's how much better or worse the team has played lately than its own season level, before luck is removed.
4. Shrink. A 5-game hot streak is mostly noise. The noise in M is SE^2 = SIGMA^2 * sum (w - 1/n)^2. The real
   change it could reflect is modeled as a random walk, which gives a prior V = Q * sum c_k c_l min(t_k, t_l)
   for the same weighted contrast (t = days into the season). The index keeps the share that carries forward:
       index = M * V / (V + SE^2),   SD = sqrt(SE^2 * V / (V + SE^2)),   P(hot) = Phi(index / SD)
   Early in the season the days are bunched, V is small and the index stays near zero; by March about 3/4 of
   raw form carries. Q is a calibration constant, not a literal day-to-day drift: it was fit walk-forward on
   2024-25 and 2025-26 (18,000 team-games) so the index predicts next-game results one-for-one (a team at +1.0
   beats the number by about 1.0 next time out, on average). Tags use the index itself, in points.
   Limits: game noise is about 11 points, so even a real 8-point swing at midseason is caught about 5 times in 6
   and understated (simulations in tests/test_momentum.py). Sudden changes such as injuries show up slowly.
5. Haslametrics. Its own Momentum, Momentum-Offense and Momentum-Defense (ratings.xml) are shown beside ours.
   Each day's numbers are archived, and once HASLA_MIN_GAMES later games are graded, Haslametrics earns a weight
   in the index only if it adds predictive value beyond ours (t >= 2). Until then its weight is 0.

Backtest (both seasons, games from Dec 1 with both teams 5+ games):
  Hot     (index >= +0.75, ~4% of team-games):  next game +0.72 pts vs the static number, beat it 52.6%
  Warming (+0.25 to +0.75, ~17%):              +0.39, 51.4%
  Cooling (-0.25 to -0.75, ~16%):              -0.60, 47.9%
  Cold    (index <= -0.75, ~4%):               -1.57, 44.3%   (cold carries forward more than hot)
  The board's current 4-of-5 rule: Hot beat it 50.8%, Cold 50.5% (no better than a coin flip).
"""
import datetime, glob, json, math, os, re, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import names as N

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
SITE = os.path.join(ROOT, "site", "data")
ARCHIVE = os.path.join(ROOT, "data", "momentum")

HALF_LIFE = 15.0     # games: a game 15 games back counts half as much as the latest one
Q = 1.0              # calibration of the random-walk prior (points^2 per day), fit walk-forward on 2024-25 + 2025-26
SIGMA = 11.4         # one game's noise around a team's true level (points), measured on the same seasons
MIN_GAMES = 5        # below this, no index
R_OBS, S0, HCA0, HCA_VAR = 11.0 ** 2, 9.0 ** 2, 3.0, 0.05     # static rating filter; home edge held near 3
# (a loose home-edge prior soaks up November's buy games, where the stronger team is nearly always at home)
TAGS = ((0.75, "Hot"), (0.25, "Warming"), (-0.25, "Steady"), (-0.75, "Cooling"), (-99.0, "Cold"))   # index, points
HASLA_MIN_GAMES = 400
BACKTEST = {
    "seasons": ["2024-25", "2025-26"], "team_games": 18044,
    "slope": 0.99, "slope_se": 0.21, "season_slopes": [0.92, 1.07],
    "buckets": [  # share of team-games, average index, next game vs the static number, % beating it
        {"tag": "Hot", "share": 4, "index": 1.01, "next": 0.72, "se": 0.41, "beat": 52.6},
        {"tag": "Warming", "share": 17, "index": 0.44, "next": 0.39, "se": 0.21, "beat": 51.4},
        {"tag": "Steady", "share": 60, "index": 0.00, "next": 0.10, "se": 0.11, "beat": 50.4},
        {"tag": "Cooling", "share": 16, "index": -0.44, "next": -0.60, "se": 0.21, "beat": 47.9},
        {"tag": "Cold", "share": 4, "index": -1.02, "next": -1.57, "se": 0.44, "beat": 44.3}],
    "old_rule": {"hot_beat": [50.9, 50.7], "cold_beat": [50.7, 50.3], "hot_next": [0.29, 0.30], "cold_next": [-0.10, -0.33]},
    "drift_pts_per_30_days": 1.1,
}
Phi = lambda z: 0.5 * (1 + math.erf(z / math.sqrt(2)))


# ---------------- data ----------------
def _espn_team(c):
    t = c.get("team", {})
    if t.get("conferenceId") is None:          # non-D1 opponents have no conference
        return None
    for key in ("location", "shortDisplayName", "displayName", "name"):
        x = N.to_canon(t.get(key) or "")
        if x:
            return x
    return N.to_canon(t.get("displayName", ""), allow_prefix=True)


def finals_from_scoreboard(sb, day):
    out = []
    for ev in (sb or {}).get("events", []):
        if not ev.get("status", {}).get("type", {}).get("completed"):
            continue
        comp = (ev.get("competitions") or [{}])[0]
        cs = {c.get("homeAway"): c for c in comp.get("competitors", [])}
        if "home" not in cs or "away" not in cs:
            continue
        h, a = _espn_team(cs["home"]), _espn_team(cs["away"])
        if not h or not a or h == a:
            continue
        try:
            hs, as_ = int(cs["home"].get("score")), int(cs["away"].get("score"))
        except (TypeError, ValueError):
            continue
        out.append({"id": str(ev["id"]), "date": day, "home": h, "away": a, "neutral": bool(comp.get("neutralSite")),
                    "hs": hs, "as": as_})
    return out


def season_finals(today, raw=RAW):
    """Every completed D1-vs-D1 game this season before `today`, from the saved ESPN scoreboards."""
    start = datetime.date(today.year if today.month >= 8 else today.year - 1, 8, 1)
    seen, games = set(), []
    for path in sorted(glob.glob(os.path.join(raw, "*"))):
        d = os.path.basename(path)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            continue
        day = datetime.date.fromisoformat(d)
        if not (start <= day < today):
            continue
        for name in ("espn_final.json", "espn_scoreboard.json"):
            try:
                sb = json.load(open(os.path.join(path, name), encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                continue
            for g in finals_from_scoreboard(sb, d):
                if g["id"] not in seen:
                    seen.add(g["id"]); games.append(g)
    games.sort(key=lambda g: (g["date"], g["id"]))
    return games


def hasla_xml(text):
    """Haslametrics ratings.xml -> {team: {mom, mom_off, mom_def, inconsistency}}. -999 / 999 mean 'no number yet'."""
    out = {}
    for m in re.finditer(r"<mr\b([^>]*)/?>", text or ""):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        team = N.to_canon(attrs.get("t", "")) or N.to_canon(attrs.get("t", ""), True)
        if not team:
            continue
        def num(k, bad):
            try:
                v = float(attrs.get(k, ""))
            except ValueError:
                return None
            return None if v == bad else round(v, 3)
        out[team] = {"mom": num("mom", -999), "mom_off": num("mmo", -999), "mom_def": num("mmd", -999),
                     "inconsistency": num("inc", 999)}
    return out


# ---------------- model ----------------
def static_ratings(games, teams):
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    th = np.zeros(n + 1); th[n] = HCA0
    P = np.eye(n + 1) * S0; P[n, n] = HCA_VAR
    for g in games:
        i, j = idx[g["home"]], idx[g["away"]]
        h = 0.0 if g["neutral"] else 1.0
        Hp = P[:, i] - P[:, j] + h * P[:, n]
        S = Hp[i] - Hp[j] + h * Hp[n] + R_OBS
        K = Hp / S
        th += K * (g["hs"] - g["as"] - (th[i] - th[j] + h * th[n]))
        P -= np.outer(K, Hp)
    return {t: float(th[idx[t]]) for t in teams}, float(th[n])


def team_index(log, rating, hca):
    """log: this team's games oldest first, each {date, opp, site(+1 home/-1 away/0 neutral), margin, ...}."""
    n = len(log)
    gs = np.array([g["margin"] + rating[g["opp"]] - g["site"] * hca for g in log])
    season_level = float(gs.mean())
    out = {"n": n, "season_level": round(season_level, 2),
           "games": [{**g, "game_score": round(float(s), 1), "vs_level": round(float(s - season_level), 1)}
                     for g, s in zip(log, gs)]}
    if n < MIN_GAMES:
        return {**out, "form": None, "index": None, "sd": None, "p_hot": None, "tag": None}
    w = 0.5 ** ((n - 1 - np.arange(n)) / HALF_LIFE); w /= w.sum()
    c = w - 1.0 / n
    M = float(c @ gs)
    se2 = SIGMA ** 2 * float(c @ c)
    t = np.array([datetime.date.fromisoformat(g["date"]).toordinal() for g in log], float); t -= t[0]
    V = Q * float(c @ np.minimum.outer(t, t) @ c)
    k = V / (V + se2) if V > 0 else 0.0
    idx = k * M
    sd = math.sqrt(k * se2) if k > 0 else 0.0
    p = Phi(idx / sd) if sd > 0 else 0.5
    tag = next(lab for cut, lab in TAGS if idx >= cut)
    return {**out, "form": round(M, 2), "index": round(idx, 2), "sd": round(sd, 2), "p_hot": round(p, 3),
            "carry": round(k, 3), "tag": tag}


def hasla_weight(today, archive=ARCHIVE, raw=RAW):
    """Learn how much Haslametrics' Momentum adds beyond ours, from archived pre-game snapshots and later finals."""
    snaps = {}
    for p in glob.glob(os.path.join(archive, "*.json")):
        try:
            snaps[os.path.basename(p)[:10]] = json.load(open(p))
        except (json.JSONDecodeError, OSError):
            pass
    X, y = [], []
    for g in season_finals(today, raw):
        s = snaps.get(g["date"])
        if not s:
            continue
        a, b = s["teams"].get(g["home"]), s["teams"].get(g["away"])
        if not a or not b or None in (a[1], b[1], a[3], b[3]):
            continue
        h = 0.0 if g["neutral"] else 1.0
        e = g["hs"] - g["as"] - (a[0] - b[0] + h * s["hca"]) - (a[1] - b[1])   # residual after rating + our index
        X.append(a[3] - b[3]); y.append(e)
    n = len(X)
    if n < HASLA_MIN_GAMES:
        return {"weight": 0.0, "games": n, "status": f"learning ({n} of {HASLA_MIN_GAMES} games graded)"}
    X, y = np.array(X), np.array(y)
    b = float(X @ y / (X @ X)); res = y - b * X
    se = math.sqrt(float(res @ res) / (n - 1) / float(X @ X))
    if b / se < 2:
        return {"weight": 0.0, "games": n, "status": f"no added value yet (t = {b/se:.1f})", "fit": round(b, 3)}
    return {"weight": round(b, 3), "games": n, "status": f"active (t = {b/se:.1f})", "fit": round(b, 3)}


def build(today, raw=RAW, archive=ARCHIVE, hasla_text=None):
    games = season_finals(today, raw)
    teams = sorted({g["home"] for g in games} | {g["away"] for g in games})
    rating, hca = static_ratings(games, teams) if games else ({}, HCA0)
    logs = {t: [] for t in teams}
    for g in games:
        s = 0 if g["neutral"] else 1
        logs[g["home"]].append({"date": g["date"], "opp": g["away"], "site": s, "margin": g["hs"] - g["as"],
                                "score": f"{g['hs']}-{g['as']}"})
        logs[g["away"]].append({"date": g["date"], "opp": g["home"], "site": -s, "margin": g["as"] - g["hs"],
                                "score": f"{g['as']}-{g['hs']}"})
    if hasla_text is None:
        hasla_text = ""
        for d in sorted(glob.glob(os.path.join(raw, "*", "hasla_ratings.xml")), reverse=True):
            if os.path.basename(os.path.dirname(d)) <= today.isoformat():
                hasla_text = open(d, encoding="utf-8", errors="replace").read(); break
    hz = hasla_xml(hasla_text)
    hw = hasla_weight(today, archive, raw)
    rows = []
    for t in teams:
        r = team_index(logs[t], rating, hca)
        r["games"] = r["games"][-8:]
        hm = hz.get(t)
        combined = r["index"]
        if combined is not None and hm and hm.get("mom") is not None and hw["weight"]:
            combined = round(combined + hw["weight"] * hm["mom"], 2)
        rows.append({"team": t, "conf": N.TEAMS.get(t, ""), "rating": round(rating[t], 2), **r,
                     "hasla": hm, "combined": combined})
    rows.sort(key=lambda r: (r["index"] is None, -(r["combined"] or 0)))
    out = {"date": today.isoformat(), "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
           "games_used": len(games), "hca": round(hca, 2),
           "params": {"half_life_games": HALF_LIFE, "q": Q, "sigma": SIGMA, "min_games": MIN_GAMES,
                      "tags": {lab: cut for cut, lab in TAGS}},
           "hasla": {"teams": len(hz), "weight": hw}, "backtest": BACKTEST, "teams": rows}
    return out


def save(out, site=SITE, archive=ARCHIVE):
    os.makedirs(site, exist_ok=True); os.makedirs(archive, exist_ok=True)
    with open(os.path.join(site, "momentum.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    snap = {"hca": out["hca"], "teams": {r["team"]: [r["rating"], r["index"], r["sd"], (r["hasla"] or {}).get("mom")]
                                         for r in out["teams"]}}
    with open(os.path.join(archive, f"{out['date']}.json"), "w", encoding="utf-8") as f:
        json.dump(snap, f, separators=(",", ":"))


def main():
    import zoneinfo
    ET = zoneinfo.ZoneInfo("America/New_York")
    v = os.environ.get("BALLGO_NOW")
    now = datetime.datetime.fromisoformat(v).astimezone(ET) if v else datetime.datetime.now(ET)
    today = datetime.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else now.date()
    out = build(today)
    save(out)
    rated = [r for r in out["teams"] if r["index"] is not None]
    hot = [r["team"] for r in rated if r["tag"] == "Hot"][:5]
    cold = [r["team"] for r in reversed(rated) if r["tag"] == "Cold"][:5]
    print(f"momentum: {out['games_used']} games, {len(rated)} teams rated; hot {hot or '-'}; cold {cold or '-'}; "
          f"Haslametrics {out['hasla']['teams']} teams, {out['hasla']['weight']['status']}")


if __name__ == "__main__":
    main()
