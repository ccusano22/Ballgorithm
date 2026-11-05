"""Daily collection, run by GitHub Actions. Saves raw source data under data/raw/<date>/ and a status.json that
says what worked. Each source is independent: one failing never stops the others.

  ESPN       public JSON API: schedule, DraftKings spread/total, final scores, BPI matchup predictor
  Torvik     T-Rank game predictions through the cbbdata API (barttorvik.com itself blocks GitHub's servers)
  Haslametrics  ratings page, "This week's expected outcomes" (projected score per team)
  CBBD       CollegeBasketballData.com adjusted efficiency ratings and team pace
"""
import datetime, json, os, sys, time, traceback, zoneinfo
import requests

ET = zoneinfo.ZoneInfo("America/New_York")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
      "Version/17.0 Safari/605.1.15")
ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball"


def day_dir(d):
    p = os.path.join(ROOT, "data", "raw", d.isoformat())
    os.makedirs(p, exist_ok=True)
    return p


def save(d, name, obj):
    path = os.path.join(day_dir(d), name)
    with open(path, "w", encoding="utf-8") as f:
        if isinstance(obj, str):
            f.write(obj)
        else:
            json.dump(obj, f, ensure_ascii=False, indent=1)
    return path


def espn_scoreboard(d):
    r = requests.get(f"{ESPN}/scoreboard", params={"dates": d.strftime("%Y%m%d"), "groups": "50", "limit": "500"},
                     headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    return r.json()


def espn_predictors(sb):
    """BPI matchup predictor for every game that hasn't started."""
    out = {}
    for ev in sb.get("events", []):
        if ev.get("status", {}).get("type", {}).get("state") != "pre":
            continue
        try:
            r = requests.get(f"{ESPN}/summary", params={"event": ev["id"]}, headers={"User-Agent": UA}, timeout=30)
            r.raise_for_status()
            pr = r.json().get("predictor")
            if pr:
                out[ev["id"]] = pr
        except Exception as e:  # keep going; one bad game shouldn't lose the rest
            out[ev["id"]] = {"error": str(e)[:200]}
        time.sleep(0.25)
    return out


def page_text(url, login=None, wait_ms=5000):
    """Render a page in headless Chromium and return its visible text and HTML."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(user_agent=UA, viewport={"width": 1400, "height": 2000})
        if login:
            login(pg)
        pg.goto(url, wait_until="domcontentloaded", timeout=90000)
        try:
            pg.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass
        pg.wait_for_timeout(wait_ms)
        txt, html = pg.inner_text("body"), pg.content()
        b.close()
    return txt, html


def run(source, fn, status):
    t0 = time.time()
    try:
        info = fn() or {}
        status[source] = {"ok": True, "secs": round(time.time() - t0, 1), **info}
    except Exception as e:
        status[source] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}",
                          "trace": traceback.format_exc()[-1200:]}
    print(source, json.dumps(status[source])[:300], flush=True)


def main():
    today = datetime.datetime.now(ET).date()
    if len(sys.argv) > 1:
        today = datetime.date.fromisoformat(sys.argv[1])
    status = {"collected_at": datetime.datetime.now(ET).isoformat(timespec="seconds"), "date": today.isoformat()}

    # Finals for the last three days (late West Coast and Hawaii games finish after midnight ET).
    def finals(d):
        sb = espn_scoreboard(d)
        save(d, "espn_final.json", sb)   # kept apart from the pre-game snapshot, which holds the lines we used
        done = sum(1 for e in sb.get("events", []) if e.get("status", {}).get("type", {}).get("completed"))
        return {"games": len(sb.get("events", [])), "final": done}
    for back in (1, 2, 3):
        d = today - datetime.timedelta(days=back)
        run(f"espn_finals_{d.isoformat()}", lambda d=d: finals(d), status)

    def espn_today():
        sb = espn_scoreboard(today)
        save(today, "espn_scoreboard.json", sb)
        pr = espn_predictors(sb)
        save(today, "espn_predictor.json", pr)
        return {"games": len(sb.get("events", [])), "predictors": sum(1 for v in pr.values() if "error" not in v)}
    run("espn", espn_today, status)

    # Torvik through cbbdata (barttorvik.com itself refuses GitHub's servers). Needs CBD_USER / CBD_PW secrets.
    if os.environ.get("CBD_USER") and os.environ.get("CBD_PW"):
        def torvik():
            sys.path.insert(0, os.path.join(ROOT, "model"))
            import names as N
            r = requests.post("https://www.cbbdata.com/api/auth/login",
                              json={"username": os.environ["CBD_USER"], "password": os.environ["CBD_PW"]}, timeout=30)
            r.raise_for_status()
            body = r.json()
            key = next(iter(body.values())) if isinstance(body, dict) else body[0]
            if isinstance(key, list):
                key = key[0]
            sb = json.load(open(os.path.join(day_dir(today), "espn_scoreboard.json")))
            out, asked = [], 0
            for ev in sb.get("events", []):
                comp = (ev.get("competitions") or [{}])[0]
                cs = {c.get("homeAway"): c for c in comp.get("competitors", [])}
                if ev.get("status", {}).get("type", {}).get("state") != "pre" or "home" not in cs or "away" not in cs:
                    continue
                h = N.to_canon(cs["home"]["team"].get("location", "")) or N.to_canon(cs["home"]["team"].get("displayName", ""), True)
                a = N.to_canon(cs["away"]["team"].get("location", "")) or N.to_canon(cs["away"]["team"].get("displayName", ""), True)
                if not h or not a:
                    continue
                q = {"key": key, "team": N.TORVIK.get(h, h), "opp": N.TORVIK.get(a, a), "date": today.strftime("%Y%m%d"),
                     "location": "N" if comp.get("neutralSite") else "H"}
                asked += 1
                try:
                    rr = requests.get("https://www.cbbdata.com/api/torvik/game/prediction", params=q, timeout=30)
                    out.append({"espn_id": ev["id"], "home": h, "away": a, "status": rr.status_code,
                                "rows": rr.json() if rr.headers.get("content-type", "").startswith("application/json") else rr.text[:300]})
                except Exception as e:
                    out.append({"espn_id": ev["id"], "home": h, "away": a, "error": str(e)[:200]})
                time.sleep(0.3)
            save(today, "torvik_cbbdata.json", out)
            good = sum(1 for x in out if x.get("status") == 200 and isinstance(x.get("rows"), list) and len(x["rows"]) >= 2)
            return {"asked": asked, "predictions": good}
        run("torvik", torvik, status)
    else:
        status["torvik"] = {"ok": False, "error": "No cbbdata login saved (CBD_USER / CBD_PW secrets)."}

    # CBBD: adjusted efficiency ratings and team pace. Needs the CBBD_API_KEY secret.
    if os.environ.get("CBBD_API_KEY"):
        def cbbd():
            hdr = {"Authorization": "Bearer " + os.environ["CBBD_API_KEY"], "Accept": "application/json"}
            season = today.year + 1 if today.month >= 8 else today.year
            base = "https://api.collegebasketballdata.com"
            rat = requests.get(f"{base}/ratings/adjusted", params={"season": season}, headers=hdr, timeout=60)
            rat.raise_for_status()
            stats = requests.get(f"{base}/stats/team/season", params={"season": season}, headers=hdr, timeout=60)
            stats.raise_for_status()
            save(today, "cbbd_ratings.json", rat.json())
            save(today, "cbbd_team_stats.json", stats.json())
            return {"teams_rated": len(rat.json()), "teams_with_stats": len(stats.json())}
        run("cbbd", cbbd, status)
    else:
        status["cbbd"] = {"ok": False, "error": "No CBBD_API_KEY secret saved."}

    def hasla():
        txt, html = page_text("https://haslametrics.com/ratings.php", wait_ms=8000)
        save(today, "hasla.txt", txt)
        save(today, "hasla.html", html)
        return {"chars": len(txt), "has_outcomes": "EXPECTED OUTCOMES" in txt.upper()}
    run("haslametrics", hasla, status)

    save(today, "status.json", status)
    bad = [k for k, v in status.items() if isinstance(v, dict) and not v.get("ok")]
    print("collection done; failed:", bad or "none")


if __name__ == "__main__":
    main()
