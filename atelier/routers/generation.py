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
