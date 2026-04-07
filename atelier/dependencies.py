from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from atelier.config import Settings

if TYPE_CHECKING:
    from atelier.services.midjourney import MidjourneyService


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_knowledge(request: Request) -> dict[str, str]:
    return request.app.state.knowledge


def get_midjourney(request: Request) -> "MidjourneyService":
    """Return the active MidjourneyService or 503 if MJ is disabled.

    Routes that depend on this will return 503 when MJ is not configured,
    rather than the rest of the app refusing to start.
    """
    mj = request.app.state.midjourney
    if mj is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Midjourney generation is not enabled. Set ATELIER_MJ_ENABLED=true "
                "and provide ATELIER_MJ_DISCORD_TOKEN / ATELIER_MJ_CHANNEL_ID."
            ),
        )
    return mj
