"""Are cbbdata and CBBD up, and how do they answer without a key?"""
import requests
for name, url in [
    ("cbbdata_prediction_nokey", "https://www.cbbdata.com/api/torvik/game/prediction?team=Duke&opp=Army&date=20261102&location=H"),
    ("cbbdata_login_bad", None),
    ("cbbd_ratings_nokey", "https://api.collegebasketballdata.com/ratings/adjusted?season=2027"),
]:
    try:
        if url:
            r = requests.get(url, timeout=30)
        else:
            r = requests.post("https://www.cbbdata.com/api/auth/login", json={"username": "x", "password": "y"}, timeout=30)
        print(name, r.status_code, r.headers.get("content-type"), r.text[:200].replace("\n", " "), flush=True)
    except Exception as e:
        print(name, "ERROR", type(e).__name__, str(e)[:200], flush=True)
