"""The Ballgorithm daily run: grade whatever has finished, then build (or refresh) today's slate.

Reads   data/raw/<date>/   (written by collect/collect.py)
Writes  site/data/state.json, site/data/slate-<date>.json, site/data/index.json

Safe to run more than once a day: games that have already tipped keep the numbers they had, everything
else is re-blended with the newest sources and lines, and the Hammer is re-picked only among games that
haven't tipped (a Hammer that has tipped stays put).
"""
import datetime, json, os, re, statistics, sys, zoneinfo
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine as E
import names as N

ET = zoneinfo.ZoneInfo("America/New_York")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
SITE = os.path.join(ROOT, "site", "data")
SRC_NAME = {"torvik": "Torvik", "hasla": "Haslametrics", "espn": "ESPN", "dratings": "DRatings", "massey": "Massey", "cbbd": "CBBD"}
HCA_FALLBACK = 3.0          # measured from published lines on Nov 2; re-measured daily when enough lines exist
CBBD_MIN_GAMES = 5          # CBBD's ratings come only from this season's games, so wait for a few per team


def now_et():
    v = os.environ.get("BALLGO_NOW")
    return datetime.datetime.fromisoformat(v).astimezone(ET) if v else datetime.datetime.now(ET)


def jload(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def tload(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def jsave(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def raw(d, name):
    return os.path.join(RAW, d.isoformat(), name)


# ---------------- ESPN ----------------
def espn_team(c):
    t = c.get("team", {})
    if t.get("conferenceId") is None:   # ESPN gives every D1 team a conference; non-D1 opponents ("Kentucky Wesleyan") have none
        return None
    for key in ("location", "shortDisplayName", "displayName", "name"):
        x = N.to_canon(t.get(key) or "")
        if x:
            return x
    return N.to_canon(t.get("displayName", ""), allow_prefix=True)


def parse_odds(o, home_abbr, away_abbr):
    det = (o.get("details") or "").strip()
    sh = None
    if det.upper() in ("EVEN", "PK", "PICK", "PICK'EM"):
        sh = 0.0
    else:
        m = re.match(r"(.+?)\s+(-?\d+(?:\.\d+)?)$", det)
        if m:
            ab, x = m.group(1).strip(), abs(float(m.group(2)))
            sh = -x if ab == home_abbr else x if ab == away_abbr else None
    if sh is None and o.get("spread") is not None:
        sh = float(o["spread"])
    if sh is None:
        return None
    return {"spread_home": sh, "total": o.get("overUnder"), "book": (o.get("provider") or {}).get("name") or "DraftKings"}


def espn_games(sb, unmatched):
    out = []
    for ev in (sb or {}).get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        cs = {c.get("homeAway"): c for c in comp.get("competitors", [])}
        if "home" not in cs or "away" not in cs:
            continue
        h, a = espn_team(cs["home"]), espn_team(cs["away"])
        if not h or not a:   # a non-D1 opponent, or a name we haven't learned yet
            for side, c in (("home", h), ("away", a)):
                if not c:
                    unmatched.add(cs[side].get("team", {}).get("displayName", "?"))
            continue
        st = ev.get("status", {}).get("type", {})
        done = bool(st.get("completed"))
        tv = [n for b in comp.get("broadcasts", []) for n in b.get("names", [])]
        g = {"espn_id": ev["id"], "utc": ev.get("date"), "home": h, "away": a, "neutral": bool(comp.get("neutralSite")),
             "state": st.get("state"), "completed": done, "ot": "OT" in (st.get("detail") or st.get("shortDetail") or ""),
             "home_pts": int(cs["home"].get("score") or 0) if done else None,
             "away_pts": int(cs["away"].get("score") or 0) if done else None,
             "tv": ", ".join(tv[:2]) or None,
             "venue": ((comp.get("venue") or {}).get("address") or {}).get("city")}
        odds = comp.get("odds") or []
        if odds:
            g["market"] = parse_odds(odds[0], cs["home"]["team"].get("abbreviation"), cs["away"]["team"].get("abbreviation"))
        out.append(g)
    return out


def tip(g, day):
    t = datetime.datetime.fromisoformat(g["utc"].replace("Z", "+00:00")).astimezone(ET)
    mins = t.hour * 60 + t.minute + (1440 if t.date() > day else 0)
    label = t.strftime("%-I:%M %p").replace(":00 ", " ") + " ET" + (" (next day)" if t.date() > day else "")
    return mins, label


# ---------------- text sources (order-independent: each team plays once a day) ----------------
SCORE = re.compile(r"(\d{2,3})-(\d{2,3})(?:\s*\((\d{1,3}(?:\.\d+)?)%\))?")


def hasla_scores(txt):
    """'Army 336 51.86' entries after "EXPECTED OUTCOMES". Returns {team: projected points}."""
    i = txt.upper().find("EXPECTED OUTCOMES")
    if i < 0:
        return {}
    out = {}
    for m in re.finditer(r"([A-Z][A-Za-z.&'()\- ]*?[A-Za-z.)])\s+(\d{1,3})\s+(\d{2,3}\.\d\d)\b", txt[i:]):
        t = N.to_canon(m.group(1)) or N.canon_tail(m.group(1))
        if t and t not in out:
            out[t] = float(m.group(3))
    return out


def torvik_cbbdata(rows_by_game):
    """{espn_id: (home_pts, away_pts, home_win_pct)} from cbbdata's T-Rank predictions (one row per team)."""
    out = {}
    for x in rows_by_game or []:
        rows = x.get("rows")
        if x.get("status") != 200 or not isinstance(rows, list) or len(rows) < 2:
            continue
        by = {}
        for r in rows:
            t = N.to_canon(str(r.get("team", ""))) or N.to_canon(str(r.get("team", "")), True)
            if t:
                by[t] = r
        h, a = by.get(x["home"]), by.get(x["away"])
        if h is None or a is None:   # fall back to row order: team (home) first, then opponent
            h, a = rows[0], rows[1]
        try:
            hp, ap = float(h["pts"]), float(a["pts"])
            wp = h.get("win_per")
            wp = float(wp) / (100 if wp is not None and float(wp) > 1 else 1) if wp is not None else None
            out[x["espn_id"]] = (hp, ap, wp)
        except (KeyError, TypeError, ValueError):
            continue
    return out


def dratings_games(pages, day):
    """{frozenset(teams): {away, home, away_pts, home_pts, away_win}} from DRatings' "Upcoming Games" tables.
    Each page is one day (page 1 = today); only games tipping on `day` (Eastern) are kept. Away is listed first."""
    out = {}
    for h in pages:
        i, j = h.find("Upcoming Games"), h.find("Completed Games")
        if i < 0:
            continue
        sec = h[i:j if j > i else len(h)]
        for row in re.findall(r"<tr>(.*?)</tr>", sec, re.S):
            dt = re.search(r'datetime="([^"]+)"', row)
            teams = re.findall(r'<a href="/teams/[^"]+">([^<]+)</a>', row)
            win = re.search(r'([\d.]+)%</span>\s*<br\s*/?>\s*<span[^>]*>([\d.]+)%', row)
            pts = re.search(r'<td class="table-division">\s*([\d.]+)\s*<br\s*/?>\s*([\d.]+)\s*</td>', row)
            if not (dt and len(teams) >= 2 and pts):
                continue
            when = datetime.datetime.fromisoformat(dt.group(1).replace("Z", "+00:00")).astimezone(ET)
            if when.date() != day:
                continue
            a, hm = (N.to_canon(html_unescape(t)) or N.to_canon(html_unescape(t), True) for t in teams[:2])
            if not a or not hm or a == hm:
                continue
            out[frozenset((a, hm))] = {"away": a, "home": hm, "away_pts": float(pts.group(1)), "home_pts": float(pts.group(2)),
                                       "away_win": float(win.group(1)) / 100 if win else None}
    return out


def massey_games(txt):
    """{frozenset(teams): {away, home, margin_home, total, home_win}} from Massey's rendered games table.
    Each game is a tab-separated block: date+time | away / @ home | standing | score | predicted score | win % | MOV | total."""
    out = {}
    for blk in re.split(r"\n(?=(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d\d\.\d\d\n)", txt or ""):
        cells = blk.split("\t")
        if len(cells) < 8 or "\n@ " not in "\n" + cells[1]:
            continue
        nm = [x.strip() for x in cells[1].strip().split("\n")]
        if len(nm) != 2 or not nm[1].startswith("@ "):
            continue
        a, h = N.to_canon_abbr(nm[0]), N.to_canon_abbr(nm[1][2:].strip())
        if not a or not h or a == h:
            continue
        num = lambda c: [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", c)]
        pred, pw, mov, tot = num(cells[4]), num(cells[5]), num(cells[6]), num(cells[7])
        if len(pred) != 2:
            continue
        diff = pred[1] - pred[0]
        home_fav = (pw[1] > pw[0]) if len(pw) == 2 and pw[0] != pw[1] else diff >= 0
        # MOV is the favorite's median margin; if a market spread ever shares the cell, keep the one nearer the predicted score
        m = min((abs(x) for x in mov), key=lambda x: abs(x - abs(diff)), default=abs(diff))
        t = min(tot, key=lambda x: abs(x - sum(pred)), default=sum(pred))
        out[frozenset((a, h))] = {"away": a, "home": h, "margin_home": m if home_fav else -m, "total": t,
                                  "home_win": pw[1] / 100 if len(pw) == 2 else None}
    return out


def html_unescape(s):
    import html
    return html.unescape(s).strip()


def cbbd_ratings(rat, stats):
    """{team: (net_rating, pace or None, games)} from CBBD."""
    pace, games = {}, {}
    for r in stats or []:
        t = N.to_canon(str(r.get("team", ""))) or N.to_canon(str(r.get("team", "")), True)
        if t:
            pace[t] = r.get("pace")
            games[t] = r.get("games") or 0
    out = {}
    for r in rat or []:
        t = N.to_canon(str(r.get("team", ""))) or N.to_canon(str(r.get("team", "")), True)
        net = r.get("netRating", r.get("net_rating"))
        if t and net is not None:
            out[t] = (float(net), pace.get(t), games.get(t, 0))
    return out


def measure_hca(rows):
    """Home-court points implied by today's published projections vs CBBD's neutral numbers (needs both kinds of site)."""
    import numpy as np
    pts = [(r["neutral_m"], 0.0 if r["neutral"] else 1.0, r["pub_m"]) for r in rows if r.get("pub_m") is not None]
    if len(pts) < 20 or sum(1 for p in pts if p[1] == 1.0) < 10 or sum(1 for p in pts if p[1] == 0.0) < 3:
        return HCA_FALLBACK, "fallback"
    X = np.array([[1.0, p[0], p[1]] for p in pts]); y = np.array([p[2] for p in pts])
    h = float(np.linalg.lstsq(X, y, rcond=None)[0][2])
    return (round(h, 1), "measured") if 0.5 <= h <= 6.0 else (HCA_FALLBACK, "fallback")


def espn_p_home(pr):
    try:
        return float(pr["homeTeam"]["gameProjection"]) / 100
    except (KeyError, TypeError, ValueError):
        return None


# ---------------- grading ----------------
def backfill_dawg(state):
    """One time (Nov 10): rebuild every team's season "THAT DAWG" book from the results already written onto
    the slate files, so the list starts from opening night instead of from the day the feature shipped.
    Runs before grading, so only games graded on earlier runs are counted; later games go through E.grade."""
    if state.get("dawg_since"):
        return 0
    for t in state["teams"].values():
        t["dawg"] = E.new_dawg()
    n = 0
    for ds in sorted((jload(os.path.join(SITE, "index.json"), {}) or {}).get("dates", [])):
        for g in (jload(os.path.join(SITE, f"slate-{ds}.json"), {}) or {}).get("games", []):
            r = g.get("result")
            if not r or g["home"] not in state["teams"] or g["away"] not in state["teams"]:
                continue
            actual = r["score"][1] - r["score"][0]
            resid = actual - g["blend"]["margin"]
            E.dawg_add(state["teams"][g["home"]], resid, actual, g["away"], ds)
            E.dawg_add(state["teams"][g["away"]], -resid, -actual, g["home"], ds)
            n += 1
    state["dawg_since"] = datetime.date.today().isoformat()
    return n


def grade_pending(state, today):
    pend = state.get("pending")
    if not pend:
        return None
    start = datetime.date.fromisoformat(pend["date"])
    finals = {}
    d = start
    while d <= today:
        for name in ("espn_final.json", "espn_scoreboard.json"):
            for g in espn_games(jload(raw(d, name)), set()):
                if g["completed"]:
                    finals.setdefault(frozenset((g["away"], g["home"])), g)
        d += datetime.timedelta(days=1)
    results = {}
    for pg in pend["games"]:
        f = finals.get(frozenset((pg["away"], pg["home"])))
        if f:
            pts = {f["away"]: f["away_pts"], f["home"]: f["home_pts"]}
            results[pg["id"]] = {"away_pts": pts[pg["away"]], "home_pts": pts[pg["home"]], "ot": f["ot"]}
    if not results:
        return {"graded": [], "shifts": [], "left": len(pend["games"])}
    rep = E.grade(state, results)
    rep["left"] = len((state.get("pending") or {}).get("games") or [])
    # write the results onto each slate file
    by_date = {}
    for r in rep["graded"]:
        by_date.setdefault(r["id"][:10], []).append(r)
    for ds, rs in by_date.items():
        path = os.path.join(SITE, f"slate-{ds}.json")
        sl = jload(path)
        if not sl:
            continue
        G = {g["id"]: g for g in sl["games"]}
        for r in rs:
            g = G.get(r["id"])
            if not g:
                continue
            g["result"] = {"label": r["label"], "tags": r["tags"], "score": r["score"], "model_err": r["model_err"],
                           "src_err": r["src_err"], "src_tot_err": r.get("src_tot_err", {}), "won": r["won"], "hammer": r["hammer"],
                           "total_err": round(abs(sum(r["score"]) - g["blend"]["total"]), 1) if g["blend"].get("total") is not None else None}
        done = [g for g in sl["games"] if g.get("result")]
        w = sum(g["result"]["won"] for g in done)
        mae = statistics.mean(g["result"]["model_err"] for g in done)
        hm = next((g for g in done if g.get("hammer")), None)
        left = [g for g in sl["games"] if not g.get("result")]
        line = (f"GRADED: {w}-{len(done) - w} picking winners ({w / len(done):.0%}), average margin miss {mae:.1f}."
                + (f" Hammer {hm['hammer']['team']} {hm['hammer']['spread']:+.1f}: {hm['result']['hammer']}." if hm and hm["result"].get("hammer") else "")
                + (f" {len(left)} still waiting on a final." if left else ""))
        sl["notes"] = [n for n in sl["notes"] if not n.startswith("GRADED")]
        sl["notes"].insert(0, line)
        sl["graded"] = not left
        sl.pop("abridged", None)
        jsave(path, sl)
    return rep


# ---------------- build ----------------
def build_today(state, now):
    today = now.date()
    status = jload(raw(today, "status.json"), {}) or {}
    unmatched = set()
    sched = espn_games(jload(raw(today, "espn_scoreboard.json")), unmatched)
    preds = jload(raw(today, "espn_predictor.json"), {}) or {}
    tv = torvik_cbbdata(jload(raw(today, "torvik_cbbdata.json"), []))
    ha = hasla_scores(tload(raw(today, "hasla.txt")))
    cb = cbbd_ratings(jload(raw(today, "cbbd_ratings.json"), []), jload(raw(today, "cbbd_team_stats.json"), []))
    dr = dratings_games([tload(raw(today, f"dratings_{i}.html")) or "" for i in range(1, 6)], today)
    ma = massey_games(tload(raw(today, "massey.txt")))
    paces = [v[1] for v in cb.values() if v[1]]
    avg_pace = statistics.mean(paces) if paces else 68.0

    path = os.path.join(SITE, f"slate-{today.isoformat()}.json")
    old = jload(path)
    now_m = now.hour * 60 + now.minute
    locked = [g for g in (old or {}).get("games", []) if g.get("result") or g.get("tip_sort", 0) <= now_m + 2]
    locked_ids = {g["id"] for g in locked}

    games, waiting = [], []
    for g in sched:
        if g["state"] != "pre":
            continue
        gid = f"{today.isoformat()}-" + re.sub(r"[^a-z0-9]+", "-", f"{g['away']}-{g['home']}".lower()).strip("-")
        tsort, tlabel = tip(g, today)
        if gid in locked_ids or tsort <= now_m + 2:
            continue
        a, h = g["away"], g["home"]
        src = {}
        if g["espn_id"] in tv:
            hp, ap, wp = tv[g["espn_id"]]
            src["torvik"] = {"margin": round(hp - ap, 2), "total": round(hp + ap, 1),
                             "note": f"T-Rank prediction (via cbbdata): {h} {hp:.1f}, {a} {ap:.1f}" + (f", {h} {wp * 100:.0f}% to win." if wp is not None else ".")}
        if a in ha and h in ha:
            src["hasla"] = {"margin": round(ha[h] - ha[a], 2), "total": round(ha[h] + ha[a], 1),
                            "note": f"Published projection: {a} {ha[a]:.1f}, {h} {ha[h]:.1f}."}
        key = frozenset((a, h))
        if key in dr:
            d = dr[key]
            hp, ap = (d["home_pts"], d["away_pts"]) if d["home"] == h else (d["away_pts"], d["home_pts"])
            src["dratings"] = {"margin": round(hp - ap, 2), "total": round(hp + ap, 1),
                               "note": f"Published projection: {a} {ap:.1f}, {h} {hp:.1f}."}
        if key in ma:
            m = ma[key]
            mh = m["margin_home"] if m["home"] == h else -m["margin_home"]
            fav = h if mh >= 0 else a
            src["massey"] = {"margin": round(mh, 2), "total": round(m["total"], 1),
                             "note": f"Published median margin: {fav} by {abs(mh):.1f}, total {m['total']:.1f}."}
        ph = espn_p_home(preds.get(g["espn_id"]))
        if ph is not None and 0.001 < ph < 0.999:
            src["espn"] = {"margin": round(E.margin_from_winprob(ph, E.SIGMA0), 2), "derived": True,
                           "note": f"BPI matchup predictor: {h} {ph * 100:.1f}% to win, converted to points at ±{E.SIGMA0}."}
        if h in cb and a in cb and min(cb[h][2], cb[a][2]) >= CBBD_MIN_GAMES:
            (nh, ph, _), (na, pa, _) = cb[h], cb[a]
            poss = (ph * pa / avg_pace) if ph and pa else avg_pace
            src["cbbd"] = {"neutral_m": (nh - na) * poss / 100, "poss": poss, "derived": True}
        elif h in cb and a in cb:
            src["cbbd"] = {"margin": None, "note": f"CBBD ratings wait until both teams have {CBBD_MIN_GAMES} games."}
        for k in E.SOURCES:
            src.setdefault(k, {"margin": None, "note": "No number today."})
        rec = {"id": gid, "away": a, "home": h, "away_conf": N.TEAMS.get(a), "home_conf": N.TEAMS.get(h),
               "short": {}, "neutral": g["neutral"], "site": ("Neutral site" + (f", {g['venue']}" if g.get("venue") else "")) if g["neutral"] else f"at {h}",
               "tip": tlabel, "tip_sort": tsort, "tv": g.get("tv"), "sources": src, "market": g.get("market"), "injuries": []}
        (games if any(v.get("margin") is not None for v in src.values()) else waiting).append(rec)

    # CBBD is a rating, so its margin needs home court: measured from today's published projections when possible
    rows = []
    for x in games + waiting:
        c = x["sources"].get("cbbd", {})
        if "neutral_m" in c:
            pub = [x["sources"][k]["margin"] for k in ("torvik", "hasla", "dratings", "massey") if x["sources"][k].get("margin") is not None]
            rows.append({"neutral_m": c["neutral_m"], "neutral": x["neutral"], "pub_m": statistics.mean(pub) if pub else None})
    hca, hca_how = measure_hca(rows)
    for x in games + waiting:
        c = x["sources"].get("cbbd", {})
        if "neutral_m" in c:
            m = c["neutral_m"] + (0 if x["neutral"] else hca)
            x["sources"]["cbbd"] = {"margin": round(m, 2), "derived": True,
                                    "note": f"From CBBD ratings: net rating gap at {c['poss']:.1f} possessions" + ("." if x["neutral"] else f", +{hca} home court ({hca_how}).")}
    moved = [x for x in waiting if x["sources"].get("cbbd", {}).get("margin") is not None]
    for x in moved:
        waiting.remove(x); games.append(x)

    # coverage + collection problems go in the notes, so a bad day is visible on the board
    cov = {k: sum(1 for x in games if x["sources"][k].get("margin") is not None) for k in E.SOURCES}
    n_mkt = sum(1 for x in games if (x.get("market") or {}).get("spread_home") is not None)
    notes = [f"{len(games) + len(locked)} D1 games on the board"
             + (f" ({len(locked)} locked at tip)" if locked else "") + ". Coverage: "
             + ", ".join(f"{SRC_NAME[k]} {cov[k]}" for k in E.SOURCES) + f", DraftKings lines {n_mkt}."]
    dormant = "blank" in str((status.get("torvik") or {}).get("error", ""))
    broke = [SRC_NAME.get(k, k) for k, v in status.items() if isinstance(v, dict) and not v.get("ok")
             and k in ("espn", "torvik", "haslametrics", "dratings", "massey", "cbbd") and not (k == "torvik" and dormant)]
    if broke:
        notes.append("Didn't load this run: " + ", ".join(sorted(set(b.replace("haslametrics", "Haslametrics") for b in broke)))
                     + ". Those games use the remaining sources.")
    if dormant:
        notes.append("Torvik is dormant: cbbdata has no 2026-27 T-Rank numbers yet. It rejoins automatically when they appear.")
    if cov.get("cbbd", 0) == 0:
        notes.append(f"CBBD joins once both teams in a game have played {CBBD_MIN_GAMES} games and its adjusted ratings are published.")
    if unmatched:
        notes.append(f"{len(unmatched)} ESPN games skipped because one team isn't Division I or its name isn't recognized yet.")

    if not games and not locked and not waiting:
        print("No games could be built for", today, "- leaving the board's last slate in place.", flush=True)
        return {"games": [], "waiting": [], "hammer": None}
    pend = state.get("pending") or {}
    if pend.get("games"):   # today's untipped games are rebuilt below; anything of today's that no longer qualifies drops out
        pend["games"] = [x for x in pend["games"] if not x["id"].startswith(today.isoformat()) or x["id"] in locked_ids]
    slate = E.build_slate(state, today.isoformat(), games, notes, generated_at=now.isoformat(timespec="seconds"))
    for p in slate["games"]:
        p["tip_sort"] = next(x["tip_sort"] for x in games if x["id"] == p["id"])
    slate["waiting"] = waiting
    slate["games"] = locked + slate["games"]
    old_hm = (old or {}).get("hammer")
    if old_hm and old_hm["id"] in locked_ids:          # the Hammer already tipped: it stays
        slate["hammer"] = old_hm
        for pg in (state.get("pending") or {}).get("games", []):
            if pg["id"] in {x["id"] for x in games}:
                pg["hammer"] = None
    else:
        E.set_hammer(slate, state, eligible={x["id"] for x in games})
    slate["unmatched"] = sorted(unmatched)
    jsave(path, slate)
    idx = jload(os.path.join(SITE, "index.json"), {"dates": []})
    if today.isoformat() not in idx["dates"]:
        idx["dates"].append(today.isoformat())
    idx["dates"].sort()
    idx["latest"] = today.isoformat()
    jsave(os.path.join(SITE, "index.json"), idx)
    return slate


def main():
    now = now_et()
    state = jload(os.path.join(SITE, "state.json")) or E.new_state("2026-27")
    E.migrate(state)
    backfill_dawg(state)
    rep = grade_pending(state, now.date())
    slate = build_today(state, now)
    state["updated"] = now.isoformat(timespec="seconds")
    E.refresh_display(state)
    jsave(os.path.join(SITE, "state.json"), state)
    g = (rep or {}).get("graded", [])
    print(f"graded {len(g)} ({sum(x['won'] for x in g)} right), still pending {((rep or {}).get('left'))}; "
          f"today {len(slate['games'])} games, waiting {len(slate['waiting'])}, hammer "
          f"{(slate.get('hammer') or {}).get('team')} {(slate.get('hammer') or {}).get('spread')}")


if __name__ == "__main__":
    main()
