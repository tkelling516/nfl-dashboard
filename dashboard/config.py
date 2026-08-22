"""Dashboard configuration."""

from pathlib import Path

# The DuckDB file lives at the repo root (sibling of nfl_betting/), not
# inside nfl_betting/ -- see nfl_betting/config.py's DB_PATH, which resolves
# to the same place. Kept as an absolute Path (not a bare string) so the
# dashboard works regardless of the process's current working directory.
DB_PATH = Path(__file__).resolve().parent.parent / "nfl_betting.duckdb"

# No hardcoded season/week -- these are auto-detected from the DB (see
# queries_ext.get_current_week).
