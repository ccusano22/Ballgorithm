"""One-off diagnostic: which ways of reaching Torvik and KenPom get past their bot protection from GitHub's servers."""
import json, os, sys, time, datetime
import requests
CHROME_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
HDRS = {"User-Agent": CHROME_UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.google.com/"}
mode = sys.argv[1] if len(sys.argv) > 1 else "headless"
d = datetime.date.today().strftime("%Y%m%d")
out = {"mode": mode}

def probe(name, fn):
    try:
        out[name] = fn()
    except Exception as e:
        out[name] = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    print(name, out[name], flush=True)

if mode == "headless":
    for name, url in [("torvik_requests_schedule", f"https://barttorvik.com/schedule.php?date={d}"),
                      ("torvik_requests_csv", "https://barttorvik.com/2027_team_results.csv"),
                      ("torvik_requests_trank", "https://barttorvik.com/trank.php")]:
        probe(name, lambda url=url: (lambda r: {"status": r.status_code, "len": len(r.text), "proj": r.text.count("%)"),
                                                "head": r.text[:120]})(requests.get(url, headers=HDRS, timeout=30)))

from playwright.sync_api import sync_playwright

def stealth_page(p):
    b = p.chromium.launch(headless=(mode == "headless"),
                          args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
    ctx = b.new_context(user_agent=CHROME_UA, viewport={"width": 1400, "height": 1800}, locale="en-US",
                        timezone_id="America/New_York")
    ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
                        "window.chrome={runtime:{}};"
                        "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3]});"
                        "Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});")
    return b, ctx.new_page()

def wait_clear(pg, secs=45):
    t0 = time.time()
    while time.time() - t0 < secs:
        txt = pg.inner_text("body")
        if "security verification" not in txt.lower() and "just a moment" not in txt.lower() and "request blocked" not in txt.lower():
            return round(time.time() - t0, 1), txt
        pg.wait_for_timeout(1500)
    return None, pg.inner_text("body")

with sync_playwright() as p:
    b, pg = stealth_page(p)
    def torvik():
        pg.goto(f"https://barttorvik.com/schedule.php?date={d}", wait_until="domcontentloaded", timeout=90000)
        secs, txt = wait_clear(pg, 30)
        return {"cleared_after": secs, "len": len(txt), "proj": txt.count("%)"), "head": txt[:150]}
    probe(f"torvik_browser_{mode}", torvik)
    def kenpom():
        pg.goto("https://kenpom.com/", wait_until="domcontentloaded", timeout=90000)
        secs, txt = wait_clear(pg, 45)
        res = {"home_cleared_after": secs, "home_len": len(txt)}
        if secs is not None and os.environ.get("KENPOM_EMAIL"):
            try:
                pg.fill('input[name="email"]', os.environ["KENPOM_EMAIL"], timeout=15000)
                pg.fill('input[name="password"]', os.environ["KENPOM_PASSWORD"], timeout=15000)
                pg.click('input[type="submit"], button[type="submit"]', timeout=15000)
                pg.wait_for_timeout(5000)
                res["logged_in_text"] = "logout" in pg.inner_text("body").lower()
            except Exception as e:
                res["login_error"] = str(e)[:150]
            pg.goto("https://kenpom.com/fanmatch.php", wait_until="domcontentloaded", timeout=90000)
            s2, t2 = wait_clear(pg, 45)
            res.update({"fanmatch_cleared_after": s2, "fanmatch_len": len(t2), "proj": t2.count("%)"), "head": t2[:150]})
        return res
    probe(f"kenpom_browser_{mode}", kenpom)
    b.close()

os.makedirs("data/raw/diag", exist_ok=True)
json.dump(out, open(f"data/raw/diag/{mode}.json", "w"), indent=1)
