from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from atelier.config import Settings
from atelier.database import create_engine, create_session_factory
from atelier.models import Base
from atelier.routers import coaching, images, projects, prompts, tags


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
        app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")

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
            return FileResponse(str(index))
        return {"status": "atelier running", "docs": "/docs"}

    return app


app = create_app()
