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
SRC_NAME = {"kenpom": "KenPom", "torvik": "Torvik", "hasla": "Haslametrics", "espn": "ESPN"}


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


def torvik_lines(txt):
    """'Florida -2.8,' then the next '82-79 (60%)'. Returns {favorite: (spread, fav_pts, dog_pts, pct)}."""
    out = {}
    marks = []
    for m in re.finditer(r"(-\d+(?:\.\d)?),", txt):
        head = txt[:m.start()].rsplit("\n", 1)[-1]
        fav = N.canon_tail(head)
        if fav:
            marks.append((m.end(), fav, abs(float(m.group(1)))))
    for i, (pos, fav, spread) in enumerate(marks):
        stop = marks[i + 1][0] if i + 1 < len(marks) else len(txt)
        sm = re.compile(r"(\d{2,3})-(\d{2,3}) \((\d{1,3})%\)").search(txt, pos, stop)
        if sm and fav not in out:
            out[fav] = (spread, int(sm.group(1)), int(sm.group(2)), int(sm.group(3)) / 100)
    return out


def fanmatch_lines(txt):
    """'Florida 84-77 (74%)'. Returns {winner: (win_pts, lose_pts, pct)}."""
    out = {}
    for m in re.finditer(r"(\d{2,3})-(\d{2,3})\s*\((\d{1,3}(?:\.\d+)?)%\)", txt):
        head = txt[:m.start()].rsplit("\n", 1)[-1]
        w = N.canon_tail(head)
        if w and w not in out:
            out[w] = (int(m.group(1)), int(m.group(2)), float(m.group(3)) / 100)
    return out


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


def espn_p_home(pr):
    try:
        return float(pr["homeTeam"]["gameProjection"]) / 100
    except (KeyError, TypeError, ValueError):
        return None


# ---------------- grading ----------------
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
        sl["abridged"] = E.abridged(sl, datetime.date.fromisoformat(ds).strftime("%a, %b %-d"), state["records"])
        jsave(path, sl)
    return rep


# ---------------- build ----------------
def build_today(state, now):
    today = now.date()
    status = jload(raw(today, "status.json"), {}) or {}
    unmatched = set()
    sched = espn_games(jload(raw(today, "espn_scoreboard.json")), unmatched)
    preds = jload(raw(today, "espn_predictor.json"), {}) or {}
    tv = torvik_lines(tload(raw(today, "torvik.txt")))
    fm = fanmatch_lines(tload(raw(today, "fanmatch.txt")))
    ha = hasla_scores(tload(raw(today, "hasla.txt")))

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
        fav = a if a in tv else h if h in tv else None
        if fav and fav in tv:
            sp, fp, dp, pc = tv[fav]
            src["torvik"] = {"margin": sp if fav == h else -sp, "total": fp + dp,
                             "note": f"Published line: {fav} −{sp:.1f}, {fp}-{dp}, {round(pc * 100)}%."}
        win = a if a in fm else h if h in fm else None
        if win:
            wp, lp, pc = fm[win]
            src["kenpom"] = {"margin": (wp - lp) if win == h else -(wp - lp), "total": wp + lp,
                             "note": f"FanMatch: {win} {wp}-{lp} ({pc * 100:.0f}%)."}
        if a in ha and h in ha:
            src["hasla"] = {"margin": round(ha[h] - ha[a], 2), "total": round(ha[h] + ha[a], 1),
                            "note": f"Published projection: {a} {ha[a]:.1f}, {h} {ha[h]:.1f}."}
        ph = espn_p_home(preds.get(g["espn_id"]))
        if ph is not None and 0.001 < ph < 0.999:
            src["espn"] = {"margin": round(E.margin_from_winprob(ph, E.SIGMA0), 2), "derived": True,
                           "note": f"BPI matchup predictor: {h} {ph * 100:.1f}% to win, converted to points at ±{E.SIGMA0}."}
        for k in E.SOURCES:
            src.setdefault(k, {"margin": None, "note": "No number today."})
        rec = {"id": gid, "away": a, "home": h, "away_conf": N.TEAMS.get(a), "home_conf": N.TEAMS.get(h),
               "short": {}, "neutral": g["neutral"], "site": ("Neutral site" + (f", {g['venue']}" if g.get("venue") else "")) if g["neutral"] else f"at {h}",
               "tip": tlabel, "tip_sort": tsort, "tv": g.get("tv"), "sources": src, "market": g.get("market"), "injuries": []}
        (games if any(v.get("margin") is not None for v in src.values()) else waiting).append(rec)

    # coverage + collection problems go in the notes, so a bad day is visible on the board
    cov = {k: sum(1 for x in games if x["sources"][k].get("margin") is not None) for k in E.SOURCES}
    n_mkt = sum(1 for x in games if (x.get("market") or {}).get("spread_home") is not None)
    notes = [f"{len(games) + len(locked)} D1 games on the board"
             + (f" ({len(locked)} locked at tip)" if locked else "") + ". Coverage: "
             + ", ".join(f"{SRC_NAME[k]} {cov[k]}" for k in E.SOURCES) + f", DraftKings lines {n_mkt}."]
    broke = [SRC_NAME.get(k, k) for k, v in status.items() if isinstance(v, dict) and not v.get("ok")
             and k in ("espn", "torvik", "haslametrics", "kenpom")]
    if broke:
        notes.append("Didn't load this run: " + ", ".join(sorted(set(b.replace("haslametrics", "Haslametrics") for b in broke)))
                     + ". Those games use the remaining sources.")
    if unmatched:
        notes.append(f"{len(unmatched)} ESPN games skipped because one team isn't Division I or its name isn't recognized yet.")

    if not games and not locked and not waiting:
        print("No games could be built for", today, "- leaving the board's last slate in place.", flush=True)
        return {"games": [], "waiting": [], "hammer": None}
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
    slate["abridged"] = E.abridged(slate, today.strftime("%a, %b %-d"), state["records"])
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
