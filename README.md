# NFL Betting — Data Pipeline + Props Dashboard

## Layout

```
nfl_betting/              ingestion + analytics pipeline (see nfl_betting/db/ANALYTICS_HANDOFF.md
                           and nfl_betting/analytics/DASHBOARD_HANDOFF.md)
nfl_betting.duckdb         the database itself, committed to the repo (see .gitignore)
dashboard/                 Streamlit dashboard, read-only over nfl_betting.duckdb
```

## Running the dashboard locally

```bash
pip install -r dashboard/requirements.txt
streamlit run dashboard/app.py
```

## Updating the database

The dashboard reads a committed snapshot of `nfl_betting.duckdb` — there's
no live connection to a hosted database. After new games are played:

1. Run the ingestion pipeline: `python nfl_betting/main.py`
2. Rebuild the analytics layer: `python nfl_betting/analytics/main.py`
3. Commit and push the updated database:
   ```bash
   git add nfl_betting.duckdb
   git commit -m "DB update week N"
   git push
   ```
4. Streamlit Cloud redeploys automatically on push.

## Deploying to Streamlit Cloud

- Main file path: `dashboard/app.py`
- `dashboard/requirements.txt` and `dashboard/.streamlit/config.toml` are
  picked up automatically from the app's own directory.
- No secrets/config needed — `dashboard/config.py` locates the DB file
  relative to the repo root at runtime.
