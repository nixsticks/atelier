import mimetypes
import shutil
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from atelier.config import Settings
from atelier.dependencies import get_db, get_settings
from atelier.models import Image, PromptNode
from atelier.schemas import ImageFeedbackUpdate, ImageResponse

router = APIRouter(prefix="/api/projects/{project_id}/nodes/{node_id}", tags=["images"])


def _image_dir(settings: Settings, project_id: int) -> Path:
    d = settings.image_dir / str(project_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


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


async def _save_image(
    db: AsyncSession,
    settings: Settings,
    node: PromptNode,
    project_id: int,
    node_id: int,
    data: bytes,
    ext: str,
) -> Image:
    filename = f"{node_id}{ext}"
    dest = _image_dir(settings, project_id) / filename
    dest.write_bytes(data)

    if node.image:
        old_path = _image_dir(settings, project_id) / node.image.filename
        if old_path.exists() and old_path != dest:
            old_path.unlink()
        node.image.filename = filename
    else:
        image = Image(prompt_node_id=node_id, filename=filename)
        db.add(image)

    await db.flush()
    refreshed = await _get_node(db, project_id, node_id)
    return refreshed.image


@router.post("/image", response_model=ImageResponse, status_code=201)
async def upload_image(
    project_id: int,
    node_id: int,
    file: UploadFile,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    node = await _get_node(db, project_id, node_id)
    ext = Path(file.filename).suffix if file.filename else ".png"
    data = await file.read()
    return await _save_image(db, settings, node, project_id, node_id, data, ext)


class ImageFromUrl(BaseModel):
    url: str


@router.post("/image/from-url", response_model=ImageResponse, status_code=201)
async def upload_image_from_url(
    project_id: int,
    node_id: int,
    body: ImageFromUrl,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    node = await _get_node(db, project_id, node_id)

    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        resp = await client.get(body.url)
        if resp.status_code != 200:
            raise HTTPException(400, f"Failed to fetch image (HTTP {resp.status_code})")

    content_type = resp.headers.get("content-type", "")
    ext = mimetypes.guess_extension(content_type.split(";")[0]) or ".png"
    if ext == ".jpe":
        ext = ".jpg"

    return await _save_image(db, settings, node, project_id, node_id, resp.content, ext)


@router.get("/image", response_model=ImageResponse)
async def get_image(
    project_id: int, node_id: int, db: AsyncSession = Depends(get_db)
):
    node = await _get_node(db, project_id, node_id)
    if not node.image:
        raise HTTPException(404, "No image for this node")
    return node.image


@router.patch("/image", response_model=ImageResponse)
async def update_image_feedback(
    project_id: int,
    node_id: int,
    body: ImageFeedbackUpdate,
    db: AsyncSession = Depends(get_db),
):
    node = await _get_node(db, project_id, node_id)
    if not node.image:
        raise HTTPException(404, "No image for this node")
    node.image.feedback = body.feedback
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
    path = _image_dir(settings, project_id) / node.image.filename
    if path.exists():
        path.unlink()
    await db.delete(node.image)
