-- NFL betting analytics schema (DuckDB).
-- All tables are idempotent (IF NOT EXISTS) so this file can be re-run safely.

CREATE TABLE IF NOT EXISTS core_teams (
    team_id     VARCHAR PRIMARY KEY,
    team_abbr   VARCHAR,
    team_name   VARCHAR
);

CREATE TABLE IF NOT EXISTS core_players (
    player_id               VARCHAR PRIMARY KEY,
    player_name             VARCHAR,
    player_name_normalized  VARCHAR,
    position                VARCHAR
);

CREATE TABLE IF NOT EXISTS core_games (
    game_id     VARCHAR PRIMARY KEY,
    game_date   DATE,
    season      INTEGER,
    week        INTEGER,
    home_team   VARCHAR,
    away_team   VARCHAR,
    spread_line DOUBLE,
    total_line  DOUBLE,
    roof        VARCHAR,
    temp        DOUBLE,
    wind        DOUBLE,
    div_game    BOOLEAN
);

-- Migration for pre-existing databases created before these columns existed.
ALTER TABLE core_games ADD COLUMN IF NOT EXISTS spread_line DOUBLE;
ALTER TABLE core_games ADD COLUMN IF NOT EXISTS total_line DOUBLE;
ALTER TABLE core_games ADD COLUMN IF NOT EXISTS roof VARCHAR;
ALTER TABLE core_games ADD COLUMN IF NOT EXISTS temp DOUBLE;
ALTER TABLE core_games ADD COLUMN IF NOT EXISTS wind DOUBLE;
ALTER TABLE core_games ADD COLUMN IF NOT EXISTS div_game BOOLEAN;

CREATE TABLE IF NOT EXISTS pbp_plays (
    game_id             VARCHAR,
    play_id             DOUBLE,
    drive               DOUBLE,
    posteam             VARCHAR,
    defteam             VARCHAR,
    down                DOUBLE,
    ydstogo             DOUBLE,
    yardline_100        DOUBLE,
    play_type           VARCHAR,
    yards_gained        DOUBLE,
    epa                 DOUBLE,
    wp                  DOUBLE,
    passer_player_id    VARCHAR,
    rusher_player_id    VARCHAR,
    receiver_player_id  VARCHAR,
    air_yards           DOUBLE,
    first_down          BOOLEAN,
    game_date           DATE,
    season              INTEGER,
    week                INTEGER,
    touchdown           BOOLEAN,
    td_player_id        VARCHAR,
    complete_pass        BOOLEAN,
    pass_attempt         BOOLEAN,
    rush_attempt         BOOLEAN,
    return_team           VARCHAR,
    penalty                BOOLEAN,
    route                  VARCHAR,
    offense_personnel      VARCHAR,
    defenders_in_box        DOUBLE,
    was_pressure             BOOLEAN,
    time_to_throw             DOUBLE,
    defense_coverage_type      VARCHAR,
    two_point_attempt          BOOLEAN,
    goal_to_go                 BOOLEAN,
    PRIMARY KEY (game_id, play_id)
);

-- Migration for pre-existing databases created before these columns existed.
ALTER TABLE pbp_plays ADD COLUMN IF NOT EXISTS route VARCHAR;
ALTER TABLE pbp_plays ADD COLUMN IF NOT EXISTS offense_personnel VARCHAR;
ALTER TABLE pbp_plays ADD COLUMN IF NOT EXISTS defenders_in_box DOUBLE;
ALTER TABLE pbp_plays ADD COLUMN IF NOT EXISTS was_pressure BOOLEAN;
ALTER TABLE pbp_plays ADD COLUMN IF NOT EXISTS time_to_throw DOUBLE;
ALTER TABLE pbp_plays ADD COLUMN IF NOT EXISTS defense_coverage_type VARCHAR;
ALTER TABLE pbp_plays ADD COLUMN IF NOT EXISTS two_point_attempt BOOLEAN;
ALTER TABLE pbp_plays ADD COLUMN IF NOT EXISTS goal_to_go BOOLEAN;

CREATE TABLE IF NOT EXISTS agg_player_game (
    player_id             VARCHAR,
    game_id               VARCHAR,
    season                INTEGER,
    week                  INTEGER,
    team                  VARCHAR,
    position              VARCHAR,
    is_active             BOOLEAN,
    player_type           VARCHAR,
    targets               INTEGER,
    carries               INTEGER,
    pass_attempts         INTEGER,
    team_targets          INTEGER,
    team_carries          INTEGER,
    team_pass_attempts    INTEGER,
    total_rush_yards      INTEGER,
    total_receiving_yards INTEGER,
    total_receptions      INTEGER,
    touchdowns            INTEGER,
    passing_yards         INTEGER,
    yards                 INTEGER,
    epa_avg               DOUBLE,
    red_zone_targets      INTEGER,
    red_zone_carries      INTEGER,
    red_zone_attempts     INTEGER,
    air_yards             DOUBLE,
    snap_count            INTEGER,
    snap_pct              DOUBLE,
    routes_run            INTEGER,
    PRIMARY KEY (player_id, game_id)
);

CREATE TABLE IF NOT EXISTS agg_team_game (
    team                     VARCHAR,
    game_id                  VARCHAR,
    season                   INTEGER,
    week                     INTEGER,
    total_plays              INTEGER,
    pass_plays               INTEGER,
    run_plays                INTEGER,
    total_pass_yards         INTEGER,
    total_rush_yards         INTEGER,
    third_down_attempts      INTEGER,
    third_down_conversions   INTEGER,
    third_down_pct           DOUBLE,
    red_zone_trips           INTEGER,
    red_zone_plays           INTEGER,
    red_zone_pass_plays      INTEGER,
    red_zone_run_plays       INTEGER,
    fourth_down_attempts     INTEGER,
    fourth_down_conversions  INTEGER,
    avg_epa                  DOUBLE,
    PRIMARY KEY (team, game_id)
);

-- Betting data ingestion is deferred. Tables exist for schema completeness only.
CREATE TABLE IF NOT EXISTS betting_game_odds (
    game_id     VARCHAR,
    sportsbook  VARCHAR,
    market_type VARCHAR,
    home_odds   DOUBLE,
    away_odds   DOUBLE,
    spread      DOUBLE,
    total       DOUBLE,
    timestamp   TIMESTAMP
);

CREATE TABLE IF NOT EXISTS betting_player_props (
    player_name VARCHAR,
    game_id     VARCHAR,
    sportsbook  VARCHAR,
    prop_type   VARCHAR,
    line        DOUBLE,
    over_odds   DOUBLE,
    under_odds  DOUBLE,
    timestamp   TIMESTAMP
);

CREATE TABLE IF NOT EXISTS external_injuries (
    player_name VARCHAR,
    team        VARCHAR,
    status      VARCHAR,
    injury      VARCHAR,
    report_date DATE,
    source      VARCHAR
);

CREATE TABLE IF NOT EXISTS external_news (
    player_name  VARCHAR,
    team         VARCHAR,
    headline     VARCHAR,
    summary      VARCHAR,
    source       VARCHAR,
    published_at TIMESTAMP
);
