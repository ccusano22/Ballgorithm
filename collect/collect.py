"""Daily collection, run by GitHub Actions. Saves raw source data under data/raw/<date>/ and a status.json that
says what worked. Each source is independent: one failing never stops the others.

  ESPN       public JSON API: schedule, DraftKings spread/total, final scores, BPI matchup predictor
  Torvik     barttorvik.com daily schedule page (T-Rank line and projected score per game)
  Haslametrics  ratings page, "This week's expected outcomes" (projected score per team)
  KenPom     FanMatch, only if KENPOM_EMAIL and KENPOM_PASSWORD secrets are set
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


def kenpom_login(pg):
    pg.goto("https://kenpom.com/", wait_until="domcontentloaded", timeout=90000)
    pg.fill('input[name="email"]', os.environ["KENPOM_EMAIL"])
    pg.fill('input[name="password"]', os.environ["KENPOM_PASSWORD"])
    pg.click('input[type="submit"], button[type="submit"]')
    pg.wait_for_timeout(4000)


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

    def torvik():
        txt, html = page_text(f"https://barttorvik.com/schedule.php?date={today.strftime('%Y%m%d')}")
        save(today, "torvik.txt", txt)
        save(today, "torvik.html", html)
        return {"chars": len(txt), "lines_with_projection": txt.count("%)")}
    run("torvik", torvik, status)

    def hasla():
        txt, html = page_text("https://haslametrics.com/ratings.php", wait_ms=8000)
        save(today, "hasla.txt", txt)
        save(today, "hasla.html", html)
        return {"chars": len(txt), "has_outcomes": "EXPECTED OUTCOMES" in txt.upper()}
    run("haslametrics", hasla, status)

    if os.environ.get("KENPOM_EMAIL") and os.environ.get("KENPOM_PASSWORD"):
        def kenpom():
            txt, html = page_text(f"https://kenpom.com/fanmatch.php?d={today.isoformat()}", login=kenpom_login)
            save(today, "fanmatch.txt", txt)
            return {"chars": len(txt), "lines_with_projection": txt.count("%)")}
        run("kenpom", kenpom, status)
    else:
        status["kenpom"] = {"ok": False, "error": "No KenPom login saved in the repo's secrets; KenPom sits out."}

    save(today, "status.json", status)
    bad = [k for k, v in status.items() if isinstance(v, dict) and not v.get("ok") and k != "kenpom"]
    print("collection done; failed:", bad or "none")


if __name__ == "__main__":
    main()
