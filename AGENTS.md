# Working on FinCall

Guidance for contributors and coding agents.

## Run and test

- Backend (`backend/`, Python 3.12, uv):
  - `uv run --extra voice pytest -q` runs the whole suite in memory mode (no database, no keys).
  - `uv run ruff check src tests evals` and `uv run mypy src` (strict) must stay clean.
  - `BC_REPOSITORY=memory uv run building-call` starts the API on :8000 without PostgreSQL.
  - With PostgreSQL: `docker compose up -d db`, then `uv run alembic upgrade head`.
- Frontend (`frontend/`): pnpm only (`npm` is blocked by a preinstall script).
  `corepack pnpm run dev | build | lint | test`. The dev server proxies `/api` to :8000.
- Measure before and after any prompt or model change:
  `uv run python evals/compare_models.py --models qwen3.5:4b --verbose` (accuracy + p50/p95).

## Conventions

- Conventional commit messages (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`).
- Code and docs in English; what the agent says and the console text stay in Spanish (translated
  per language in `backend/src/building_call/i18n/locales/*.toml` and `frontend/src/i18n/`).
- The LLM interprets, the code decides: at most one LLM request per caller turn, flat JSON with
  full keys, one line, no nulls, validated in code. Keep the deterministic paths (yes/no, short
  addresses, apartments, plain names, whole set phrases) free of model calls.
- `next_step` alone decides the order of questions; graph nodes never chain each other. When a
  caller says something the graph does not handle, add the case to
  `backend/evals/gold_interpretation.jsonl` and to `backend/tests/test_naturalness.py` first.
- Integrations (n8n, observability) are best-effort, asynchronous, with a timeout: their failure
  never breaks a call.
- Secrets only through environment variables (`backend/.env` is git-ignored); never commit keys,
  tokens or real endpoints. Use fictional people, addresses and phone numbers in tests.
- Database changes go through a new Alembic migration.
