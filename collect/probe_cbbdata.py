"""One-off check of what cbbdata currently serves for the 2026-27 season. Prints counts only, never the key."""
import io, json, os, requests

u, p = os.environ["CBD_USER"].strip(), os.environ["CBD_PW"].strip()
r = requests.post("https://www.cbbdata.com/api/auth/login", json={"username": u, "password": p}, timeout=30)
r.raise_for_status()
body = r.json()
key = next(iter(body.values())) if isinstance(body, dict) else body[0]
key = key[0] if isinstance(key, list) else key
print(f"::add-mask::{key}")
B = "https://www.cbbdata.com/api"

def pred(team, opp, date, loc="H"):
    q = {"key": key, "team": team, "opp": opp, "location": loc}
    if date:
        q["date"] = date
    rr = requests.get(f"{B}/torvik/game/prediction", params=q, timeout=60)
    try:
        js = rr.json()
    except Exception:
        js = rr.text[:120]
    print("prediction", team, "v", opp, date or "(no date)", rr.status_code, json.dumps(js)[:400])

for d in ["20261105", "20261104", "20261102", None, "20260301"]:
    pred("Duke", "North Carolina", d)
pred("Texas", "Gardner Webb", "20261105")

def parquet(path, **q):
    import pyarrow.parquet as pq
    rr = requests.get(f"{B}/{path}", params={"key": key, **q}, timeout=120)
    if rr.status_code != 200:
        print(path, q, rr.status_code, rr.text[:150]); return
    try:
        t = pq.read_table(io.BytesIO(rr.content)).to_pandas()
    except Exception as e:
        print(path, q, "not parquet:", rr.content[:150]); return
    cols = list(t.columns)
    extra = ""
    if "date" in cols:
        extra = f" dates {t['date'].min()} .. {t['date'].max()}"
    if "year" in cols:
        extra += f" years {sorted(t['year'].unique().tolist())[-3:]}"
    print(path, q, f"{len(t)} rows{extra}; columns: {cols[:25]}")

parquet("torvik/ratings", year=2027)
parquet("torvik/ratings", year=2026)
parquet("torvik/ratings/archive", year=2027)
parquet("torvik/ratings/archive", year=2026)
parquet("torvik/game/factors", year=2027)
