import logging
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload


from atelier.config import Settings
from atelier.dependencies import get_db, get_settings
from atelier.models import Image, PromptNode
from atelier.schemas import ImageResponse, ImageUpdate
from atelier.services.vision import describe_image

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects/{project_id}/nodes/{node_id}", tags=["images"])


async def _generate_description_task(
    session_factory: async_sessionmaker[AsyncSession],
    image_id: int,
    image_path: Path,
    claude_cli: str | None,
) -> None:
    """Background task: describe an image and save the result.

    Best-effort — failures are logged but never raised so upload always
    succeeds. The user can manually retry from the UI if this fails.
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
        # Don't overwrite a description the user already edited.
        if image.description:
            return
        image.description = description
        await session.commit()


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
        await db.flush()
        return node.image
    else:
        image = Image(prompt_node_id=node_id, filename=filename)
        db.add(image)
        await db.flush()
        await db.refresh(image)
        # Eagerly load tags to prevent lazy-load crash in async serialization
        result = await db.execute(
            select(Image)
            .where(Image.id == image.id)
            .options(selectinload(Image.tags))
        )
        return result.scalar_one()


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
        image = await _save_image(db, settings, node, project_id, node_id, data, ext)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Upload failed: {type(e).__name__}: {e}")

    # Clear any stale description from a previously-uploaded image on this node,
    # then schedule auto-description in the background.
    image.description = None
    await db.flush()
    background_tasks.add_task(
        _generate_description_task,
        request.app.state.session_factory,
        image.id,
        _image_dir(settings, project_id) / image.filename,
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

    image = await _save_image(db, settings, node, project_id, node_id, resp.content, ext)
    image.description = None
    await db.flush()
    background_tasks.add_task(
        _generate_description_task,
        request.app.state.session_factory,
        image.id,
        _image_dir(settings, project_id) / image.filename,
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
    image_path = _image_dir(settings, project_id) / node.image.filename
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
    path = _image_dir(settings, project_id) / node.image.filename
    if path.exists():
        path.unlink()
    await db.delete(node.image)
