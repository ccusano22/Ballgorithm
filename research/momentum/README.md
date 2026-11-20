# Momentum Index: how it was chosen

Question: which teams are hot or cold, and does it carry into the next game?

Everything below is walk-forward on every D1-vs-D1 game from ESPN for 2024-25 and 2025-26 (about 5,750 games a
season). Each game is predicted only from games before its date. "Next game vs the number" means the final margin
minus our static rating's prediction for that game.

## Findings

1. **Team strength really does change during a season, but slowly and by little.** A Kalman filter that lets every
   team's strength wander fits best at about 1.1 points of drift per 30 days, the same in both seasons
   (`backtest.py`). That's small next to one game's noise (about 11.4 points).
2. **The board's current hot/cold rule is a coin flip.** Teams that beat our number in 4 of their last 5 beat it
   50.9% and 50.7% of the time next game. Teams that missed 4 of 5 *also* beat it (50.7%, 50.3%)
   (`signals.py`).
3. **Short streaks are mostly noise; slow trends carry.** Raw last-3, last-5 and fast-decay averages all carry a
   little forward but need heavy shrinking (only 4 to 15% of a last-5 average carries). Longer windows predict
   better. Mixing a short and a long window didn't beat one window out of sample.
4. **Shrink by how much evidence there is.** The index is an empirical-Bayes shrink of raw form. Its noise is known
   (game noise times the recency weights) and the real change it could reflect comes from a random-walk prior.
   Fit so that it carries forward one-for-one, it does: slope 0.99 ± 0.21 pooled, 0.92 and 1.07 in each season
   (`rw_eb.py`).
5. **Cold carries more than hot** (`tag_cutoffs.py`, both seasons pooled, games from Dec 1):

| Tag | Index | Share of team-games | Next game vs number | Beat the number |
|---|---|---|---|---|
| Hot | ≥ +0.75 | 4% | +0.72 ± 0.41 | 52.6% |
| Warming | +0.25 to +0.75 | 17% | +0.39 ± 0.21 | 51.4% |
| Steady | within ±0.25 | 60% | +0.10 ± 0.11 | 50.4% |
| Cooling | −0.25 to −0.75 | 16% | −0.60 ± 0.21 | 47.9% |
| Cold | ≤ −0.75 | 4% | −1.57 ± 0.44 | 44.3% |

Replaying 2025-26 at six dates (Dec 15 to Mar 2), teams tagged Hot beat the number by +0.83 over their next five
games (53.1%), and Cold teams missed by −1.30 (45.1%).

## Dead ends, for the record

- **Base + mean-reverting form** as two separate states per team: not identifiable from margins alone. Form
  barely moved off its prior.
- **Fixed-size prior** (same shrink at 5 games or 30): makes the index jumpy in a team's first weeks. Replaced by
  the random-walk prior, which keeps it near zero until the games are spread out enough to show a trend.
- **Requiring the raw streak to clear 1.5 or 2 noise SDs** on top of the index: smaller groups, no sharper results.

## Limits

- Game noise is about 11 points. In simulations (`tests/test_momentum.py`), a real 8-point swing at midseason is
  tagged Hot/Warming 6 times in 6 and Cold/Cooling 5 times in 6, but its size is understated. Sudden changes such
  as injuries show up slowly.
- Margins only. Haslametrics' Momentum, Momentum-Offense and Momentum-Defense are built from shot-level data and may
  be less noisy. They're collected daily from `ratings.xml` and earn a weight only once they predict beyond our
  index on 400+ later games.
- The index is checked against our own static rating here, not against the board's blend. The blend's sources
  already weight recent games (Haslametrics says so outright), so part of this may already be priced in. Archived
  daily snapshots (`data/momentum/`) let that be tested on this season's graded games.

## Reproduce

```
cd research/momentum
python pull_espn.py bt/espn    # two seasons of ESPN scoreboards, about 2 minutes
python backtest.py             # finding 1
python signals.py              # finding 2
python rw_eb.py                # finding 4
python tag_cutoffs.py          # finding 5
```
