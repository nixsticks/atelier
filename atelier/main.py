from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

from atelier.config import Settings
from atelier.database import create_engine, create_session_factory
from atelier.models import Base
from atelier.routers import coaching, generation, images, projects, prompts, tags
from atelier.services.claude_cli import find_claude_cli


class NoCacheStaticFiles(StaticFiles):
    """StaticFiles that always tells the browser not to cache.

    Atelier is a local dev tool and we ship HTML/JS/CSS edits constantly.
    Browser caching here just causes confusion ("why don't I see my new
    feature?") so we force a fresh fetch on every request.
    """

    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response


def _load_knowledge(knowledge_dir: Path) -> dict[str, str]:
    knowledge = {}
    if knowledge_dir.exists():
        for f in sorted(knowledge_dir.glob("*.md")):
            knowledge[f.stem] = f.read_text()
    return knowledge


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.image_dir.mkdir(parents=True, exist_ok=True)

    engine = create_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Migrate: add name column to prompt_node if missing
        from sqlalchemy import text, inspect as sa_inspect
        def _migrate(connection):
            node_columns = [c["name"] for c in sa_inspect(connection).get_columns("prompt_node")]
            if "name" not in node_columns:
                connection.execute(text("ALTER TABLE prompt_node ADD COLUMN name VARCHAR(255)"))
            image_columns = [c["name"] for c in sa_inspect(connection).get_columns("image")]
            if "description" not in image_columns:
                connection.execute(text("ALTER TABLE image ADD COLUMN description TEXT"))
        await conn.run_sync(_migrate)

    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.knowledge = _load_knowledge(settings.knowledge_dir)

    # Resolve `claude` CLI to an absolute path so subprocess calls work
    # even when uvicorn was launched without the user's interactive shell
    # PATH (e.g. from VS Code, launchd, a non-login subshell).
    claude_path = find_claude_cli()
    app.state.claude_cli = claude_path
    if claude_path:
        print(f"[atelier] using claude CLI at: {claude_path}")
    else:
        print(
            "[atelier] WARNING: `claude` CLI not found. "
            "Coaching and auto-description will fail until it's installed "
            "and on PATH (or in ~/.local/bin)."
        )

    # Midjourney Discord client (optional, off by default).
    app.state.midjourney = None
    if settings.mj_enabled:
        if not (settings.mj_discord_token and settings.mj_channel_id):
            print(
                "[atelier] WARNING: ATELIER_MJ_ENABLED=true but "
                "ATELIER_MJ_DISCORD_TOKEN / ATELIER_MJ_CHANNEL_ID are unset; "
                "Midjourney generation disabled."
            )
        else:
            try:
                from atelier.services.midjourney import MidjourneyService
                mj = MidjourneyService(
                    token=settings.mj_discord_token,
                    channel_id=settings.mj_channel_id,
                    guild_id=settings.mj_guild_id,
                    timeout=settings.mj_timeout_seconds,
                )
                await mj.start()
                app.state.midjourney = mj
                print("[atelier] Midjourney Discord client connected")
            except Exception as e:
                print(f"[atelier] WARNING: Midjourney startup failed: {e}")

    yield

    if app.state.midjourney is not None:
        await app.state.midjourney.stop()
    await engine.dispose()


def create_app() -> FastAPI:
    settings = Settings()

    app = FastAPI(title="Atelier", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(projects.router)
    app.include_router(prompts.router)
    app.include_router(images.router)
    app.include_router(tags.router)
    app.include_router(coaching.router)
    app.include_router(generation.router)

    frontend_dir = settings.project_root / "frontend"
    if frontend_dir.exists():
        app.mount("/static", NoCacheStaticFiles(directory=str(frontend_dir)), name="static")

    # Ensure image dir exists before mounting.
    # Use NoCacheStaticFiles so a re-uploaded image with the same filename
    # (e.g. after a crop or regeneration) refreshes immediately instead of
    # being served from the browser cache.
    settings.image_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/images",
        NoCacheStaticFiles(directory=str(settings.image_dir)),
        name="images",
    )

    @app.get("/")
    async def root():
        index = frontend_dir / "index.html"
        if index.exists():
            return FileResponse(
                str(index),
                headers={
                    "Cache-Control": "no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )
        return {"status": "atelier running", "docs": "/docs"}

    return app


app = create_app()
