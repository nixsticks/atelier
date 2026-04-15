import logging
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload


from atelier.config import Settings
from atelier.dependencies import get_db, get_settings
from atelier.models import Image, PromptNode
from atelier.schemas import ImageResponse, ImageUpdate
from atelier.services.image_storage import (
    generate_description_task,
    image_dir,
    save_image,
)
from atelier.services.vision import describe_image

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects/{project_id}/nodes/{node_id}", tags=["images"])


async def _get_node(db: AsyncSession, project_id: int, node_id: int) -> PromptNode:
    result = await db.execute(
        select(PromptNode)
        .where(PromptNode.id == node_id, PromptNode.project_id == project_id)
        .options(selectinload(PromptNode.image).selectinload(Image.tags))
    )
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(404, "Node not found")
    return node


@router.post("/image", response_model=ImageResponse, status_code=201)
async def upload_image(
    project_id: int,
    node_id: int,
    file: UploadFile,
    background_tasks: BackgroundTasks,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    node = await _get_node(db, project_id, node_id)

    # Determine extension: try filename, then content_type, fallback to .png
    ext = ""
    if file.filename:
        ext = Path(file.filename).suffix
    if not ext and file.content_type:
        ext = mimetypes.guess_extension(file.content_type) or ""
        if ext == ".jpe":
            ext = ".jpg"
    if not ext:
        ext = ".png"

    try:
        data = await file.read()
        if not data:
            raise HTTPException(400, "Empty file")
        image = await save_image(db, settings, node, project_id, node_id, data, ext)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Upload failed: {type(e).__name__}: {e}")

    # Clear any stale description + MJ provenance from a prior image on
    # this node. If the row was a grid, the old Discord ids point at a
    # Discord message that no longer represents this file on disk — the
    # quadrant action UI would send U1/U2 clicks to the wrong grid.
    image.description = None
    image.kind = "uploaded"
    image.discord_message_id = None
    image.discord_channel_id = None
    await db.flush()
    background_tasks.add_task(
        generate_description_task,
        request.app.state.session_factory,
        image.id,
        image_dir(settings, project_id) / image.filename,
        request.app.state.claude_cli,
    )
    return image


class ImageFromUrl(BaseModel):
    url: str


@router.post("/image/from-url", response_model=ImageResponse, status_code=201)
async def upload_image_from_url(
    project_id: int,
    node_id: int,
    body: ImageFromUrl,
    background_tasks: BackgroundTasks,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    node = await _get_node(db, project_id, node_id)

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=30,
        headers={"User-Agent": "Atelier/0.1"},
    ) as client:
        try:
            resp = await client.get(body.url)
        except httpx.RequestError as e:
            raise HTTPException(400, f"Failed to fetch image: {e}")
        if resp.status_code != 200:
            raise HTTPException(400, f"Failed to fetch image (HTTP {resp.status_code} from {urlparse(body.url).netloc})")

    content_type = resp.headers.get("content-type", "")
    ext = mimetypes.guess_extension(content_type.split(";")[0]) or ".png"
    if ext == ".jpe":
        ext = ".jpg"

    image = await save_image(db, settings, node, project_id, node_id, resp.content, ext)
    image.description = None
    image.kind = "uploaded"
    image.discord_message_id = None
    image.discord_channel_id = None
    await db.flush()
    background_tasks.add_task(
        generate_description_task,
        request.app.state.session_factory,
        image.id,
        image_dir(settings, project_id) / image.filename,
        request.app.state.claude_cli,
    )
    return image


@router.get("/image", response_model=ImageResponse)
async def get_image(
    project_id: int, node_id: int, db: AsyncSession = Depends(get_db)
):
    node = await _get_node(db, project_id, node_id)
    if not node.image:
        raise HTTPException(404, "No image for this node")
    return node.image


@router.patch("/image", response_model=ImageResponse)
async def update_image(
    project_id: int,
    node_id: int,
    body: ImageUpdate,
    db: AsyncSession = Depends(get_db),
):
    node = await _get_node(db, project_id, node_id)
    if not node.image:
        raise HTTPException(404, "No image for this node")
    if body.feedback is not None:
        node.image.feedback = body.feedback
    if body.description is not None:
        node.image.description = body.description
    await db.flush()
    await db.refresh(node.image)
    return node.image


@router.post("/image/describe", response_model=ImageResponse)
async def describe_image_endpoint(
    project_id: int,
    node_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Synchronously generate (or regenerate) the auto-description.

    Used as a manual fallback when background description failed or for
    images that predate the auto-description feature. Blocks for as long
    as the `claude` CLI takes (~30s typical, 120s max).
    """
    node = await _get_node(db, project_id, node_id)
    if not node.image:
        raise HTTPException(404, "No image for this node")
    image_path = image_dir(settings, project_id) / node.image.filename
    if not image_path.exists():
        raise HTTPException(404, f"Image file missing on disk: {node.image.filename}")

    description = await describe_image(
        image_path, claude_cli=request.app.state.claude_cli
    )
    if not description:
        raise HTTPException(
            502,
            "Failed to generate description — check server logs. "
            "The `claude` CLI may be unavailable, timed out, or returned an empty response.",
        )

    node.image.description = description
    await db.flush()
    await db.refresh(node.image)
    return node.image


@router.delete("/image", status_code=204)
async def delete_image(
    project_id: int,
    node_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    node = await _get_node(db, project_id, node_id)
    if not node.image:
        raise HTTPException(404, "No image for this node")
    path = image_dir(settings, project_id) / node.image.filename
    if path.exists():
        path.unlink()
    await db.delete(node.image)
