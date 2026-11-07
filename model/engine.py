"""
Crystal Ball NCAAM engine — blend, uncertainty, source weighting, grading.

One state dict (state.json) carries the season. Every daily run:
  1. grade(state, results)      -> grades PENDING, updates records, form, per-source error, weights
  2. build_slate(state, games)   -> blended predictions for today, written back into PENDING
All margins are HOME minus AWAY (for neutral sites, "home" is just the second-listed team).
"""
import math, statistics, copy, datetime

SOURCES = ["torvik", "hasla", "espn", "dratings", "massey", "cbbd"]
LABEL = {"torvik": "Torvik", "hasla": "Haslametrics", "espn": "ESPN", "dratings": "DRatings", "massey": "Massey", "cbbd": "CBBD"}
# dropped Nov 2: CHD appears shut down; EvanMiya's free page only covers its top 50.
# dropped Nov 5: KenPom (its logged-in pages block automated access).
RETIRED = ["chd", "evanmiya", "kenpom"]
CORE = ("torvik", "hasla", "espn", "dratings", "massey")   # sources that publish their own game projection (CBBD is derived from ratings)
# Scored every game like a source, but never weighted into the blend.
BENCH = ["market"]

# ---- Constants (each tied to evidence or marked provisional) ----
MAE_PRIOR = 8.85                                  # Craig's Colab model, held-out margin MAE on ~2,600 games
SIGMA0 = round(MAE_PRIOR * math.sqrt(math.pi / 2), 2)  # normal errors: SD = MAE * sqrt(pi/2) -> 11.09
SIGMA_SHRINK_N = 100                              # season SD replaces the prior gradually (n/(n+100))
K_SHRINK = 10                                     # team error shrunk toward prior with 10 pseudo-games
MIN_TEAM_GAMES = 10                               # instructions: team-level override starts at 10 games
GROUP_MIN = 30                                    # conference-level error used once 30 team-games graded
CAP_LO, CAP_HI = 0.10, 0.45                       # instructions: each source 10-45% of the blend
SHIFT_REPORT = 0.05                               # provisional: report a weight move of 5+ points
LOCK = 0.90                                       # instructions: lock at >=90% win probability
TOTAL_LEAN = 6.0                                  # instructions: total lean trigger
HAMMER_MAX_SPREAD = 20.0                          # Nov 7: no Hammer on DK spreads above 20 (early-season mismatches went 0-2)
# Tier cutoffs are PROVISIONAL until ~200 graded games let us set them from data.
# Disagreement is measured as the spread of each source's implied win chance, so a 40-point favorite isn't
# flagged because sources differ by 4 points on the margin. 0.07 / 0.14 match 2 / 4 points near a coin flip.
TIER = {"strong_psd": 0.07, "strong_p": 0.75, "weak_psd": 0.14, "bad_loss_p": 0.75}


def phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def phi_inv(p):
    # Acklam's rational approximation (abs err < 1.2e-9)
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    pl, ph = 0.02425, 1 - 0.02425
    if p < pl:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > ph:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def margin_from_winprob(p, sigma=SIGMA0):
    """Convert a source's published win probability into a point margin on our scale."""
    return phi_inv(p) * sigma


# ---- State ----
def new_state(season, artifact_url=None):
    return {
        "season": season,
        "updated": None,
        "artifact_url": artifact_url,
        "constants": {"MAE_PRIOR": MAE_PRIOR, "SIGMA0": SIGMA0, "K_SHRINK": K_SHRINK, "MIN_TEAM_GAMES": MIN_TEAM_GAMES,
                       "GROUP_MIN": GROUP_MIN, "CAP": [CAP_LO, CAP_HI], "SHIFT_REPORT": SHIFT_REPORT, "LOCK": LOCK,
                       "TOTAL_LEAN": TOTAL_LEAN, "TIER": TIER, "HAMMER_MAX_SPREAD": HAMMER_MAX_SPREAD},
        "records": {"su": {"w": 0, "l": 0}, "hammer": {"w": 0, "l": 0, "p": 0, "since": None}},
        "model": {"n": 0, "mae": 0.0, "brier_sum": 0.0, "correct": 0, "sigma": SIGMA0,
                  "tot_n": 0, "tot_mae": 0.0},
        "sources": {s: {"n": 0, "mae": 0.0} for s in SOURCES},
        "sources_tot": {s: {"n": 0, "mae": 0.0} for s in SOURCES},
        "bench": {b: {"n": 0, "mae": 0.0} for b in BENCH},
        "groups": {},
        "groups_tot": {},
        "teams": {},
        "standing_notes": [],
        "pending": None,
        "history": [],   # one compact line per graded day
        "log": [],       # weight shifts and other reportable changes
    }


def _run(avg, n, x):
    """Running average: new = old + (x - old) / new_n (never stores a game log)."""
    n += 1
    return avg + (x - avg) / n, n


def migrate(state):
    """Bring an older state file up to the current source list without touching existing numbers."""
    for book in ("sources", "sources_tot", "display"):
        for r in RETIRED:
            (state.get(book) or {}).pop(r, None)
    for gk in ("groups", "groups_tot"):
        for g in (state.get(gk) or {}).values():
            for r in RETIRED: g.pop(r, None)
    for t in state.get("teams", {}).values():
        for k in ("err", "terr", "weights", "tweights"):
            for r in RETIRED: (t.get(k) or {}).pop(r, None)
    for pg in ((state.get("pending") or {}).get("games") or []):
        for k in ("sources", "src_totals"):
            for r in RETIRED: (pg.get(k) or {}).pop(r, None)
    for s in SOURCES:
        state["sources"].setdefault(s, {"n": 0, "mae": 0.0})
        for g in state["groups"].values():
            g.setdefault(s, {"n": 0, "mae": 0.0})
        for t in state["teams"].values():
            t["err"].setdefault(s, {"n": 0, "mae": 0.0})
    # Nov 2: totals get their own per-team / conference / D1 error books and weights
    state.setdefault("sources_tot", {})
    state.setdefault("groups_tot", {})
    for s in SOURCES:
        state["sources_tot"].setdefault(s, {"n": 0, "mae": 0.0})
        for g in state["groups_tot"].values():
            g.setdefault(s, {"n": 0, "mae": 0.0})
        for t in state["teams"].values():
            t.setdefault("terr", {}).setdefault(s, {"n": 0, "mae": 0.0})
    rec = state.setdefault("records", {})
    if "craig" in rec:                      # Nov 2: "Craig Picks" retired; the straight-up record lives on as "su"
        rec["su"] = rec.pop("craig")
    rec.setdefault("su", {"w": 0, "l": 0})
    if "since" not in rec.get("hammer", {}):  # Nov 2: Hammer redefined as consensus vs the DraftKings spread
        rec["hammer"] = {"w": 0, "l": 0, "p": 0, "since": "2026-11-02"}
    state.setdefault("bench", {})
    for b in BENCH:
        state["bench"].setdefault(b, {"n": 0, "mae": 0.0})
        for t in state["teams"].values():
            t.setdefault("bench", {}).setdefault(b, {"n": 0, "mae": 0.0})
    return state


def team(state, name, conf=None):
    t = state["teams"].get(name)
    if t is None:
        t = {"conf": conf, "n": 0, "form": [], "losses": [], "err": {s: {"n": 0, "mae": 0.0} for s in SOURCES},
             "terr": {s: {"n": 0, "mae": 0.0} for s in SOURCES},
             "tot": {"n": 0, "avg": 0.0}, "weights": None, "basis": "default",
             "bench": {b: {"n": 0, "mae": 0.0} for b in BENCH}}
        state["teams"][name] = t
    elif conf and not t.get("conf"):
        t["conf"] = conf
    return t


# ---- Weighting ----
def cap_weights(raw, lo=CAP_LO, hi=CAP_HI):
    """Normalize, then clip into [lo, hi], redistributing the excess proportionally among unclipped sources."""
    keys = list(raw)
    tot = sum(raw.values())
    w = {k: raw[k] / tot for k in keys}
    fixed = {}
    for _ in range(20):
        free = [k for k in keys if k not in fixed]
        left = 1 - sum(fixed.values())
        ftot = sum(raw[k] for k in free)
        for k in free:
            w[k] = left * raw[k] / ftot
        high = [k for k in free if w[k] > hi + 1e-12]
        low = [k for k in free if w[k] < lo - 1e-12]
        if not high and not low:
            break
        # clip one side per pass so the leftover share is redistributed correctly
        for k in (high or low):
            fixed[k] = hi if high else lo
            w[k] = fixed[k]
    return w


BOOKS = {"margin": ("err", "groups", "sources"), "total": ("terr", "groups_tot", "sources_tot")}


def team_weights(state, name, kind="margin"):
    """Return (weights over all 5 sources, basis label, per-source error estimate used)."""
    t = state["teams"].get(name) or team(state, name)
    tk, gk, dk = BOOKS[kind]
    g = state[gk].get(t.get("conf") or "", {})
    est, basis = {}, set()
    for s in SOURCES:
        prior, pb = None, None
        gs = g.get(s)
        if gs and gs["n"] >= GROUP_MIN:
            prior, pb = gs["mae"], "conference"
        elif state[dk][s]["n"] >= GROUP_MIN:
            prior, pb = state[dk][s]["mae"], "D1"
        te = t[tk][s]
        if te["n"] >= MIN_TEAM_GAMES:
            n = te["n"]
            est[s] = (n * te["mae"] + K_SHRINK * prior) / (n + K_SHRINK) if prior else te["mae"]
            basis.add("team")
        elif prior is not None:
            est[s] = prior
            basis.add(pb)
    if not est:
        return {s: 1 / len(SOURCES) for s in SOURCES}, "default", {}
    fill = statistics.mean(est.values())
    raw = {s: 1 / max(est.get(s, fill), 0.5) ** 2 for s in SOURCES}
    label = "team" if "team" in basis else ("conference" if "conference" in basis else "D1")
    return cap_weights(raw), label, {s: round(v, 2) for s, v in est.items()}


# ---- Prediction ----
def predict_game(state, g):
    """g: {id, away, home, away_conf, home_conf, neutral, site, tip, sources:{s:{margin,total,note,derived}}, market, notes}"""
    ta, th = team(state, g["away"], g.get("away_conf")), team(state, g["home"], g.get("home_conf"))
    wa, ba, _ = team_weights(state, g["away"])
    wh, bh, _ = team_weights(state, g["home"])
    avail = [s for s in SOURCES if g["sources"].get(s, {}).get("margin") is not None]
    if not avail:
        raise ValueError(f"No source margins for {g['away']} vs {g['home']}")
    pair = {s: (wa[s] + wh[s]) / 2 for s in SOURCES}
    use = {s: pair[s] for s in avail}
    tot = sum(use.values())
    use = {s: v / tot for s, v in use.items()}
    margins = {s: g["sources"][s]["margin"] for s in avail}
    margin = sum(use[s] * margins[s] for s in avail)
    src_sd = statistics.pstdev(margins.values()) if len(avail) > 1 else 0.0
    sig_model = state["model"]["sigma"]
    p_sd = statistics.pstdev([phi(v / sig_model) for v in margins.values()]) if len(avail) > 1 else 0.0
    sd = math.sqrt(sig_model ** 2 + src_sd ** 2)
    p_home = phi(margin / sd)

    tavail = [s for s in SOURCES if g["sources"].get(s, {}).get("total") is not None]
    total, use_t = None, {}
    if tavail:
        wta, _, _ = team_weights(state, g["away"], "total")
        wth, _, _ = team_weights(state, g["home"], "total")
        raw_t = {s: (wta[s] + wth[s]) / 2 for s in tavail}
        tt = sum(raw_t.values())
        use_t = {s: v / tt for s, v in raw_t.items()}
        total = sum(use_t[s] * g["sources"][s]["total"] for s in tavail)
    baseline = None
    if ta["tot"]["n"] and th["tot"]["n"]:
        baseline = (ta["tot"]["avg"] + th["tot"]["avg"]) / 2
    lean = None
    if total is not None and baseline is not None:
        d = total - baseline
        lean = "over" if d >= TOTAL_LEAN else "under" if d <= -TOTAL_LEAN else "near"
    tot_sd = None
    if state["model"]["tot_n"] >= 30:
        tot_sd = round(state["model"]["tot_mae"] * math.sqrt(math.pi / 2), 1)

    fav_home = margin >= 0
    pick = g["home"] if fav_home else g["away"]
    dog = g["away"] if fav_home else g["home"]
    p_pick = p_home if fav_home else 1 - p_home

    def form_flag(name):
        f = state["teams"][name]["form"][-5:]
        if len(f) < 5:
            return None
        if sum(1 for r in f if r > 0) >= 4:
            return "hot"
        if sum(1 for r in f if r < 0) >= 4:
            return "cold"
        return None

    def momentum(name):
        f = state["teams"][name]["form"][-5:]
        return {"n": len(f), "avg": round(statistics.mean(f), 1) if f else None, "last": [round(x, 1) for x in f],
                "flag": form_flag(name)}

    def bad_loss(name):
        return any(state["teams"][name]["losses"][-2:])

    flags = [form_flag(x) for x in (g["away"], g["home"]) if form_flag(x)]
    core = [s for s in CORE if s in avail]
    if p_sd > TIER["weak_psd"] or flags or bad_loss(g["away"]) or bad_loss(g["home"]):
        tier = "Weak"
        why = []
        if p_sd > TIER["weak_psd"]:
            why.append(f"sources split on the winner's chances (±{p_sd*100:.0f}%)")
        if flags:
            why.append("hot/cold flag")
        if bad_loss(g["away"]) or bad_loss(g["home"]):
            why.append("bad loss in last 2")
        reason = "; ".join(why)
    elif len(core) >= 2 and p_sd <= TIER["strong_psd"] and p_pick >= TIER["strong_p"]:
        tier, reason = "Strong", f"{len(core)} core systems agree (win chance ±{p_sd*100:.0f}%)"
    else:
        tier = "Mid"
        if len(core) < 2:
            reason = f"only {len(avail)} of {len(SOURCES)} sources have a number" + (f"; they agree within {2*src_sd:.1f} pts" if len(avail) > 1 else "")
        elif p_sd > TIER["strong_psd"]:
            reason = f"some source disagreement (win chance ±{p_sd*100:.0f}%)"
        else:
            reason = "thin margin"

    out = copy.deepcopy(g)
    out.update({
        "weights": {"away": {"basis": ba, "w": {s: round(wa[s], 3) for s in SOURCES}},
                    "home": {"basis": bh, "w": {s: round(wh[s], 3) for s in SOURCES}},
                    "used": {s: round(v, 3) for s, v in use.items()},
                    "used_total": {s: round(v, 3) for s, v in use_t.items()}},
        "blend": {"margin": round(margin, 2), "src_sd": round(src_sd, 2), "model_sd": round(sig_model, 2),
                  "sd": round(sd, 2), "p_home": round(p_home, 4), "p_sd": round(p_sd, 4), "total": round(total, 1) if total is not None else None,
                  "total_sd": tot_sd, "baseline_total": round(baseline, 1) if baseline is not None else None,
                  "lean": lean, "n_sources": len(avail)},
        "pick": {"team": pick, "dog": dog, "p": round(p_pick, 4), "margin": round(abs(margin), 2),
                 "lock": p_pick >= LOCK},
        "momentum": {g["away"]: momentum(g["away"]), g["home"]: momentum(g["home"])},
        "tier": tier, "tier_reason": reason,
        "result": None,
    })
    return out


def hammer_candidates(preds):
    """For every game with a DraftKings spread of HAMMER_MAX_SPREAD or less: which side our blend likes, how many sources agree, and the
    chance that side covers (normal model, blended margin and SD)."""
    out = []
    for p in preds:
        mk = p.get("market") or {}
        if mk.get("spread_home") is None or abs(mk["spread_home"]) > HAMMER_MAX_SPREAD:
            continue
        line_home = -mk["spread_home"]                       # DK's expected home margin
        edge = p["blend"]["margin"] - line_home
        if abs(edge) < 1e-9:
            continue
        side = "home" if edge > 0 else "away"
        srcs = {s: v["margin"] for s, v in p["sources"].items() if v.get("margin") is not None}
        agree = sum(1 for v in srcs.values() if (v - line_home) * edge > 0)
        team = p["home"] if side == "home" else p["away"]
        spread = mk["spread_home"] if side == "home" else -mk["spread_home"]
        out.append({"id": p["id"], "team": team, "side": side, "spread": spread, "spread_home": mk["spread_home"],
                    "edge": round(abs(edge), 2), "cover_p": round(phi(abs(edge) / p["blend"]["sd"]), 4),
                    "agree": agree, "n": len(srcs)})
    # Unanimous (3+ sources all on the same side of the line) first, then the highest chance to cover.
    out.sort(key=lambda c: (c["n"] >= 3 and c["agree"] == c["n"], c["cover_p"], c["agree"]), reverse=True)
    return out


def set_hammer(slate, state, eligible=None):
    """Pick (or re-pick) the Hammer of the Day and mirror it into PENDING. Graded games, and any game not in
    `eligible` (e.g. already tipped), are left alone."""
    live = [g for g in slate["games"] if not g.get("result") and (eligible is None or g["id"] in eligible)]
    for g in live:
        g.pop("hammer", None)
    cands = hammer_candidates(live)
    slate["hammer"] = cands[0] if cands else None
    if cands:
        next(g for g in slate["games"] if g["id"] == cands[0]["id"])["hammer"] = cands[0]
    for pg in (state.get("pending") or {}).get("games", []):
        if any(g["id"] == pg["id"] for g in live):
            pg["hammer"] = cands[0] if cands and cands[0]["id"] == pg["id"] else None
    return slate["hammer"]


def attach_market(slate, state, lines, book="DraftKings"):
    """lines: {game_id: {"spread_home": float, "total": float|None}}. Adds the lines to the slate and PENDING
    (games not yet graded only), then re-picks the Hammer."""
    for g in slate["games"]:
        if g.get("result") or g["id"] not in lines:
            continue
        g["market"] = {"spread_home": lines[g["id"]]["spread_home"], "total": lines[g["id"]].get("total"), "book": book}
    for pg in (state.get("pending") or {}).get("games", []):
        if pg["id"] in lines:
            pg["market"] = -lines[pg["id"]]["spread_home"]
    return set_hammer(slate, state)


def pending_entry(p):
    return {"id": p["id"], "away": p["away"], "home": p["home"], "neutral": p.get("neutral", False),
            "margin": p["blend"]["margin"], "p_home": p["blend"]["p_home"], "total": p["blend"]["total"],
            "pick": p["pick"]["team"], "hammer": None,
            "sources": {s: p["sources"][s]["margin"] for s in SOURCES if p["sources"].get(s, {}).get("margin") is not None},
            "src_totals": {s: p["sources"][s]["total"] for s in SOURCES if p["sources"].get(s, {}).get("total") is not None},
            "market": (-p["market"]["spread_home"]) if (p.get("market") or {}).get("spread_home") is not None else None}


def build_slate(state, date, games, notes=None, generated_at=None):
    migrate(state)
    has = lambda g: any(g["sources"].get(s, {}).get("margin") is not None for s in SOURCES)
    waiting = [copy.deepcopy(g) for g in games if not has(g)]   # shown on the board, not graded
    preds = [predict_game(state, g) for g in games if has(g)]
    order = {"Strong": 0, "Mid": 1, "Weak": 2}
    preds.sort(key=lambda x: (order[x["tier"]], -x["pick"]["p"]))
    slate = {"date": date, "generated_at": generated_at or datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
             "notes": notes or [], "games": preds, "waiting": waiting, "hammer": None, "graded": False}
    keep = [x for x in ((state.get("pending") or {}).get("games") or []) if x["id"] not in {p["id"] for p in preds}]
    state["pending"] = {"date": date, "games": keep + [pending_entry(p) for p in preds]}
    set_hammer(slate, state)
    return slate


# ---- Grading ----
def result_label(pick_actual, proj):
    """Plain-language grade of a pick against our own number."""
    if pick_actual > 0:
        d = pick_actual - proj
        return f"Won by {pick_actual}, " + ("on our number" if abs(d) < 0.5 else f"{abs(d):.1f} {'better' if d > 0 else 'short of'} our number")
    return f"Lost by {-pick_actual}"


def grade(state, results):
    """results: {game_id: {"away_pts", "home_pts", "ot": bool, "absence": str|None} or {"void": True}}.
    Returns a report dict of what changed."""
    migrate(state)
    pend = state.get("pending")
    if not pend:
        return {"graded": [], "shifts": []}
    before = {}
    graded = []
    for gm in pend["games"]:
        r = results.get(gm["id"])
        if not r or r.get("void"):
            continue
        actual = r["home_pts"] - r["away_pts"]
        home_won = actual > 0
        pick_home = gm["pick"] == gm["home"]
        pick_actual = actual if pick_home else -actual
        proj = abs(gm["margin"])
        label = result_label(pick_actual, proj)
        tags = []
        if r.get("ot"):
            tags.append("OT")
        if r.get("absence"):
            tags.append("key absence")
        won = pick_actual > 0
        rec = state["records"]
        rec["su"]["w" if won else "l"] += 1
        hm_res = None
        hm = gm.get("hammer")
        if isinstance(hm, dict):
            cover = (actual + hm["spread_home"]) * (1 if hm["side"] == "home" else -1)
            hm_res = "push" if cover == 0 else ("win" if cover > 0 else "loss")
            rec["hammer"][{"win": "w", "loss": "l", "push": "p"}[hm_res]] += 1
        m = state["model"]
        m["mae"], m["n"] = _run(m["mae"], m["n"], abs(actual - gm["margin"]))
        m["brier_sum"] += (gm["p_home"] - (1 if home_won else 0)) ** 2
        m["correct"] += 1 if won else 0
        tot_actual = r["home_pts"] + r["away_pts"]
        if gm.get("total") is not None:
            m["tot_mae"], m["tot_n"] = _run(m["tot_mae"], m["tot_n"], abs(tot_actual - gm["total"]))
        lean_result = None
        for name in (gm["away"], gm["home"]):
            if name not in before:
                before[name] = (team_weights(state, name)[0], team_weights(state, name, "total")[0])
        src_err = {}
        if gm.get("market") is not None:
            e = abs(actual - gm["market"])
            src_err["market"] = round(e, 2)
            bm = state["bench"]["market"]
            bm["mae"], bm["n"] = _run(bm["mae"], bm["n"], e)
            for name in (gm["away"], gm["home"]):
                tb = state["teams"][name]["bench"]["market"]
                tb["mae"], tb["n"] = _run(tb["mae"], tb["n"], e)
        for s, sm in gm["sources"].items():
            e = abs(actual - sm)
            src_err[s] = round(e, 2)
            gs = state["sources"][s]
            gs["mae"], gs["n"] = _run(gs["mae"], gs["n"], e)
            for name in (gm["away"], gm["home"]):
                t = state["teams"][name]
                te = t["err"][s]
                te["mae"], te["n"] = _run(te["mae"], te["n"], e)
                conf = t.get("conf") or "Unknown"
                grp = state["groups"].setdefault(conf, {x: {"n": 0, "mae": 0.0} for x in SOURCES})
                grp[s]["mae"], grp[s]["n"] = _run(grp[s]["mae"], grp[s]["n"], e)
        src_tot_err = {}
        for s, stot in (gm.get("src_totals") or {}).items():
            e = abs(tot_actual - stot)
            src_tot_err[s] = round(e, 2)
            gs = state["sources_tot"][s]
            gs["mae"], gs["n"] = _run(gs["mae"], gs["n"], e)
            for name in (gm["away"], gm["home"]):
                t = state["teams"][name]
                te = t["terr"][s]
                te["mae"], te["n"] = _run(te["mae"], te["n"], e)
                grp = state["groups_tot"].setdefault(t.get("conf") or "Unknown", {x: {"n": 0, "mae": 0.0} for x in SOURCES})
                grp[s]["mae"], grp[s]["n"] = _run(grp[s]["mae"], grp[s]["n"], e)
        resid = actual - gm["margin"]
        for name, sign, p_win, team_won in ((gm["home"], 1, gm["p_home"], home_won),
                                             (gm["away"], -1, 1 - gm["p_home"], not home_won)):
            t = state["teams"][name]
            t["n"] += 1
            t["form"] = (t["form"] + [round(sign * resid, 2)])[-5:]
            t["losses"] = (t["losses"] + [(not team_won) and p_win >= TIER["bad_loss_p"]])[-2:]
            t["tot"]["avg"], t["tot"]["n"] = _run(t["tot"]["avg"], t["tot"]["n"], tot_actual)
        graded.append({"id": gm["id"], "away": gm["away"], "home": gm["home"], "score": [r["away_pts"], r["home_pts"]],
                       "pick": gm["pick"], "proj": round(proj, 1), "pick_actual": pick_actual, "label": label, "tags": tags,
                       "won": won, "hammer": hm_res, "src_err": src_err, "src_tot_err": src_tot_err,
                       "model_err": round(abs(actual - gm["margin"]), 2), "lean": lean_result})
    # model SD learns from the season's own misses, shrunk toward the prior
    m = state["model"]
    if m["n"]:
        sig_season = m["mae"] * math.sqrt(math.pi / 2)
        m["sigma"] = round((m["n"] * sig_season + SIGMA_SHRINK_N * SIGMA0) / (m["n"] + SIGMA_SHRINK_N), 2)
    shifts = []
    for name, (w0, wt0) in before.items():
        t = state["teams"][name]
        w1, basis, est = team_weights(state, name)
        t["weights"], t["basis"] = {s: round(w1[s], 3) for s in SOURCES}, basis
        moved = {s: round(w1[s] - w0[s], 3) for s in SOURCES if abs(w1[s] - w0[s]) >= SHIFT_REPORT}
        if moved:
            shifts.append({"team": name, "kind": "margin", "basis": basis, "moved": moved, "now": t["weights"]})
        wt1, tbasis, _ = team_weights(state, name, "total")
        t["tweights"], t["tbasis"] = {s: round(wt1[s], 3) for s in SOURCES}, tbasis
        moved = {s: round(wt1[s] - wt0[s], 3) for s in SOURCES if abs(wt1[s] - wt0[s]) >= SHIFT_REPORT}
        if moved:
            shifts.append({"team": name, "kind": "total", "basis": tbasis, "moved": moved, "now": t["tweights"]})
    day = {"date": pend["date"], "graded": len(graded),
           "w": sum(1 for x in graded if x["won"]), "l": sum(1 for x in graded if not x["won"]),
           "mae": round(statistics.mean([x["model_err"] for x in graded]), 2) if graded else None}
    state["history"].append(day)
    for s in shifts:
        state["log"].append({"date": pend["date"], "type": "weight_shift", **s})
    left = [gm for gm in pend["games"] if gm["id"] not in results]
    state["pending"] = {"date": pend["date"], "games": left} if left else None
    return {"graded": graded, "shifts": shifts, "day": day}


# ---- Display snapshot (what the board shows on the Sources tab) ----
def refresh_display(state):
    migrate(state)
    disp = {}
    for kind, (_, _, dk) in BOOKS.items():
        est = {s: state[dk][s]["mae"] for s in SOURCES if state[dk][s]["n"] >= GROUP_MIN}
        if est:
            fill = statistics.mean(est.values())
            d1, basis = cap_weights({s: 1 / max(est.get(s, fill), 0.5) ** 2 for s in SOURCES}), "D1"
        else:
            d1, basis = {s: 1 / len(SOURCES) for s in SOURCES}, "default"
        pre = "d1" if kind == "margin" else "d1_total"
        disp[pre + "_weights"], disp[pre + "_basis"] = {s: round(d1[s], 3) for s in SOURCES}, basis
    for name, t in state["teams"].items():
        w, b, _ = team_weights(state, name)
        t["weights"], t["basis"] = {s: round(w[s], 3) for s in SOURCES}, b
        wt, bt, _ = team_weights(state, name, "total")
        t["tweights"], t["tbasis"] = {s: round(wt[s], 3) for s in SOURCES}, bt
    state["display"] = disp
    return state


# ---- Abridged report ----
def pct(p):
    if 0.995 <= p < 1:
        return ">99%"
    return f"{round(p*100)}%"


def abridged(slate, weekday_label, records=None):
    rec = records or {}
    L = [f"THE BALLGORITHM · NCAAM · {weekday_label}", "", "NOTES"]
    L += [f"- {n}" for n in slate["notes"]] or ["- none"]
    done = [g for g in slate["games"] if g.get("result")]
    if done:
        L += ["", "RESULTS"]
        for g in done:
            r = g["result"]
            L.append(f"{g['pick']['team']} −{g['pick']['margin']:.1f}: {r.get('label') or ('Won' if r.get('won') else 'Lost')} "
                     f"(final {g['away']} {r['score'][0]}, {g['home']} {r['score'][1]})")
    hr = rec.get("hammer", {"w": 0, "l": 0, "p": 0})
    L += ["", f"HAMMER OF THE DAY vs DraftKings ({hr['w']}-{hr['l']}" + (f"-{hr['p']}" if hr.get('p') else "") + ")"]
    hm = slate.get("hammer")
    if hm:
        g = next(x for x in slate["games"] if x["id"] == hm["id"])
        other = g["away"] if hm["team"] == g["home"] else g["home"]
        L.append(f"{hm['team']} {hm['spread']:+.1f} vs {other} — our number {g['pick']['team']} −{g['pick']['margin']:.1f}, "
                 f"{hm['agree']} of {hm['n']} sources on this side, {round(hm['cover_p']*100)}% to cover")
    else:
        L.append("Waiting on DraftKings lines.")
    L += ["", "WIN PROBABILITIES (our margin ± SD · N = neutral site)"]
    for tier in ("Strong", "Mid", "Weak"):
        gs = [g for g in slate["games"] if g["tier"] == tier]
        L.append(f"{tier}" + (" — none" if not gs else ""))
        for g in gs:
            pk = g["pick"]
            site = " (N)" if g.get("neutral") else ""
            tot = f" · total {g['blend']['total']:.0f}" if g["blend"]["total"] is not None else ""
            L.append(f"{pk['team']} {pct(pk['p'])} · −{pk['margin']:.1f} ±{g['blend']['sd']:.1f} vs {pk['dog']}{site}{tot}")
    for w in slate.get("waiting", []):
        L.append(f"{w['away']} vs {w['home']} — waiting on source numbers")
    return "\n".join(L)
