"""Pull ESPN D1 scoreboards for full seasons, compacted to one row per finished game."""
import datetime as dt, json, sys, os, concurrent.futures as cf, requests, time
OUT = sys.argv[1]
ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
def day(d):
    for t in range(3):
        try:
            r = requests.get(ESPN, params={"dates": d.strftime("%Y%m%d"), "groups": "50", "limit": "500"}, timeout=30)
            r.raise_for_status(); sb = r.json(); break
        except Exception as e:
            time.sleep(2); sb = None
    rows = []
    for ev in (sb or {}).get("events", []):
        st = ev.get("status", {}).get("type", {})
        if not st.get("completed"): continue
        comp = ev["competitions"][0]
        cs = {c["homeAway"]: c for c in comp["competitors"]}
        try:
            rows.append({"id": ev["id"], "date": d.isoformat(), "neutral": bool(comp.get("neutralSite")),
                         "ot": (ev["status"].get("period") or 2) > 2,
                         "home": cs["home"]["team"]["id"], "home_name": cs["home"]["team"].get("location") or cs["home"]["team"].get("displayName"),
                         "away": cs["away"]["team"]["id"], "away_name": cs["away"]["team"].get("location") or cs["away"]["team"].get("displayName"),
                         "hs": int(cs["home"]["score"]), "as": int(cs["away"]["score"]),
                         "conf": bool(comp.get("conferenceCompetition"))})
        except Exception:
            pass
    return rows
for season, (a, b) in {"2024-25": ("2024-11-04", "2025-04-07"), "2025-26": ("2025-11-03", "2026-04-06")}.items():
    d0, d1 = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
    days = [d0 + dt.timedelta(i) for i in range((d1 - d0).days + 1)]
    with cf.ThreadPoolExecutor(8) as ex:
        allrows = [r for rows in ex.map(day, days) for r in rows]
    seen = {}; out = []
    for r in allrows:
        if r["id"] not in seen: seen[r["id"]] = 1; out.append(r)
    json.dump(out, open(os.path.join(OUT, f"games-{season}.json"), "w"))
    print(season, len(days), "days", len(out), "games")
