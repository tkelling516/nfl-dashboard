# Analytics Layer — Assumptions & Issues Log

Running log of assumptions made and issues encountered while building the
analytics layer, phase by phase. Intended for review once the full layer is
built, to decide what (if anything) to revisit.

---

## Phase 1 — `agg_team_defense_game`

### Bugs found and fixed (resolved, not open issues)

1. **`penalty=true` exclusion was wrong — dropped ~1,059 live plays.**
   Originally excluded every row where `penalty=true`, on the assumption that
   flag meant the play's stats were negated. It doesn't — it just means *a*
   penalty occurred somewhere on the play. Truly negated plays already carry
   `play_type='no_play'`, which is excluded separately. Caught by comparing
   `agg_team_defense_game` against the real 2024_01_ARI_BUF box score (James
   Conner and Kyler Murray's rushing lines were off). Fix: removed the
   `penalty=true` filter entirely; `no_play` exclusion is sufficient.
   Scope: 1,065 rows league-wide had `penalty=true` on a live play (541 run +
   522 pass); only 6 were also `no_play`.

2. **Two-point conversion attempts were being counted as regular rush/pass
   attempts.** Box scores don't count 2pt tries in rushing/receiving/passing
   stat lines, but nothing was excluding them. Root cause: `pbp_plays` didn't
   carry nflverse's `two_point_attempt` flag (it's in the full upstream pbp
   data — see `db/pbp_field_dictionary.md` — but wasn't in our curated
   subset). Fix: added `two_point_attempt` to `pbp_plays` (schema.sql +
   `ingestion/ingest_pbp.py`), re-ran ingestion to backfill, and excluded
   `two_point_attempt=true` rows in the defense aggregation. Verified the
   real flag matches the `down IS NULL` heuristic exactly (278/278, no
   mismatches) before switching to it. Scope: 272 plays league-wide.
   **Note:** this changed `pbp_plays`' schema — `db/ANALYTICS_HANDOFF.md`
   doesn't mention this column yet and may need a doc update later.

   After both fixes, `agg_team_defense_game` matches the real
   2024_01_ARI_BUF box score exactly (rush attempts/yards/TDs allowed by
   both teams' defenses).

### Open assumptions / notes worth revisiting

3. **NGS coverage in this data is much higher than `ANALYTICS_HANDOFF.md`
   claims.** The handoff doc says NGS-derived columns (`was_pressure`,
   `time_to_throw`, `defenders_in_box`, `defense_coverage_type`,
   `offense_personnel`) are "~40-45% populated." In practice: `defenders_in_box`
   and `was_pressure` are **100% populated** on all rush/pass plays
   respectively (their `*_coverage_rate` columns are always 1.0 in the data
   so far); `time_to_throw` is ~88-92% populated per game (does vary, so at
   least partially confirms the doc's general caveat). The code doesn't
   assume any fixed coverage — it computes real `coverage_rate` per row
   either way — but worth knowing when interpreting these metrics or if the
   doc gets updated.

4. **RESOLVED (iteration 1).** ~~`rz_tds_allowed` counts all touchdown
   types (rush/pass/return)~~ — changed to offense-only: now requires
   `(rush_attempt OR complete_pass)` alongside `touchdown AND yardline_100
   <= 20`. Investigated the data before changing it: of 2,139 red-zone TDs
   under the old definition, 2,130 are on a rush/complete-pass play and 9
   are not (8 pick-six/INT-return TDs + 1 anomalous `field_goal`-typed row).
   Also confirmed `return_team` is **never** set on a row with
   `rush_attempt` or `complete_pass` true — so this filter has no
   edge-case leakage (e.g. a fumble-return TD off a live rush attempt,
   which would have `rush_attempt=true` and `return_team` set
   simultaneously, does not occur in this data). League-wide
   `SUM(rz_tds_allowed)` moved from 2,139 → 2,130 after the fix.

5. **`rush_yards_per_carry_allowed` and `yards_per_target_allowed_vs_*` are
   `NULL` (not 0)** when the denominator is 0 for that game, consistent with
   the existing `epa_avg` NULL convention elsewhere in this schema.

---

## Phase 2 — `agg_team_defense_season`

### Open assumptions / notes worth revisiting

1. **`avg_` naming convention.** Spec said "prefix with `avg_`" but the
   example (`avg_rush_yards_allowed_vs_rb_per_game`) also appended
   `_per_game`. Went with plain prefixing (no suffix) to match the terse
   naming already used elsewhere (e.g. `third_down_pct`). Two Phase 1
   columns already started with `avg_` (`avg_defenders_in_box`,
   `avg_time_to_throw`) — these are **not** double-prefixed into
   `avg_avg_defenders_in_box`; they pass through unchanged.

2. **"Per-game average" is a simple mean of each game's value, not a
   weighted sum/sum recomputation — including for columns that were already
   ratios in Phase 1.** For count-type columns (`rush_yards_allowed`, etc.),
   `avg_X` = total/games_played as specified. For columns that were already
   per-game ratios (`completion_pct_allowed`, `rush_yards_per_carry_allowed`,
   `yards_per_target_allowed_vs_*`, `*_coverage_rate`, `pressure_rate`), the
   same simple-mean rule was applied: **each game counts equally regardless
   of its sample size.** E.g. a 5-attempt game and a 40-attempt game weigh
   the same in the running average. This is the literal reading of the spec,
   but a possession-weighted alternative (recomputing from summed
   numerators/denominators) would behave differently, especially in
   small-sample early-season weeks. Worth deciding if this matters for prop
   modeling.

3. **Ranking columns (`rank_*`) are all sourced from count/sum-type metrics**
   (never NULL), and ties are handled via `RANK()` (skips subsequent ranks
   on a tie), per spec.

---

## Phase 3 — `agg_player_season_to_date`

### Open assumptions / notes worth revisiting

1. **RESOLVED (iteration 1).** ~~Rows only exist for weeks a player was
   active~~ — the table now has one row per `(player_id, season, week)` for
   **every week the player's team played**, not just active weeks. A new
   `is_active_week` column flags which rows are real (`true`) vs
   carried-forward (`false`). Gap rows (team played, player had zero
   qualifying involvement that game — e.g. healthy scratch, zero-snap
   inactive) carry forward the `l3_`/`l5_`/`szn_` values from the player's
   most recent active game via a group-based LOCF technique (portable
   across DuckDB versions, doesn't rely on `IGNORE NULLS` window support).
   Weeks before a player's first active game in a season are left `NULL`
   (nothing to carry forward yet) rather than fabricated. Bye weeks still
   produce no row at all (the team itself didn't play, so there's nothing
   to represent). Verified: total rows went from 14,731 (active-only) to
   22,763 (+8,032 carried-forward rows); a player's pre-debut weeks are
   `NULL`; a real gap week's carried-forward values exactly match the
   preceding active week's values.

   **New assumption introduced by this fix:** the full team-week grain is
   built from the *union* of every team a player is ever credited with in
   `agg_player_game` for that season (via `player_id, team` pairs). For a
   player traded mid-season, this means their row set spans both teams'
   full schedules, including some weeks technically before/after they were
   actually on that specific team (no roster-transaction-date data exists
   to slice this precisely). Low-impact in practice (affects only traded
   players), but worth knowing if a trade-season player's table looks like
   it has "too many" weeks.

2. **In the current data, `is_active` is `true` for 100% of `agg_player_game`
   rows** (verified: 14,731/14,731) — so the `is_active` filter is a no-op
   today. This isn't a bug in this table; it's because `agg_player_game`'s
   own row set is already built from players with a qualifying play or
   snap-count entry (see `ingest_aggregates.py`'s `keys` CTE), so an
   "inactive" row basically can't exist yet. Kept the filter anyway per
   spec and for correctness if that upstream assumption ever changes.

3. **`target_share` / `carry_share` averaging follows the same
   simple-mean-of-per-game-ratios convention established in Phase 2**
   (not a weighted sum/sum recomputation) — see Phase 2 note #2. Same
   tradeoff applies here: a 3-target game and a 12-target game count
   equally toward `l3_target_share`.

4. **`szn_red_zone_target_share_avg` / `szn_red_zone_rush_share_avg`
   require team-level red-zone target/carry totals per game, which don't
   exist in `agg_player_game`.** Computed them directly from `pbp_plays`
   inside this script (`team_rz` CTE — same logic the Phase 4 spec uses for
   `view_red_zone_target_share`/`view_red_zone_rush_share`). This means the
   same red-zone-team-totals logic now lives in two places (here and
   Phase 4's views); a future refactor could consolidate them into one
   view/CTE both phases reference, but I kept Phase 3 self-contained per
   the file-per-phase project structure.

5. **`l3`/`l5` windows correctly shrink for players early in a season** —
   verified week 1 = single game's raw value, week 2 = avg of 2 games,
   etc., growing until the window reaches full size. This is standard SQL
   window-frame clipping behavior, not special-cased in the code, and was
   explicitly spot-checked (Travis Kelce, 2024 weeks 1-7, matched a manual
   `AVG()` computed independently for weeks 2-4 and 1-4).

6. **Windows are computed within a single season only** — a player's week-1
   `l3`/`l5` in a new season does not pull trailing games from the prior
   season, consistent with the table's `(player_id, season, week)` grain.

---

## Phase 4 — Views (`view_red_zone_target_share`, `view_red_zone_rush_share`)

### Open assumptions / notes worth revisiting

1. **`pass_attempt = 1` / `rush_attempt = 1` from the spec were implemented
   as bare `pass_attempt` / `rush_attempt`**, since both are `BOOLEAN`
   columns in this schema (not 0/1 integers) — semantically identical,
   just idiomatic for the column type and consistent with the boolean-filter
   style used everywhere else in this codebase (Phases 1-3).

2. **The `JOIN` (inner join) to the per-team red-zone-totals subquery, as
   specified, does not drop any rows in practice** — verified both views
   return the full 14,731 `agg_player_game` rows. This holds because every
   team with an `agg_player_game` row for a given game necessarily has at
   least one play recorded in `pbp_plays` for that game, so a `team_rz` match
   always exists (even if `team_rz_targets`/`team_rz_carries` end up NULL —
   see #3). Flagging in case future data (e.g. a forfeited/partial game)
   ever breaks this invariant — an inner join would then silently drop
   affected players rather than surfacing a NULL share.

3. **Red-zone share is NULL (not 0 or an error) when a team had zero
   red-zone possessions that game** — verified 654 rows across both views
   hit this (team never got a pass/rush attempt inside the 20 that game).
   Consistent with the NULL-means-not-applicable convention used everywhere
   else in this schema.

4. **Manually verified share math sums correctly to 1.0 across a team's
   red-zone targets** — spot-checked 2024_01_JAX_MIA/MIA: two players each
   with 2 of the team's 4 red-zone targets, each showing `red_zone_target_
   share = 0.5`.

5. This view duplicates the `team_rz` CTE logic that's also embedded in
   Phase 3's `build_player_season.py` (see Phase 3 note #4) — three
   independent copies now exist in total (2 views + 1 script) doing the
   same per-game team red-zone-totals computation. Not a correctness issue
   since they compute identically and were each verified independently,
   but worth consolidating into one shared view/CTE if this layer keeps
   growing.

---

## Phase 5 — `queries.py`

### Open assumptions / notes worth revisiting

1. **"As of week W" resolves to the most recent available row at or before
   W, not an exact match.** This directly implements the fallback flagged
   as needed back in Phase 3 note #1: `agg_player_season_to_date` and
   `agg_team_defense_season` only have rows for weeks actually played, so
   an exact-match lookup would return empty for any bye week or any
   upcoming/unplayed week. Every profile-lookup function returns both
   `requested_week` and (`as_of_week` / `player_as_of_week` /
   `opp_as_of_week`) so a caller can see whether a fallback happened.
   Verified: querying Travis Kelce at week 6 (his 2024 bye) correctly
   resolves to his week-5 snapshot.

2. **Defined a `POSITION_STAT_MAP` for QB/RB/WR/TE** to answer "the
   relevant stat category" and "primary stat" language in the spec, since
   neither was fully spelled out. Primary stat per position: QB→
   `passing_yards`, RB→`rushing_yards`, WR/TE→`receiving_yards`, each tied
   to its corresponding defense `rank_*` column from Phase 2. RBs also
   carry receiving metrics (share of RB screen/checkdown usage), since
   modern RB props span both categories. A position outside QB/RB/WR/TE
   (or missing entirely) makes `get_prop_matchup`/`get_league_defensive_
   rankings` return an empty DataFrame rather than guessing.

3. **RESOLVED (iteration 1).** ~~`matchup_advantage` uses a hardcoded
   32-team league assumption~~ — `get_prop_matchup()` now takes a
   `bottom_pct` parameter (default `0.5`, preserving the original "bottom
   half" behavior). The cutoff is grounded in the actual pool a team's
   stored `rank_X` was computed against (`COUNT(DISTINCT defteam) FROM
   agg_team_defense_season WHERE season=? AND week=<team's own as-of
   week>`, via a new `_pool_size()` helper) rather than a hardcoded 32 —
   this also fixes the small-sample-pool caveat originally flagged here,
   since the threshold now scales with whatever pool actually exists at
   that native week. The output row now also includes `pool_size` so a
   caller can see the denominator used. Invalid `bottom_pct` (outside
   `(0, 1]`) raises `ValueError` rather than silently misbehaving.
   `get_league_defensive_rankings()` got a matching optional `bottom_pct`
   parameter (default `None` = return all teams) that filters its
   already-freshly-ranked output to the worst fraction — this one's
   denominator is the full, already-consistent pool being returned, so it
   doesn't have the stale-pool issue `get_prop_matchup`'s did. Verified
   both: `bottom_pct=0.9` flips a rank-16-of-32 matchup from
   `matchup_advantage=False` to `True`; `get_league_defensive_rankings(...,
   bottom_pct=0.25)` on a 32-team pool returns exactly 8 teams (the worst
   quarter, ranks 25-32).

4. **`get_league_defensive_rankings` recomputes rank fresh from each
   team's as-of snapshot rather than reusing the stored `rank_X` column.**
   Because byes stagger which native week each team's "most recent" data
   comes from, reusing Phase 2's pre-stored `rank_X` (computed within a
   single native week's cohort) would mix ranks computed against different
   cohorts on one leaderboard. Recomputing via `pandas.rank(method="min")`
   (same tie behavior as SQL `RANK()`) over the actually-assembled as-of
   rows avoids that. Verified with TE rankings at 2024 week 8: 32 teams
   returned, correctly sorted (Houston best at rank 1 with 24.0 yds/gm
   allowed to TEs; Kansas City worst at rank 32 with 80.9).

5. **Enriched several outputs with human-readable names** (`player_name`,
   `team_name`) beyond the columns each function's spec text named
   explicitly — done because the "Design requirements" section states the
   overarching goal is dashboard-ready output with "no raw SQL needed from
   the dashboard layer," and `get_league_defensive_rankings` explicitly
   required `team_name` already. Kept minimal: no enrichment beyond
   names/opponent/date that the spec already gestures at.

6. **`connect()` opens the database `read_only=True`.** Reasonable default
   for a query-only dashboard module and allows multiple concurrent
   readers, but means `queries.py` can't be used while something else
   holds an active read-write connection to the same file (e.g. `analytics/
   main.py` or the ingestion pipeline mid-run). Not an issue for typical
   usage (build, then query), but worth knowing if a future dashboard tries
   to query while a pipeline run is in flight.

7. **Every public function takes an optional `con=` parameter** (not
   in the spec) so a caller can reuse one open connection across multiple
   calls instead of opening/closing a new one each time — useful for a
   dashboard making several calls per page load. Defaults to `None`,
   which preserves the spec's "just call the function" simplicity via an
   internal `connect()`.

## Phase 6 — `analytics/main.py`

### Notes

1. **`queries.py` is deliberately not wired into `main.py`.** The spec's
   dependency-order list for this phase only named the four build steps
   (`build_defense_game` → `build_defense_season` → `build_player_season`
   → `build_views`); `queries.py` doesn't build/mutate anything, it's a
   read-side module, so there's nothing for `main.py` to run for it.

2. **Idempotency verified by running `analytics/main.py` twice back to
   back** — row counts were identical on both runs (`agg_team_defense_game`
   1,140 / `agg_team_defense_season` 1,140 / `agg_player_season_to_date`
   14,731 / both views 14,731), confirming the upserts don't duplicate and
   the views don't error on `CREATE OR REPLACE`.

3. **Ran the full validation checklist from the original spec end-to-end
   after the unified pipeline build** (not just per-phase, in case
   something regressed when run in the full dependency chain) — all 5
   items pass:
   - `agg_team_defense_game` has 1,140 rows ✓
   - `agg_team_defense_season` has a row for every (team, season, week) in
     `core_games` ✓ (0 missing)
   - Every `agg_player_season_to_date` row traces back to an
     `is_active=true` `agg_player_game` row ✓ (0 violations)
   - `get_prop_matchup()` returns a result for a known combination
     (Travis Kelce vs. LV, 2024 week 8) ✓
   - NGS `coverage_rate` columns are never NULL ✓ (0 violations)
