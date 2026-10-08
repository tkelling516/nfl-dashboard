"""Populate external_injuries from nflverse's weekly injury reports (via
nfl_data_py) for the current week.

Only rows with a game-day designation (Out / Doubtful / Questionable) are
stored -- a practice-participation-only row (no designation yet, common
early in the week) carries nothing the dashboard shows. Early in a week the
designation list is therefore short or empty by design; it fills in as
teams publish their reports (mostly Friday), so re-run the pipeline then.

The table is replaced per (season, week) rather than appended to, so
re-running is idempotent and always reflects the latest published report.
Earlier weeks' rows are left untouched.
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import nfl_data_py as nfl
import pandas as pd

from db import get_connection

SOURCE = "nflverse"


def _current_season_week(con) -> tuple[int, int] | None:
    """Same rule the dashboard uses: the earliest (season, week) with a
    game on or after today, else the most recent week in core_games."""
    row = con.execute(
        "SELECT season, week FROM core_games WHERE game_date >= CURRENT_DATE ORDER BY season, week LIMIT 1"
    ).fetchone()
    if row is None:
        row = con.execute("SELECT season, week FROM core_games ORDER BY season DESC, week DESC LIMIT 1").fetchone()
    return row


def ingest_injuries(con, season: int | None = None, week: int | None = None) -> int:
    """Replace external_injuries rows for (season, week) -- defaulting to
    the current week -- with the latest designated injuries. Returns the
    number of rows written. Never raises on a missing/unavailable upstream
    file: injuries are supplementary, so a failure here shouldn't take the
    rest of the pipeline down with it.
    """
    if season is None or week is None:
        current = _current_season_week(con)
        if current is None:
            print("  no games in core_games yet -- skipping injuries")
            return 0
        season, week = current

    try:
        raw = nfl.import_injuries(years=[season])
    except Exception as exc:  # noqa: BLE001 -- upstream failures are many and varied
        print(f"  injuries unavailable for {season}: {exc}")
        return 0

    raw = raw[(raw["week"] == week) & raw["report_status"].notna()]

    injury_text = raw["report_primary_injury"].fillna("").str.strip()
    secondary = raw["report_secondary_injury"].fillna("").str.strip()
    injury_text = injury_text.where(secondary == "", injury_text + ", " + secondary)

    rows = pd.DataFrame({
        "player_name": raw["full_name"],
        "team": raw["team"],
        "status": raw["report_status"],
        "injury": injury_text.replace("", None),
        # The source file has no report timestamp, so this is the date the
        # snapshot was pulled -- "as of", not the date the NFL posted it.
        "report_date": date.today(),
        "source": SOURCE,
        "season": season,
        "week": week,
        "player_id": raw["gsis_id"],
        "position": raw["position"],
    })

    con.execute("BEGIN")
    try:
        con.execute("DELETE FROM external_injuries WHERE season = ? AND week = ?", [season, week])
        if not rows.empty:
            con.register("injuries_df", rows)
            con.execute("""
                INSERT INTO external_injuries
                    (player_name, team, status, injury, report_date, source, season, week, player_id, position)
                SELECT player_name, team, status, injury, report_date, source, season, week, player_id, position
                FROM injuries_df
            """)
            con.unregister("injuries_df")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    return len(rows)


if __name__ == "__main__":
    con = get_connection()
    print(f"external_injuries: wrote {ingest_injuries(con)} rows")
    con.close()
