# NFL Betting Analytics Layer — Handoff for Dashboard Design

Context for designing a props/matchup dashboard on top of `nfl_betting.duckdb`'s
analytics layer. This layer sits on top of the core ingestion tables described
in `db/ANALYTICS_HANDOFF.md` (read that first if you need play-by-play/roster
details) and adds defense-allowed stats, rolling player averages, and a
query API purpose-built for dashboard consumption.

## Tech stack

- **DB:** DuckDB, single file `nfl_betting.duckdb`
- **Query layer:** `nfl_betting/analytics/queries.py` — import this and call
  its functions; you should not need to write raw SQL against the DB for a
  standard prop/matchup dashboard. Every function returns a pandas DataFrame.
- **Build layer:** `nfl_betting/analytics/main.py` rebuilds all analytics
  tables from the core tables. Re-run it after new games are ingested
  (`nfl_betting/main.py`) to refresh the analytics layer. Idempotent, safe to
  re-run any time.

```python
import sys; sys.path.insert(0, "path/to/nfl_betting")
from analytics import queries

con = queries.connect()  # optional: reuse across multiple calls
df = queries.get_prop_matchup("00-0030506", "LV", 2024, 8, con=con)
```

## Data lineage (what feeds what)

```
core_games, core_players, pbp_plays, agg_player_game, agg_team_game   <- core layer (see db/ANALYTICS_HANDOFF.md)
        |
        v
agg_team_defense_game        (Phase 1: per-team-per-game defense-allowed stats)
        |
        v
agg_team_defense_season      (Phase 2: season-to-date defense averages + rankings)

agg_player_game
        |
        v
agg_player_season_to_date    (Phase 3: rolling l3/l5/season player averages)

agg_player_game + pbp_plays
        |
        v
view_red_zone_target_share, view_red_zone_rush_share   (Phase 4: per-game red-zone share views)

All of the above
        |
        v
queries.py   (Phase 5: 5 dashboard-ready functions)
```

---

## Tables & views

### `agg_team_defense_game` (1,140 rows — one row per team per game)
PK: `(defteam, game_id)`. What a defense allowed in a single game, broken out
by opposing-player position (RB/WR/TE) where relevant. ~40 columns covering:
rushing allowed (overall + vs RB), receiving allowed (vs WR/TE/RB — yards,
receptions, targets, TDs, yards/target), passing allowed (overall),
red-zone-specific versions of the above, and NGS-derived columns
(`avg_defenders_in_box`, `avg_time_to_throw`, `pressure_rate`) each paired
with a `*_coverage_rate` column telling you what fraction of plays actually
had that NGS field populated.

**Position attribution** comes from `agg_player_game.position` (position at
game time), not `core_players.position` (most-recent-season only) — already
handled for you if you use this table or `queries.py`.

### `agg_team_defense_season` (1,140 rows — one row per team per week they played)
PK: `(defteam, season, week)`. Season-to-date **per-game averages** (prefix
`avg_`) and **league rankings** (prefix `rank_`, 1 = best/fewest-allowed, 32 =
worst) through that week, for the prop-relevant subset of Phase 1's columns:
`rank_rush_yards_allowed_vs_rb`, `rank_rec_yards_allowed_vs_wr`,
`rank_rec_yards_allowed_vs_te`, `rank_targets_allowed_vs_wr`,
`rank_targets_allowed_vs_te`, `rank_pass_yards_allowed`,
`rank_rz_tds_allowed`. Also has `games_played`.

⚠️ **No row for a bye week** — a team that didn't play that week has no
entry. `queries.py` handles the "as of" fallback for you (see below); if you
query this table directly, you'll need to do the same.

⚠️ **Rank is per-native-week, not globally consistent.** `rank_X` at week 8
is computed only against teams that *also* have a week-8 row — a team on
its week-7 bye isn't in that comparison set. `get_league_defensive_rankings()`
in `queries.py` recomputes a fresh, apples-to-apples rank across whatever
teams are actually being displayed; prefer that over reading `rank_X`
directly if you're building a leaderboard.

### `agg_player_season_to_date` (22,763 rows — one row per player per week their team played)
PK: `(player_id, season, week)`. Rolling averages as of that week:
- `l3_*` — trailing 3 **games played** (not calendar weeks)
- `l5_*` — trailing 5 games played
- `szn_*` — full season to date
- Metrics per window: `rushing_yards`, `receiving_yards`, `receptions`,
  `targets`, `touchdowns`, `carries`, `pass_attempts`, `passing_yards`,
  `snap_pct`, `epa_avg`, `red_zone_targets`, `red_zone_carries`,
  `target_share`, `carry_share`
- `szn`-only extras: `szn_games_played`, `szn_red_zone_target_share_avg`,
  `szn_red_zone_rush_share_avg`

**`is_active_week` (BOOLEAN)** — `true` if the player actually had a
qualifying play/snap that week; `false` means this row's rolling values were
**carried forward** from their last active game (e.g. a healthy scratch or
zero-snap inactive week — the team played, the player didn't do anything).
Rows before a player's first active game in a season are `NULL` (not
fabricated zeros). There's still no row at all for a bye week (the team
itself didn't play). **If you're showing "is this a real game's stats or a
carried-forward placeholder," check `is_active_week`.**

### `view_red_zone_target_share` / `view_red_zone_rush_share` (14,731 rows each)
Every `agg_player_game` column plus one extra: `red_zone_target_share` /
`red_zone_rush_share` = that player's red-zone targets/carries divided by
their team's red-zone targets/carries **that specific game**. `NULL` (not 0)
if the team had zero red-zone possessions that game. These are per-game
views, not rolling — for a rolling red-zone share use
`agg_player_season_to_date.szn_red_zone_target_share_avg` instead.

---

## `queries.py` API (what you'll actually call from the dashboard)

All functions: return an **empty DataFrame** (never raise) when there's no
matching data, and accept `season`/`week` explicitly so any historical
snapshot is queryable, not just the present. All accept an optional `con=`
(a `queries.connect()` connection) to avoid reopening the DB per call.

### `get_player_prop_profile(player_id, season, week, con=None)`
One row: that player's `l3_`/`l5_`/`szn_` stats **as of** `week`. "As of"
means the most recent week they were actually active at or before `week` —
so you can pass an upcoming/unplayed week number and get their latest known
form. Returns `requested_week` and `as_of_week` so you can show "stats
through Week N" even if `week` itself hasn't happened yet or was a bye.
Includes `player_name`, `position`, `team`.

### `get_defense_matchup(defteam, season, week, con=None)`
One row: that defense's season-to-date `avg_*`/`rank_*` as of `week` (same
as-of fallback as above). Includes `team_name`.

### `get_prop_matchup(player_id, defteam, season, week, bottom_pct=0.5, con=None)`
**The main one for a prop-vs-matchup view.** Auto-detects the player's
position and returns, in one row:
- Player's `l3_`/`l5_`/`szn_` averages, filtered to the metrics relevant to
  their position (e.g. a QB gets `passing_yards`/`pass_attempts`
  /`touchdowns`; a WR/TE gets `receiving_yards`/`targets`/`target_share`
  /`receptions`/`touchdowns`; an RB gets both rushing and receiving)
- Opposing defense's relevant `avg_*allowed*` and `rank_*` columns, all
  prefixed `opp_` (e.g. `opp_avg_rec_yards_allowed_vs_wr`)
- `primary_stat` (e.g. `"receiving_yards"`) — the headline prop stat for
  that position
- `primary_stat_rank` — that defense's rank against the primary stat
- `pool_size` — how many teams that rank was computed against (usually 32,
  can be fewer early season / around byes)
- `matchup_advantage` (bool) — `True` if `primary_stat_rank` falls in the
  worst `bottom_pct` fraction of `pool_size`. Default `0.5` = worse half of
  the league; pass e.g. `bottom_pct=0.25` to only flag the worst quarter.

Empty DataFrame if either side has no data, or the position isn't QB/RB/WR/TE.

### `get_league_defensive_rankings(season, week, position, bottom_pct=None, con=None)`
All teams ranked (1=best) against `position` ("QB"/"RB"/"WR"/"TE") as of
`week`, with `team_name` and the position's relevant `avg_*` columns —
good for a sortable league table or a "best/worst matchups this week" widget.
Rank here is **recomputed fresh** across whichever teams have data (handles
bye-week staggering correctly, unlike reading `rank_X` off the season table
directly). Pass `bottom_pct` (e.g. `0.25`) to get back only the worst
fraction of teams instead of all of them.

### `get_player_game_log(player_id, season, con=None)`
Full-season game log for one player — every `agg_player_game` column plus
`opponent` and `game_date`, sorted by week. Good for a player detail
page / trend chart.

---

## Things worth knowing before designing the UI

1. **"As of week" is a fallback, not an exact match**, for
   `get_player_prop_profile` and `get_defense_matchup` (and by extension
   `get_prop_matchup`). If you pass the upcoming week number for a game
   that hasn't been played yet, you correctly get the player's/defense's
   latest known form. Always surface `as_of_week` next to `requested_week`
   in the UI somewhere (even just a tooltip) so it's clear when data is
   from an earlier week than requested — e.g. a player who's been on bye
   or injured for 2 weeks will show stale data with an older `as_of_week`.

2. **`bottom_pct` lets the matchup-advantage cutoff be user-adjustable.**
   Consider exposing this as a slider/dropdown ("show matchups in the worst
   25% / 50% / etc.") rather than hardcoding it — it's already wired to be
   configurable per call.

3. **NULL has a specific meaning throughout this layer: "not enough
   qualifying data," not zero.** `epa_avg`, `snap_pct`, red-zone shares, and
   several `*_allowed` ratio columns are `NULL` when there were 0 qualifying
   plays/attempts, not 0. Don't let a chart silently render `NULL` as 0 —
   it should probably render as a gap, "—", or "N/A."

4. **`is_active_week=false` rows in `agg_player_season_to_date` are
   carried-forward, not real games.** If a dashboard view lists recent games
   or computes something game-by-game, filter to `is_active_week=true`
   unless you specifically want the "last known state during an inactive
   week" behavior.

5. **NGS columns (`avg_defenders_in_box`, `avg_time_to_throw`,
   `pressure_rate`, etc.) each have a paired `*_coverage_rate` column.**
   Low coverage doesn't necessarily mean the metric is untrustworthy in this
   dataset (`defenders_in_box`/`was_pressure` are ~100% populated;
   `time_to_throw` is ~88-92%), but it's good practice to show the coverage
   rate (or at least gray out the metric) if it drops materially below 100%
   for a given team/week.

6. **A defense's rank pool can be smaller than 32**, especially early
   season or around bye weeks. `get_league_defensive_rankings` and
   `get_prop_matchup`'s `pool_size` both surface this — worth showing
   "ranked X of Y teams" rather than always assuming a 32-team denominator.

7. **RBs get both rushing and receiving stats/matchup columns** (modern RB
   props span both). QB gets passing only. WR/TE get receiving only.

Full build history, every design decision made while building this layer,
and the reasoning behind each — including two real bugs we caught by
validating against an actual box score — is in
`analytics/ASSUMPTIONS_AND_ISSUES.md`, if you want more depth on any of the
above before making a UI decision that depends on it.

## Suggested dashboard starting points

- **Player prop card:** `get_player_prop_profile` + `get_prop_matchup` for a
  single player/upcoming-opponent — l3/l5/season trend plus the matchup
  rank/advantage flag.
- **Weekly matchup board:** `get_league_defensive_rankings` per position,
  filtered to `bottom_pct` for a "exploitable defenses this week" view.
- **Player trend page:** `get_player_game_log` charted over the season,
  with `l3_`/`szn_` lines overlaid from `agg_player_season_to_date`.
- **Red-zone usage:** `view_red_zone_target_share`/`view_red_zone_rush_share`
  per game, or the rolling `szn_red_zone_target_share_avg`/
  `szn_red_zone_rush_share_avg` from `agg_player_season_to_date` for a
  season-long usage trend.
