# Atelier — Claude Code Project Guide

## What is this
Personal Midjourney prompt workbench. Write prompts → get LLM coaching → generate in MJ (manually) → upload result → describe feedback → get refined prompt → iterate. Single user, no auth.

## Quick start
```bash
python -m uvicorn atelier.main:app --reload --port 8000
```
Open http://localhost:8000 in browser. Frontend is served as static files from `frontend/`.

## Stack
- **Backend:** FastAPI + SQLAlchemy 2.0 async + aiosqlite (SQLite)
- **Frontend:** Vanilla JS (no framework, no build step)
- **LLM coaching:** Shells out to `claude` CLI (not Anthropic API directly)

## Project structure
```
atelier/          # Python package
  main.py         # FastAPI app, lifespan, static mounts
  config.py       # Pydantic Settings
  database.py     # Async engine + session factory
  models.py       # SQLAlchemy ORM models
  schemas.py      # Pydantic request/response schemas
  dependencies.py # FastAPI DI (get_db, get_settings, get_knowledge)
  routers/        # API route handlers
  services/       # Business logic (coaching, tree ops)
frontend/         # Static HTML/CSS/JS served by FastAPI
knowledge/        # Bundled MJ skill files loaded at startup
data/             # Runtime data (gitignored) — DB + uploaded images
```

## Key patterns

### Async SQLAlchemy — ALWAYS eagerly load relationships
Every query that touches PromptNode.image MUST chain:
```python
selectinload(PromptNode.image).selectinload(Image.tags)
```
Failing to do this causes MissingGreenlet crashes when FastAPI serializes the response.
This applies to ALL routers and services that query PromptNode.

### Coaching service
- Shells out to `claude -p --output-format stream-json` as a subprocess
- Streams tokens via SSE (Server-Sent Events) to the frontend
- Each SSE token is JSON-encoded on the server, JSON-parsed on the client (to preserve newlines)

### Frontend routing
- Hash-based: `#/projects/{id}/nodes/{id}`
- Two markdown renderers: `renderMarkdownStreaming()` (inline-only, for live updates) and `renderMarkdown()` (full block-level, for final output)

## Rules

### NEVER delete data/atelier.db
This is the user's real database with actual project data. For testing, use a separate DB file or in-memory SQLite.

### No unnecessary frameworks or dependencies
Frontend is vanilla JS by design. Don't introduce React, Vue, build tools, etc.

### DI via FastAPI Depends()
All request-scoped resources (db session, settings, knowledge) come through dependency injection. Don't import singletons.

## Testing
```bash
python -m pytest tests/ -v
```
Tests should use a separate in-memory or temp-file SQLite database.
