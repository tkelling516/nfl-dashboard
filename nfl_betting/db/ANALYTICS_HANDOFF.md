# NFL Betting Database — Handoff for Analytics Layer Development

Context for building an analytics/props layer on top of `nfl_betting.duckdb`. This covers the schema, what each computed field actually means, current data coverage, and known gaps/caveats worth knowing before building on top of it.

## Tech stack

- **DB:** DuckDB, single file `nfl_betting.duckdb` (~15.5 MB currently)
- **Connect:** `duckdb.connect("path/to/nfl_betting.duckdb")` in Python, or the DuckDB CLI
- **Source data:** nflverse via `nfl_data_py` 0.3.3 (play-by-play, rosters, snap counts)
- **Pipeline:** `nfl_betting/main.py` — idempotent, safe to re-run (upserts via `ON CONFLICT`, no duplication)

## Current data coverage (as of 2026-07-29)

- **Seasons:** 2024 (complete), 2025 (complete through Super Bowl), 2026 (0 rows — season hasn't started; pipeline will pick it up automatically once nflverse publishes it in September 2026)
- 570 games (285/season, 22 weeks = 18 regular season + playoffs), 98,263 plays
- 1,466 players — **QB/RB/WR/TE only**, K/P/OL/DL/LB/DB excluded everywhere
- 14,731 player-game rows, 1,140 team-game rows
- Betting and external (injuries/news) tables exist but are **empty** — ingestion deferred, not yet built

## Schema

### `core_teams` (32 rows)
| Column | Notes |
|---|---|
| `team_id` PK | Same value as `team_abbr` (e.g. `'KC'`) — **not** nflverse's numeric team id |
| `team_abbr` | |
| `team_name` | Full name, e.g. "Kansas City Chiefs" |

### `core_players` (1,466 rows)
| Column | Notes |
|---|---|
| `player_id` PK | GSIS id (nflverse's canonical player id, e.g. `'00-0033553'`) |
| `player_name` | |
| `player_name_normalized` | Lowercased/stripped |
| `position` | QB / RB / WR / TE only |

⚠️ Only the **most recent season's** name/position is kept per player — no historical team/position tracking here. For a player's team/position *in a specific game*, use `agg_player_game.team` / `.position` instead.

### `core_games` (570 rows)
| Column | Notes |
|---|---|
| `game_id` PK | Format `YYYY_WW_AWAY_HOME`, e.g. `'2024_01_ARI_BUF'` |
| `game_date`, `season`, `week` | week 1–18 regular season, 19–22 playoffs |
| `home_team`, `away_team` | joins to `core_teams.team_id` |

### `pbp_plays` (98,263 rows)
One row per play. PK `(game_id, play_id)`. **Not** the full ~400-column nflverse play-by-play — a curated subset, chosen for what feeds the aggregates below, plus 6 extra columns added for richer prop modeling:

`game_id, play_id, drive, posteam, defteam, down, ydstogo, yardline_100, play_type, yards_gained, epa, wp, passer_player_id, rusher_player_id, receiver_player_id, air_yards, first_down, game_date, season, week, touchdown, td_player_id, complete_pass, pass_attempt, rush_attempt, return_team, penalty, route, offense_personnel, defenders_in_box, was_pressure, time_to_throw, defense_coverage_type`

Key semantics:
- `yardline_100`: distance from opponent's end zone (0–100). **Red zone = `yardline_100 <= 20`** everywhere in this project.
- `play_type`: `'pass'`, `'run'`, `'punt'`, `'field_goal'`, `'kickoff'`, `'extra_point'`, `'qb_kneel'`, `'qb_spike'`, `'no_play'`, or NULL
- `route`, `offense_personnel`, `defense_coverage_type`, `was_pressure`, `time_to_throw`, `defenders_in_box` come from nflverse's NGS/participation-data merge, not the core nflfastR dictionary — sparse coverage (~40–45% of plays have non-null values for these; NULL usually means "not tracked," not "false/zero")
- ⚠️ **`route` only captures the *targeted* receiver's route on a play** (nfl_data_py flattens participation to one row per play). It is **not** a true routes-run signal — verified it's ≤ targets and equal 96% of the time. Don't use it to derive "routes run."

Full nflverse field dictionary (all ~400 upstream columns, descriptions, which ones made it into this schema) is at `db/pbp_field_dictionary.md` if you need to widen this table later.

### `agg_player_game` (14,731 rows)
One row per player per game, pre-filtered to QB/RB/WR/TE. PK `(player_id, game_id)`.

| Column | Formula / meaning |
|---|---|
| `player_type` | `'QB'` if position is QB, else `'skill'` |
| `is_active` | true if player has snap-count data OR ≥1 pass attempt/carry/target that game |
| `targets` / `carries` / `pass_attempts` | counts from `pbp_plays` (`receiver_player_id` / `rusher_player_id` / `passer_player_id`) |
| `team_targets` / `team_carries` / `team_pass_attempts` | team totals for that game — divide the player column by these for target/carry share |
| `total_rush_yards` / `total_receiving_yards` / `total_receptions` / `passing_yards` | standard box-score stats |
| `touchdowns` | **all** TD types combined (rush + rec + pass), whichever `td_player_id` matched |
| `yards` | `passing_yards + total_rush_yards + total_receiving_yards` — generic combined total; use position-specific columns for prop-specific lines |
| `epa_avg` | play-count-weighted average EPA across the player's attempts+carries+targets that game; **NULL** (not 0) if the player had none of those (e.g. only appeared in snap-count data) |
| `red_zone_targets` / `red_zone_carries` / `red_zone_attempts` | same as targets/carries/pass_attempts, restricted to `yardline_100 <= 20` |
| `air_yards` | air yards thrown (QB) or targeted (pass-catcher) |
| `snap_count` / `snap_pct` | from nflverse snap counts, cross-walked PFR id → GSIS id via `import_ids()`; NULL if the crosswalk didn't match or season data isn't published yet |
| `routes_run` | **always NULL** — no reliable per-play, multi-player route-participation source available (see caveat above) |

### `agg_team_game` (1,140 rows)
One row per team per game, **offense only** (`posteam = team`). PK `(team, game_id)`.

| Column | Formula |
|---|---|
| `third_down_pct` | conversions / attempts, NULL if 0 attempts |
| `red_zone_trips` | count of distinct **drives** with ≥1 play at `yardline_100 <= 20` — trips into the red zone, not red-zone play count |
| `red_zone_plays` / `red_zone_pass_plays` / `red_zone_run_plays` | raw play counts inside the 20 |
| `fourth_down_conversions` / `third_down_conversions` | down = 4 (or 3) AND `first_down` = true — `first_down` includes penalty-awarded first downs, so an automatic first down from a defensive penalty on 4th down counts as a "conversion" here |
| `avg_epa` | simple average EPA across all offensive plays that game |

⚠️ **No defensive/opponent-allowed equivalent exists yet.** This table is offense-only per team — there's no "how has this defense performed against opposing WRs" table. If the analytics layer needs matchup-adjusted props (e.g. "this WR vs. this specific pass defense"), that requires a new aggregation off `pbp_plays.defteam`, not yet built.

### `betting_game_odds`, `betting_player_props`, `external_injuries`, `external_news`
Schema only, 0 rows — deferred. See `db/schema.sql` for exact columns when ready to populate.

## Known caveats (worth keeping in mind while designing the analytics layer)

1. `routes_run` — always NULL.
2. `epa_avg` — NULL means "no qualifying plays," not zero.
3. `snap_count`/`snap_pct` — NULL on crosswalk misses or unpublished seasons.
4. NGS-derived pbp columns (`was_pressure`, `time_to_throw`, `defenders_in_box`, `defense_coverage_type`, `offense_personnel`) — ~40–45% populated; NULL ≠ false.
5. `core_players` has no season-by-season history — use `agg_player_game` for game-specific team/position.
6. **No defensive/opponent-allowed aggregates** — biggest gap for matchup-adjusted prop modeling.
7. 2026 season will be empty until nflverse publishes it; the pipeline auto-picks it up on next run once available.

## Suggested starting points

- **Prop baselines:** rolling averages/medians of `agg_player_game` columns over trailing N games
- **Usage share:** `targets / team_targets`, `carries / team_carries`
- **TD likelihood:** `red_zone_targets` / `red_zone_carries` / `red_zone_attempts` as leading indicators
- **Matchup adjustment:** not directly supported yet — would need a new `defteam`-based aggregate (see gap #6 above)
