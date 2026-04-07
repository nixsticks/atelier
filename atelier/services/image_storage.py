"""Shared image-write helpers used by both manual upload and MJ generation.

Pulled out of `atelier/routers/images.py` once a second caller (the
generation router) needed the same write path. Both routers go through
the exact same on-disk layout, the same `Image` row creation/update,
and the same auto-description background task.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from atelier.config import Settings
from atelier.models import Image, PromptNode
from atelier.services.vision import describe_image

logger = logging.getLogger(__name__)


def image_dir(settings: Settings, project_id: int) -> Path:
    d = settings.image_dir / str(project_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


async def save_image(
    db: AsyncSession,
    settings: Settings,
    node: PromptNode,
    project_id: int,
    node_id: int,
    data: bytes,
    ext: str,
) -> Image:
    filename = f"{node_id}{ext}"
    dest = image_dir(settings, project_id) / filename
    dest.write_bytes(data)

    if node.image:
        old_path = image_dir(settings, project_id) / node.image.filename
        if old_path.exists() and old_path != dest:
            old_path.unlink()
        node.image.filename = filename
        await db.flush()
        return node.image

    image = Image(prompt_node_id=node_id, filename=filename)
    db.add(image)
    await db.flush()
    await db.refresh(image)
    # Eagerly load tags so the caller can serialize without a lazy-load
    # crash in the async context.
    result = await db.execute(
        select(Image).where(Image.id == image.id).options(selectinload(Image.tags))
    )
    return result.scalar_one()


async def generate_description_task(
    session_factory: async_sessionmaker[AsyncSession],
    image_id: int,
    image_path: Path,
    claude_cli: str | None,
) -> None:
    """Background task: describe an image and save the result.

    Best-effort — failures are logged but never raised so the caller's
    flow always succeeds. Skips if a user-edited description already
    exists on the row.
    """
    try:
        description = await describe_image(image_path, claude_cli=claude_cli)
    except Exception:
        logger.exception("auto-description failed for image_id=%s", image_id)
        return
    if not description:
        logger.warning("auto-description returned nothing for image_id=%s", image_id)
        return
    async with session_factory() as session:
        image = await session.get(Image, image_id)
        if image is None:
            return
        if image.description:
            return
        image.description = description
        await session.commit()
