# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Pre-game NHL win probabilities for every 2026-27 game, logged before puck drop and scored afterward. Later: logistic regression / gradient boosting models, a season and playoff simulator for Stanley Cup odds, and a final evaluation report.

## Working with the user

- Never commit or push without explicit permission, each time. Suggest `git add` commands and commit messages instead.
- Ask before making changes beyond what was requested.
- This is an ML learning project. The user has a software background and beginner-to-intermediate Python. Briefly explain design choices in chat, not in code comments.
- Keep code comments and docstrings minimal: only where something is genuinely non-obvious.
- `reports/results.md` is a concise log of key decisions and results. Never rewrite a logged result; add a dated note instead.

## Commands

Python 3.12, managed with uv. Run everything from the repo root.

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest                                      # db tests need TEST_DATABASE_URL=.../nhl_test
uv run pytest -m "not db"                          # skip tests that need a database
uv run pytest tests/test_elo.py::test_probability_by_hand
scripts/reset_test_db.sh                           # recreate nhl_test; rerun after adding a migration

uv run nhl-forecast fetch-schedule                 # store raw schedule weeks, 2015-16 to now
uv run nhl-forecast parse-schedule                 # rebuild teams and games from raw responses
uv run nhl-forecast validate-games                 # exits 1 on any failed check
uv run nhl-forecast backtest-elo                   # tune Elo variants; never loads 2025-26
uv run nhl-forecast freeze-elo --version elo-v1    # register a model; needs a clean git tree
uv run nhl-forecast daily                          # what the scheduled job runs
uv run nhl-forecast score-live                     # live scorecard per model
uv run python scripts/make_elo_figures.py          # writes reports/figures/
```

`DATABASE_URL` defaults to local Supabase (`supabase start`, port 54322). To target prod, set it to the `PROD_DATABASE_URL` value in `.env`.

## How the pieces fit

- **Ingest:** `NhlClient` (rate-limited, retried, no database access) fetches JSON; `store_raw` saves every response to `raw_api_responses`, deduplicated by URL and a hash of canonical JSON. `fetch_schedule` walks each season week by week via `nextStartDate` and skips weeks whose stored copy is complete.
- **Parse:** `load_schedules` reads the latest raw copy of each week, parses it with pure functions in `nhl_api/parse.py`, and upserts `teams` and `games`. Parsing never deletes games.
- **Elo has no stored state.** `run_elo` replays every game from 2015-16 in start-time order on each run (well under a second). `ratings_history` and `backtest_predictions` are outputs, rebuilt freely; nothing reads ratings back.
- **Models:** `MODELS` in `eval/freeze.py` defines each model's settings for registration only. After `freeze-elo`, the `model_versions` row is what counts: `daily` predicts with every `primary` and `shadow` model, using the hyperparameters stored there.
- **Live:** `jobs/live.py` predicts games starting in the next 36 hours and skips a game if its probability is unchanged since that model's last prediction. Scoring uses each model's latest prediction made before puck drop.
- **Evaluation:** `eval/backtest.py` loads only final games through 2024-25. Compare models on the same games with `paired_bootstrap`.

## Conventions

- Teams are keyed by NHL team id, never abbreviation. `rating_lineage` maps 53 ARI -> 59 Utah Hockey Club -> 68 Utah Mammoth.
- `game_type` 2 = regular season, 3 = playoffs (preseason is not stored). `home_win` is a generated column. Shootout final scores include the winner's extra goal.
- Seasons: 2015-16 Elo warm-up (never scored); train 2016-17 to 2023-24; validate 2024-25; 2025-26 test, already run once (do not re-test or tune on it); 2026-27 live since 2026-11-09.
- `elo-v1` (K 9.5, HFA 30) is primary; `elo-margin-v1` is a shadow model, promoted only if it beats the primary on live log loss. Single-season log loss can swing by 0.02.

## Database rules

- Predictions are immutable: the database stamps `created_at`, rejects inserts at or after puck drop, and blocks update, delete and truncate.
- Prod has run every migration in `supabase/migrations/`. Never edit an existing migration; add a new file. Every new table enables RLS in its own migration. No RLS policies exist yet, so the public API can read nothing.
- Tests only run against a database whose name ends in `_test` (enforced in `tests/conftest.py`). Never run `supabase db reset` on the dev database: it wipes ingested data.
- Prod connects through the Supabase session pooler (the direct host is IPv6-only); `db.py` sets `prepare_threshold=None`.

## Live job

- `.github/workflows/daily.yml` runs at 14:00 and 16:00 UTC, plus manual runs, using the `production` environment secret `DATABASE_URL`. Validation failures still write predictions, then fail the run.
- GitHub pauses scheduled workflows after 60 days without a commit; re-enable from the Actions tab.
- `/v1/partner-game/{CA|US}/now` returns in-game odds once a game starts; use `lastUpdatedUTC` to keep pre-game snapshots.

## Dev environment

- Development happens in WSL2 Ubuntu (`~/nhl-forecast`). Windows Smart App Control blocks unsigned binaries such as uv-managed Python and the Supabase CLI.
- `.env` (gitignored) has Windows line endings: strip `\r` when reading it from a shell.

## Status

Done: historical ingest and validation, Elo baseline (`reports/results.md`), live daily predictions. Next: MoneyPuck benchmark capture, a web UI for current probabilities, then logistic regression / gradient boosting (rest, back-to-backs, rolling xG share, starting goalies) as shadow models. Later: season and playoff simulator with daily Cup odds.
