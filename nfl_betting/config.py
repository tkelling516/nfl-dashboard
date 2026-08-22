"""Central configuration for the NFL betting data pipeline."""

from pathlib import Path

# Seasons to ingest. 2026 will simply return partial/empty data until the
# season kicks off in September 2026 — nfl_data_py handles that gracefully.
SEASONS = [2024, 2025, 2026]

# Positions the pipeline cares about. Kickers, punters, and linemen are
# out of scope for player-prop analytics.
SKILL_POSITIONS = ["QB", "RB", "WR", "TE"]

PROJECT_ROOT = Path(__file__).resolve().parent
DB_PATH = PROJECT_ROOT.parent / "nfl_betting.duckdb"
SCHEMA_PATH = PROJECT_ROOT / "db" / "schema.sql"
