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

Step 1 also pulls the current week's injury report. Teams publish their
game-day designations (Out / Doubtful / Questionable) through the week,
mostly Friday, so re-run steps 1–4 later in the week for fresher injury
status; early in the week the report is short or empty.

## Deploying to Streamlit Cloud

- Main file path: `dashboard/app.py`
- `dashboard/requirements.txt` and `dashboard/.streamlit/config.toml` are
  picked up automatically from the app's own directory.
- `dashboard/config.py` locates the DB file relative to the repo root at
  runtime, so the dashboard itself needs no configuration.
- Optional: to enable the Injuries tab's AI analysis, add `ANTHROPIC_API_KEY`
  under the app's Settings → Secrets. Without it the tab still shows the
  injury report and just notes that AI analysis isn't configured. For local
  runs, put the same line in `dashboard/.streamlit/secrets.toml` (git-ignored).
