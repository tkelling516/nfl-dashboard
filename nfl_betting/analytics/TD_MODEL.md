# Anytime-TD Probability Model

How `agg_td_probability` is built, what goes into it, and what it doesn't
know. Companion to `DASHBOARD_HANDOFF.md` — read that first for the
underlying tables this model is built on top of.

```python
from analytics import build_td_model
con = get_connection()
build_td_model.load_agg_td_probability(con)  # retrains + rescores, ~10-20s
```

Runs automatically as step 5/5 of `analytics/main.py` — no separate step to
remember. See "Pipeline integration" below for why that's safe to do on
every dashboard update.

---

## What it predicts

For every RB/WR/TE, in every game they've played and the current/upcoming
week's roster: the probability they score **at least one offensive
touchdown** (rushing or receiving — the "anytime TD scorer" prop market).
Passing TDs, return TDs, and defensive/special-teams TDs are excluded (see
"Target variable" below). QBs are out of scope for now — this would need a
different feature set (rushing-only, since passing TD props are a distinct
market) and wasn't part of the current request.

---

## Target variable

`scored_td = 1` if the player has a play in `pbp_plays` that game with
`touchdown = true`, `td_player_id` = them, **and** (`rush_attempt` or
`complete_pass`) — i.e. it actually happened on a rush or a catch.

That last condition matters: `agg_player_game.touchdowns` (the field the
rest of this codebase uses) does **not** filter this way, and 35 of 2,920
touchdown plays league-wide credited to a skill-position player are neither
a rush nor a completed pass — almost certainly kick/punt returns (e.g. a WR
who also returns). Using the raw field as the target would have the model
spend some of its signal trying to predict special-teams scores, which have
nothing to do with the offensive-role features it's given. `build_td_model.py`
recomputes the label directly from `pbp_plays` rather than reusing the
existing column.

---

## The leakage rule (read this before changing anything)

`agg_player_season_to_date` and `agg_team_defense_season` are both
**inclusive of the current week's own game** — the row at week W already
reflects what happened in week W. That's the right design for the
dashboard's "current form" display, and it's exactly wrong for a training
row: joining week W's row to predict week W's outcome lets the model see
the answer.

The fix, used for every feature in this model: `LAG(1) OVER (PARTITION BY
<player or team>, season ORDER BY week)`, i.e. always use the *previous*
row, never the row at the target week itself.

The reason this one rule is also correct for scoring the live
current/upcoming week (not just historical training rows) is worth
spelling out, because it's not obvious: `agg_player_season_to_date` carries
a row for *every* week a player's team plays, including future ones,
filled in via carry-forward (`is_active_week=false`) from their last real
active game. So for an unplayed week W, the row *at* W already equals
whatever LAG(1) would return anyway — nothing has happened since to change
it. One SQL pattern, applied uniformly, is provably correct in both
regimes. No separate "training mode" vs. "scoring mode" feature code exists
in `build_td_model.py` — there's exactly one feature-extraction path
(`_attach_lagged_features`), used for both.

Practical consequence: a player's first tracked game (no prior row to LAG
from at all) can't be modeled. `build_td_model.py` drops those rows. A
team's first game of a season similarly has no prior opponent-defense row —
but see below, that turned out to matter less than expected.

---

## Predictor variables

### Player role/usage (prior week's snapshot — the dominant signal class)
- Touchdown rate: `szn_touchdowns`, `l5_touchdowns`, `l3_touchdowns`
- Receiving volume: `szn_targets`, `l5_targets`, `l3_targets`,
  `szn_target_share`, `l5_target_share`, `l3_target_share`,
  `szn_receptions`, `l5_receptions`, `szn_receiving_yards`
- Rushing volume: `szn_carries`, `l5_carries`, `l3_carries`,
  `szn_carry_share`, `l5_carry_share`, `szn_rushing_yards`,
  `l5_rushing_yards`, `l3_rushing_yards`
- Snap share: `szn_snap_pct`, `l5_snap_pct`, `l3_snap_pct`
- Red-zone role: `szn_red_zone_target_share_avg`,
  `szn_red_zone_rush_share_avg`, **`szn_gtg_touches_avg`** (new — see below)
- Experience: `szn_games_played`

### Opponent defense (prior week's snapshot, curated subset)
Only TD-specific and position-matched allowed-rate columns from
`agg_team_defense_season` — `avg_rz_tds_allowed`,
`avg_rush_tds_allowed_vs_rb`, `avg_rec_tds_allowed_vs_{wr,te,rb}`,
`avg_rush_yards_allowed_vs_rb`, `avg_rec_yards_allowed_vs_{wr,te,rb}`,
`avg_rz_rush_yards_allowed_vs_rb`, `avg_rz_rec_yards_allowed_vs_{wr,te}`,
`avg_rz_targets_allowed_vs_{wr,te}`.

An earlier exploratory pass fed the model *every* `avg_*`/`rank_*` column
in that table (~50 of them, including pass-protection/coverage metrics
like completion % allowed, pressure rate, defenders in box). Those carried
essentially zero importance — they describe pass-defense quality, not
scoring tendency, and just added noise on a ~13K-row dataset. Dropped from
the production feature set.

### Game context (the target game itself — no lag needed; a spread/total is
set *before* kickoff, so it's legitimately pre-game information for that
same game, unlike player/opponent history)
- `is_home`
- `team_implied_total` = `(total_line ± spread_line) / 2` (this team's
  side)
- `team_favored_by` = `spread_line` signed from this team's perspective
- `is_dome` (roof is `dome` or `closed`)
- `div_game`

### Position
One-hot: `pos_RB`, `pos_WR`, `pos_TE`.

---

## Model choice

| Model | Holdout AUC | Holdout log-loss |
|---|---|---|
| Baseline (constant rate) | 0.500 | 0.482 |
| Logistic regression | 0.700–0.704 | 0.450–0.453 |
| Random forest | 0.740–0.741 | 0.426–0.427 |
| **HistGradientBoostingClassifier (shipped)** | **0.75** | **0.40** |

A plain logistic regression is a legitimate baseline but leaves real
accuracy on the table — the tree-based models pick up interaction effects
(e.g. "high red-zone share *and* a recent scoring streak") a linear model
undervalues.

`HistGradientBoostingClassifier` (`max_depth=4, learning_rate=0.05,
max_iter=300`) was chosen over a plain random forest for one production
reason beyond the small accuracy edge: **it handles missing feature values
natively.** That matters concretely here — a team's opponent has no prior
defensive row in the *literal first game of a season*, and a plain
scikit-learn `RandomForestClassifier`/`LogisticRegression` can't accept
NaN at all, which would have forced dropping every Week 1 game from
scoring entirely. HGB routes missing values down a learned split
direction instead, so Week 1 games still get an (opponent-blind, weaker)
prediction rather than none.

Calibration (holdout, predicted-probability deciles vs. actual TD rate):

| Decile | Avg. predicted | Actual rate |
|---|---|---|
| 1 (lowest) | 1.7% | 0.2% |
| 4 | 7.0% | 5.4% |
| 6 | 14.3% | 13.0% |
| 8 | 25.6% | 23.9% |
| 10 (highest) | 46.6% | 56.3% |

Middle deciles track closely; the model is if anything conservative at the
very top end rather than overconfident — the safer failure mode.

---

## What's most predictive (empirical, not assumed)

Ranked by random-forest / HGB feature importance across the full
exploratory run:

1. **Recent touchdown rate** (season TD rate/game) — by far the single
   strongest signal. Scoring is persistent: it's a proxy for the role
   (goal-line back, primary red-zone target) more than for that week's
   matchup.
2. **Usage volume and share** — targets, target share, receptions, snap %,
   carries, carry share, rushing/receiving yards. All cluster near the
   top; usage is the substrate scoring opportunity comes from.
3. **Goal-to-go touch share** — newly added (see below); lands around the
   #5 most important feature on its own once included.
4. Red-zone target/rush share (the broader "inside the 20" version) —
   still meaningfully predictive (TD rate roughly doubles from the bottom
   to top quartile) but ranks lower than raw usage once the model already
   has recent TD rate and volume — a lot of the same underlying signal is
   already captured by those.
5. **Opponent defensive matchup — surprisingly weak.** Every
   `opp_*_allowed`/`opp_rank_*` feature falls in the bottom half of all
   ~100 candidate features tested. TD rate by opponent red-zone-TDs-allowed
   quintile was nearly flat (19.0% → 17.7% → 18.8% → 19.1% → 20.9%). This
   doesn't mean matchup is irrelevant to scoring in general — it plausibly
   matters more for *team*-level scoring environment than for *which
   specific player* on that team scores, which is dominated by role. Worth
   knowing given the rest of this dashboard is built around matchup
   coloring: for touchdowns specifically, a player's own usage trumps who
   they're playing.
6. **Home/away — negligible.**

---

## Missing data

Two different categories, worth keeping separate:

**Already available upstream, now wired in** (this update): `spread_line`,
`total_line`, `roof`, `temp`, `wind`, `div_game` (added to `core_games`,
sourced from `nfl_data_py.import_schedules()`), `goal_to_go` (added to
`pbp_plays`). nflverse's full play-by-play pull carries ~370 documented
columns beyond what this project curates into `pbp_plays`/`core_games` —
see `db/pbp_field_dictionary.md` for the full inventory if another feature
idea needs upstream data that isn't here yet.

**Genuinely absent, would need a new data source:**
- `betting_player_props` and `external_injuries` exist as tables in the
  schema but have **0 rows** — ingestion for both is explicitly deferred.
  Out of scope for this pass per direction from the project owner.
  Injury/role-change context (a backup getting starter's-share goal-line
  work) is likely the single biggest real-world week-to-week swing factor
  in TD scoring and can't be derived from historical stats at all. Live
  anytime-TD odds would also be the natural benchmark to sanity-check this
  model's calibration against — currently nothing to compare to.
- Depth-chart/committee-backfield designation (who's "the" goal-line back
  when it's not obvious from usage yet, e.g. a new addition).

---

## Known limitations

- **Can't score a player's first tracked game** — no prior row to LAG
  from. Not fixable without cross-season history (see next point) or an
  external depth-chart signal.
- **Rolling windows don't cross season boundaries** (inherited from
  `agg_player_season_to_date`'s own design) — a player's Week 1 features
  next season start from scratch, same as the rest of this dashboard.
- **Opponent-blind for Week 1 of a season** — no prior defensive row
  exists yet for either team. HGB still produces a (weaker) prediction
  from player-side features alone rather than refusing to score, but it's
  worth knowing that early-season Week 1 predictions lean more heavily on
  last season's-worth-removed usage patterns than matchup.
- **~13K labeled rows across 2 seasons** is a modest sample for ~45
  features. The curated feature set (vs. throwing in all ~100 candidate
  columns) was a deliberate response to this, not just a readability
  choice.
- **No persisted model file.** `build_td_model.py` retrains from scratch
  every run — see "Pipeline integration."

---

## Pipeline integration

`agg_td_probability` is rebuilt from scratch every time `analytics/main.py`
runs (step 5/5), which is already part of the existing "update the
dashboard" workflow (`nfl_betting/main.py` → `nfl_betting/analytics/main.py`
→ commit `nfl_betting.duckdb` → push). No new step for you to remember.

This is intentionally **not** a persisted/cached model file that gets
loaded and reused — retraining is a few seconds on this data volume, so
every run reflects the database's current contents exactly, with the same
"recompute from source, idempotent, safe to re-run any time" property the
rest of this pipeline already has. The tradeoff: the model is only ever as
good as whatever 1-2 seasons of history exist at run time, and its exact
coefficients will drift slightly run to run as more games are added — the
holdout AUC printed on each run is there so a big unexpected drop is
visible rather than silent.

`agg_td_probability` schema:

| Column | Meaning |
|---|---|
| `player_id`, `game_id`, `season`, `week`, `position` | Identity |
| `td_probability` | Model output, 0-1 |
| `scored_td_actual` | `TRUE`/`FALSE` for already-played games, `NULL` for the live current/upcoming week (outcome not known yet) |
| `model_holdout_auc`, `model_holdout_log_loss` | This run's holdout metrics (same value on every row — a run-level stamp, not per-row) |
| `trained_at` | When this run happened |

The dashboard joins this onto `get_weekly_position_board()` (LEFT JOIN on
`player_id, game_id`) and shows it as a `TD Prob` column on all four
position tabs. `NULL` (rendered `—`) means the player didn't have enough
trailing history to be modeled that week — never 0, consistent with the
NULL convention everywhere else in this layer.
