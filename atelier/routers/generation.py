"""Midjourney generation endpoint.

Streams events from `MidjourneyService.generate()` to the client over
SSE, and on the terminal `done` event fetches the grid image, persists
it via the shared image-storage path, and triggers auto-description in
the background. The frontend ultimately consumes the same `Image` row
that manual uploads produce — generation is just a different way of
populating it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from atelier.config import Settings
from atelier.dependencies import get_db, get_midjourney, get_settings
from atelier.models import Image, PromptNode
from atelier.services.image_storage import (
    generate_description_task,
    image_dir,
    save_image,
)
from atelier.services.midjourney import GenerationEvent, MidjourneyService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/projects/{project_id}/nodes/{node_id}",
    tags=["generation"],
)


async def _get_node(
    db: AsyncSession, project_id: int, node_id: int
) -> PromptNode:
    result = await db.execute(
        select(PromptNode)
        .where(PromptNode.id == node_id, PromptNode.project_id == project_id)
        .options(selectinload(PromptNode.image).selectinload(Image.tags))
    )
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(404, "Node not found")
    return node


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _event_payload(event: GenerationEvent) -> dict:
    out: dict = {"type": event.type}
    if event.progress is not None:
        out["progress"] = event.progress
    if event.image_url is not None:
        out["image_url"] = event.image_url
    if event.message is not None:
        out["message"] = event.message
    return out


@router.post("/generate")
async def generate(
    project_id: int,
    node_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    mj: MidjourneyService = Depends(get_midjourney),
):
    """Generate an image for a node via Midjourney.

    Streams events as SSE: `queued` → `progress*` → `done` (with the
    persisted image's id and filename) or `error`. The terminal `done`
    event arrives only after the grid has been downloaded and saved, so
    the frontend can immediately render the local image without a
    second round-trip.
    """
    node = await _get_node(db, project_id, node_id)
    prompt_text = (node.prompt_text or "").strip()
    if not prompt_text:
        raise HTTPException(400, "Node has no prompt text to generate from")

    async def stream():
        try:
            async for event in mj.generate(prompt_text):
                if event.type == "done" and event.image_url:
                    # Replace the raw "done" event with one that also
                    # carries the local image id/filename, so the
                    # frontend doesn't need a separate fetch.
                    payload = await _ingest_grid(
                        db=db,
                        request=request,
                        settings=settings,
                        project_id=project_id,
                        node_id=node_id,
                        grid_url=event.image_url,
                        discord_message_id=event.discord_message_id,
                        discord_channel_id=event.discord_channel_id,
                    )
                    yield _sse(payload)
                    return

                yield _sse(_event_payload(event))

                if event.type == "error":
                    return
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


async def _ingest_grid(
    db: AsyncSession,
    request: Request,
    settings: Settings,
    project_id: int,
    node_id: int,
    grid_url: str,
    discord_message_id: str | None,
    discord_channel_id: str | None,
) -> dict:
    """Download the MJ grid, persist it, and schedule auto-description.

    Returns either a `done` payload (with image id, filename, source url)
    or an `error` payload. Always returns a dict — never raises into the
    SSE stream.
    """
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=60,
            headers={"User-Agent": "Atelier/0.1"},
        ) as client:
            resp = await client.get(grid_url)
        if resp.status_code != 200:
            return {
                "type": "error",
                "message": f"failed to fetch MJ grid (HTTP {resp.status_code})",
            }
    except httpx.RequestError as e:
        return {"type": "error", "message": f"failed to fetch MJ grid: {e}"}

    content_type = resp.headers.get("content-type", "")
    ext = mimetypes.guess_extension(content_type.split(";")[0]) or ".png"
    if ext == ".jpe":
        ext = ".jpg"

    try:
        # Re-fetch the node — the original instance was loaded before
        # the long-running stream started, and we want a fresh view of
        # any existing image relationship before overwriting it.
        node = await _get_node(db, project_id, node_id)
        image = await save_image(
            db, settings, node, project_id, node_id, resp.content, ext
        )
        # Clear any stale description from a prior image on this node.
        image.description = None
        # Tag this image as an MJ grid with its Discord provenance so the
        # frontend can expose quadrant actions (U1–U4 / V1–V4) that talk
        # to MidjourneyService via the original message.
        image.kind = "grid"
        image.discord_message_id = discord_message_id
        image.discord_channel_id = discord_channel_id
        await db.flush()
        # Commit explicitly so the row is durable before we yield the
        # done event — the dependency's final commit will be a no-op.
        await db.commit()
    except Exception as e:
        logger.exception("failed to persist MJ grid for node_id=%s", node_id)
        return {"type": "error", "message": f"failed to save MJ grid: {e}"}

    # Auto-description, fire-and-forget. Using asyncio.create_task
    # rather than FastAPI BackgroundTasks because we're already inside
    # the streaming response body, where BackgroundTasks behavior is
    # less predictable. The task takes a session_factory and creates
    # its own session, so it's safe to detach.
    asyncio.create_task(
        generate_description_task(
            request.app.state.session_factory,
            image.id,
            image_dir(settings, project_id) / image.filename,
            request.app.state.claude_cli,
        )
    )

    return {
        "type": "done",
        "image_url": grid_url,
        "image_id": image.id,
        "filename": image.filename,
    }


# ============================================================
# Image CDN URL — re-fetch a live Discord CDN URL for an MJ
# image so it can be used as a --cref / --sref reference in a
# new prompt. MJ CDN URLs have signed tokens that expire, so
# we can't persist them — fetch on demand each time.
# ============================================================


@router.get("/image/cdn-url")
async def get_image_cdn_url(
    project_id: int,
    node_id: int,
    db: AsyncSession = Depends(get_db),
    mj: MidjourneyService = Depends(get_midjourney),
):
    node = await _get_node(db, project_id, node_id)
    if not node.image:
        raise HTTPException(404, "No image on this node")
    if node.image.kind == "uploaded":
        raise HTTPException(
            400, "This image isn't from Midjourney — no CDN URL available"
        )
    if not node.image.discord_message_id or not node.image.discord_channel_id:
        raise HTTPException(
            400,
            "Image is missing Discord provenance — was it ingested before "
            "the Discord ID migration?",
        )
    try:
        url = await mj.fetch_attachment_url(
            node.image.discord_message_id, node.image.discord_channel_id
        )
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    return {"url": url}


# ============================================================
# Upscale / Variation — press U1..U4 or V1..V4 on a grid and
# ingest the result as a new child node.
# ============================================================


class QuadrantRequest(BaseModel):
    quadrant: int = Field(..., ge=1, le=4)


def _validate_grid_parent(parent: PromptNode) -> None:
    """Raise 400 if the node isn't a grid with Discord provenance."""
    if not parent.image or parent.image.kind not in ("grid", "variation"):
        raise HTTPException(
            400, "Upscale/variation is only available on MJ grid images."
        )
    if not parent.image.discord_message_id or not parent.image.discord_channel_id:
        raise HTTPException(
            400,
            "Grid is missing Discord provenance — was it generated before "
            "the upscale feature was enabled?",
        )


@router.post("/upscale")
async def upscale(
    project_id: int,
    node_id: int,
    body: QuadrantRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    mj: MidjourneyService = Depends(get_midjourney),
):
    """Upscale one quadrant of a grid and stream the result as a child."""
    parent = await _get_node(db, project_id, node_id)
    _validate_grid_parent(parent)

    async def stream():
        try:
            async for event in mj.upscale(
                parent.image.discord_message_id,
                parent.image.discord_channel_id,
                body.quadrant,
            ):
                if event.type == "done" and event.image_url:
                    payload = await _ingest_action(
                        db=db, request=request, settings=settings,
                        project_id=project_id, parent_node_id=node_id,
                        parent_prompt_text=parent.prompt_text,
                        child_name=f"U{body.quadrant}",
                        image_kind="upscale",
                        image_url=event.image_url,
                        discord_message_id=event.discord_message_id,
                        discord_channel_id=event.discord_channel_id,
                    )
                    yield _sse(payload)
                    return
                yield _sse(_event_payload(event))
                if event.type == "error":
                    return
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/variation")
async def variation(
    project_id: int,
    node_id: int,
    body: QuadrantRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    mj: MidjourneyService = Depends(get_midjourney),
):
    """Vary one quadrant of a grid and stream the result as a child.

    Variations produce a new 2x2 grid seeded by the chosen quadrant.
    The child node's image.kind is 'variation' and the quadrant strip
    will render on it so the user can upscale/vary from the new grid.
    """
    parent = await _get_node(db, project_id, node_id)
    _validate_grid_parent(parent)

    async def stream():
        try:
            async for event in mj.variation(
                parent.image.discord_message_id,
                parent.image.discord_channel_id,
                body.quadrant,
            ):
                if event.type == "done" and event.image_url:
                    payload = await _ingest_action(
                        db=db, request=request, settings=settings,
                        project_id=project_id, parent_node_id=node_id,
                        parent_prompt_text=parent.prompt_text,
                        child_name=f"V{body.quadrant}",
                        image_kind="variation",
                        image_url=event.image_url,
                        discord_message_id=event.discord_message_id,
                        discord_channel_id=event.discord_channel_id,
                    )
                    yield _sse(payload)
                    return
                yield _sse(_event_payload(event))
                if event.type == "error":
                    return
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


async def _ingest_action(
    db: AsyncSession,
    request: Request,
    settings: Settings,
    project_id: int,
    parent_node_id: int,
    parent_prompt_text: str,
    child_name: str,
    image_kind: str,
    image_url: str,
    discord_message_id: str | None,
    discord_channel_id: str | None,
) -> dict:
    """Download a MJ result, create a child node, persist the image.

    Shared by both upscale and variation endpoints. Child-node creation
    happens on `done` so MJ errors don't leave orphaned empty nodes.
    """
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=60,
            headers={"User-Agent": "Atelier/0.1"},
        ) as client:
            resp = await client.get(image_url)
        if resp.status_code != 200:
            return {
                "type": "error",
                "message": f"failed to fetch MJ {image_kind} (HTTP {resp.status_code})",
            }
    except httpx.RequestError as e:
        return {"type": "error", "message": f"failed to fetch MJ {image_kind}: {e}"}

    content_type = resp.headers.get("content-type", "")
    ext = mimetypes.guess_extension(content_type.split(";")[0]) or ".png"
    if ext == ".jpe":
        ext = ".jpg"

    try:
        child = PromptNode(
            project_id=project_id,
            parent_id=parent_node_id,
            name=child_name,
            prompt_text=parent_prompt_text,
        )
        db.add(child)
        await db.flush()
        await db.refresh(child, attribute_names=["image"])
        image = await save_image(
            db, settings, child, project_id, child.id, resp.content, ext
        )
        image.kind = image_kind
        image.discord_message_id = discord_message_id
        image.discord_channel_id = discord_channel_id
        image.description = None
        await db.flush()
        await db.commit()
    except Exception as e:
        logger.exception(
            "failed to persist MJ %s under parent node_id=%s",
            image_kind, parent_node_id,
        )
        return {"type": "error", "message": f"failed to save {image_kind}: {e}"}

    asyncio.create_task(
        generate_description_task(
            request.app.state.session_factory,
            image.id,
            image_dir(settings, project_id) / image.filename,
            request.app.state.claude_cli,
        )
    )

    return {
        "type": "done",
        "image_url": image_url,
        "image_id": image.id,
        "filename": image.filename,
        "node_id": child.id,
    }
