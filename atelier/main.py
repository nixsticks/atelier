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
from atelier.routers import coaching, images, projects, prompts, tags


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

    yield

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

    frontend_dir = settings.project_root / "frontend"
    if frontend_dir.exists():
        app.mount("/static", NoCacheStaticFiles(directory=str(frontend_dir)), name="static")

    # Ensure image dir exists before mounting
    settings.image_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/images",
        StaticFiles(directory=str(settings.image_dir)),
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
