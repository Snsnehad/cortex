# Development docs

Local setup details, dev scripts (TASK-01.08), worker operations, troubleshooting.

## Backend tooling (TASK-01.02)

All Python work goes through [uv](https://docs.astral.sh/uv/); you never need a system Python. Run from `backend/`:

| Command | What it does |
|---|---|
| `uv sync` | Create/update `backend/.venv` exactly as pinned in `uv.lock` (Python 3.12 is installed by uv if missing) |
| `uv run ruff check` | Lint (pycodestyle, pyflakes, bugbear, bandit security rules, import order, …) |
| `uv run ruff check --fix` | Lint and apply safe auto-fixes |
| `uv run ruff format` | Format code (`--check` to verify only, as CI does) |
| `uv run mypy` | Strict type check of `app/` and `tests/` |
| `uv run pytest` | Run the test suite |
| `uv run pytest -m "not integration"` | Skip tests that need Docker services |
| `uv add <pkg>` / `uv add --dev <pkg>` | Add a runtime / dev dependency (updates `pyproject.toml` and `uv.lock`; commit both) |

All tool configuration lives in `backend/pyproject.toml`.

### Conventions enforced by tooling
- Python is pinned to 3.12 (`requires-python`, `.python-version`, and a test).
- Every directory under `app/` must be a regular package with a docstring stating its responsibility (`tests/unit/test_project_layout.py`).
- Warnings are errors in tests (`filterwarnings = ["error"]`), so deprecations are fixed when they first appear.
- Test markers are strict: `integration`, `slow`, `eval`, `llm`. An unknown marker fails the run.

### Windows notes
- uv's cache is on C: and the project is on D:, so `link-mode = "copy"` is set in `pyproject.toml` (hardlinks cannot cross drives).

## Configuration (TASK-01.03)

Settings live in `backend/app/config/settings.py`; use them via `from app.config import get_settings`.

- Every setting is a `CORTEX_*` environment variable; nested groups use `__`, e.g. `CORTEX_DATABASE__PASSWORD`. Lists are JSON: `CORTEX_CORS_ORIGINS=["http://localhost:5173"]`.
- Local development: `Copy-Item .env.example .env` at the repo root. Real environment variables override `.env`.
- **Unknown `CORTEX_*` variables are an error** (typo protection), from the environment or `.env`.
- `staging` / `production` refuse to start with development defaults. The error lists every problem at once.
- Secrets are `SecretStr`: they print as `**********`. Call `.get_secret_value()` only where the raw value is needed.
- **Adding a setting:** add the field to the right group, document it in `.env.example` (commented `# CORTEX_...=` if optional), and add validation tests. `test_env_example_documents_every_setting_and_nothing_else` fails until `.env.example` matches.

Generate a real secret for any shared environment:

```powershell
uv run python -c "import secrets; print(secrets.token_urlsafe(48))"
```
