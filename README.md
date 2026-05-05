# Atelier

Personal prompt workbench for iterating on Midjourney concept art. Write a prompt, get LLM coaching on it, generate in Midjourney, upload the result, describe what you want changed, and get a refined prompt back. Single-user, no auth.

## Stack

- **Backend:** FastAPI + SQLAlchemy 2.0 (async) + aiosqlite (SQLite)
- **Frontend:** Vanilla JS, no build step, served as static files
- **LLM coaching:** shells out to the `claude` CLI and streams tokens over SSE
- **Midjourney (optional v2):** Discord self-bot integration for in-app generation

## Layout

```
atelier/      FastAPI app (routers, services, models, schemas)
frontend/    Static HTML/CSS/JS
knowledge/   Bundled MJ skill files loaded at startup
data/        Runtime DB + uploaded images (gitignored)
scripts/     One-off migration and spike scripts
tests/       Pytest suite
```

## Setup

Requires Python 3.11+ and the `claude` CLI on `PATH`.

```bash
pip install -e .            # core
pip install -e '.[dev]'     # + tests
pip install -e '.[mj]'      # + Discord/Midjourney integration
```

Optional `.env` in the project root (all keys prefixed `ATELIER_`):

```
ATELIER_ANTHROPIC_API_KEY=...        # only if you wire the SDK directly
ATELIER_MJ_ENABLED=true              # opt into Discord MJ integration
ATELIER_MJ_DISCORD_TOKEN=...
ATELIER_MJ_CHANNEL_ID=...
ATELIER_MJ_GUILD_ID=...
```

## Run

```bash
python -m uvicorn atelier.main:app --reload --port 8000
```

Then open <http://localhost:8000>.

## Test

```bash
python -m pytest tests/ -v
```

Tests use a separate SQLite DB. Never point them at `data/atelier.db`.
