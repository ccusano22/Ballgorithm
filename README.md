# The Ballgorithm

NCAA Division I men's basketball predictions, built and graded automatically every day.

## What runs, and when

GitHub Actions runs `.github/workflows/daily.yml` twice a day:

| Run | Eastern time (standard / daylight) | What it does |
|---|---|---|
| Morning | 5:40 AM / 6:40 AM | Pulls final scores, grades every pending game, builds today's slate |
| Afternoon | 3:20 PM / 4:20 PM | Pulls fresh lines and projections, re-blends games that haven't tipped, re-picks the Hammer among them |

GitHub sometimes starts scheduled runs 10–30 minutes late. Either run can be started by hand from the
**Actions** tab → *Daily collect and build* → *Run workflow*.

A Claude scheduled task publishes `site/` to the board artifact after each run.

## Sources

| Source | How it's collected | Notes |
|---|---|---|
| ESPN | Public JSON API | Schedule, DraftKings spread and total, final scores, BPI matchup predictor |
| Torvik | cbbdata API (barttorvik.com blocks GitHub) | T-Rank projected score and win percentage per game |
| Haslametrics | Headless browser, ratings page | "This week's expected outcomes" |
| CBBD | CollegeBasketballData.com API | Adjusted efficiency ratings and pace; margins derived, used once both teams have 5 games |

Each source is collected independently. If one fails, the others still run, and the board's notes say which
one was missing. `data/raw/<date>/status.json` records what worked on every run.

## Layout

- `collect/collect.py` — collection (writes `data/raw/<date>/`)
- `model/engine.py` — blend, SD, tiers, source weighting, grading, Hammer
- `model/run_daily.py` — grade what finished, build or refresh today's slate
- `model/names.py`, `model/teams.json` — one name per team, plus every alias seen from other sites
- `site/` — the board page and its data (`site/data/state.json` is the season's single source of truth)
- `tests/test_engine.py` — simulated season check of the learning loop

## Adding a team-name alias

If the board's notes say games were skipped for unrecognized names, add the site's spelling to `ALIAS` in
`model/names.py`, mapping it to the name in `model/teams.json`.

## Secrets (Settings → Secrets and variables → Actions)

| Secret | What it's for |
|---|---|
| `CBBD_API_KEY` | CollegeBasketballData.com API key (free) |
| `CBD_USER`, `CBD_EMAIL`, `CBD_PW` | cbbdata account, which serves T-Rank (Torvik) predictions. Create the account once with the *Create cbbdata account (run once)* workflow. |
