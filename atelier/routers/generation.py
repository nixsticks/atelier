"""Midjourney generation endpoints.

Each endpoint (generate, upscale, variation) starts a background task
that runs the MJ interaction to completion and saves the image on done,
regardless of whether the SSE client is still connected. This means a
page refresh mid-generation no longer loses the image — the task keeps
running, saves the result, and the user sees it on next page load.

The SSE response just tails the task's event stream. If the client
disconnects, the subscriber is removed but the task is unaffected.
Reconnecting clients (or new requests for the same node while the task
is in flight) get all accumulated events replayed, then live-tail.
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
from dataclasses import dataclass, field

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
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


# ============================================================
# Background generation jobs — survive client disconnects
# ============================================================


@dataclass
class GenerationJob:
    """Tracks an in-flight MJ generation/upscale/variation.

    The background task pushes events via `broadcast()`. SSE handlers
    subscribe via `tail()` which replays accumulated events then
    live-tails. Client disconnects just remove the subscriber queue —
    the task keeps running.
    """
    events: list[dict] = field(default_factory=list)
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None

    def broadcast(self, payload: dict) -> None:
        self.events.append(payload)
        for q in list(self.subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass


def _get_jobs(request: Request) -> dict[str, GenerationJob]:
    jobs = getattr(request.app.state, "generation_jobs", None)
    if jobs is None:
        jobs = {}
        request.app.state.generation_jobs = jobs
    return jobs


def _tail_job(job: GenerationJob) -> StreamingResponse:
    """Return an SSE response that tails a GenerationJob."""
    async def stream():
        q: asyncio.Queue[dict] = asyncio.Queue()
        # Replay events that already happened (for reconnecting clients).
        for e in list(job.events):
            await q.put(e)
        job.subscribers.append(q)
        try:
            while True:
                payload = await q.get()
                yield _sse(payload)
                if payload.get("type") in ("done", "error"):
                    return
        except asyncio.CancelledError:
            # Client disconnected — task keeps running.
            pass
        finally:
            if q in job.subscribers:
                job.subscribers.remove(q)
            yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


# ============================================================
# Generate — /imagine
# ============================================================


@router.post("/generate")
async def generate(
    project_id: int,
    node_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    mj: MidjourneyService = Depends(get_midjourney),
):
    node = await _get_node(db, project_id, node_id)
    prompt_text = (node.prompt_text or "").strip()
    if not prompt_text:
        raise HTTPException(400, "Node has no prompt text to generate from")

    jobs = _get_jobs(request)
    key = f"generate:{project_id}:{node_id}"

    # If a job is already running for this node, tail it.
    existing = jobs.get(key)
    if existing and not existing.done.is_set():
        return _tail_job(existing)

    job = GenerationJob()
    jobs[key] = job

    session_factory = request.app.state.session_factory
    claude_cli = request.app.state.claude_cli

    async def run():
        try:
            async for event in mj.generate(prompt_text):
                if event.type == "done" and event.image_url:
                    payload = await _ingest_grid(
                        session_factory=session_factory,
                        settings=settings,
                        claude_cli=claude_cli,
                        project_id=project_id,
                        node_id=node_id,
                        grid_url=event.image_url,
                        discord_message_id=event.discord_message_id,
                        discord_channel_id=event.discord_channel_id,
                    )
                    job.broadcast(payload)
                    return
                job.broadcast(_event_payload(event))
                if event.type == "error":
                    return
        except Exception as e:
            logger.exception("generation task failed for node_id=%s", node_id)
            job.broadcast({"type": "error", "message": str(e)})
        finally:
            job.done.set()
            await asyncio.sleep(120)
            jobs.pop(key, None)

    job.task = asyncio.create_task(run())
    return _tail_job(job)


# ============================================================
# Image CDN URL
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
# Upscale / Variation
# ============================================================


class QuadrantRequest(BaseModel):
    quadrant: int = Field(..., ge=1, le=4)


def _validate_grid_parent(parent: PromptNode) -> None:
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
    parent = await _get_node(db, project_id, node_id)
    _validate_grid_parent(parent)

    jobs = _get_jobs(request)
    key = f"upscale:{project_id}:{node_id}:U{body.quadrant}"

    existing = jobs.get(key)
    if existing and not existing.done.is_set():
        return _tail_job(existing)

    job = GenerationJob()
    jobs[key] = job

    grid_msg_id = parent.image.discord_message_id
    grid_chan_id = parent.image.discord_channel_id
    parent_prompt = parent.prompt_text
    quadrant = body.quadrant
    session_factory = request.app.state.session_factory
    claude_cli = request.app.state.claude_cli

    async def run():
        try:
            async for event in mj.upscale(grid_msg_id, grid_chan_id, quadrant):
                if event.type == "done" and event.image_url:
                    payload = await _ingest_action(
                        session_factory=session_factory,
                        settings=settings,
                        claude_cli=claude_cli,
                        project_id=project_id,
                        parent_node_id=node_id,
                        parent_prompt_text=parent_prompt,
                        child_name=f"U{quadrant}",
                        image_kind="upscale",
                        image_url=event.image_url,
                        discord_message_id=event.discord_message_id,
                        discord_channel_id=event.discord_channel_id,
                    )
                    job.broadcast(payload)
                    return
                job.broadcast(_event_payload(event))
                if event.type == "error":
                    return
        except Exception as e:
            logger.exception("upscale task failed for node_id=%s U%d", node_id, quadrant)
            job.broadcast({"type": "error", "message": str(e)})
        finally:
            job.done.set()
            await asyncio.sleep(120)
            jobs.pop(key, None)

    job.task = asyncio.create_task(run())
    return _tail_job(job)


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
    parent = await _get_node(db, project_id, node_id)
    _validate_grid_parent(parent)

    jobs = _get_jobs(request)
    key = f"variation:{project_id}:{node_id}:V{body.quadrant}"

    existing = jobs.get(key)
    if existing and not existing.done.is_set():
        return _tail_job(existing)

    job = GenerationJob()
    jobs[key] = job

    grid_msg_id = parent.image.discord_message_id
    grid_chan_id = parent.image.discord_channel_id
    parent_prompt = parent.prompt_text
    quadrant = body.quadrant
    session_factory = request.app.state.session_factory
    claude_cli = request.app.state.claude_cli

    async def run():
        try:
            async for event in mj.variation(grid_msg_id, grid_chan_id, quadrant):
                if event.type == "done" and event.image_url:
                    payload = await _ingest_action(
                        session_factory=session_factory,
                        settings=settings,
                        claude_cli=claude_cli,
                        project_id=project_id,
                        parent_node_id=node_id,
                        parent_prompt_text=parent_prompt,
                        child_name=f"V{quadrant}",
                        image_kind="variation",
                        image_url=event.image_url,
                        discord_message_id=event.discord_message_id,
                        discord_channel_id=event.discord_channel_id,
                    )
                    job.broadcast(payload)
                    return
                job.broadcast(_event_payload(event))
                if event.type == "error":
                    return
        except Exception as e:
            logger.exception("variation task failed for node_id=%s V%d", node_id, quadrant)
            job.broadcast({"type": "error", "message": str(e)})
        finally:
            job.done.set()
            await asyncio.sleep(120)
            jobs.pop(key, None)

    job.task = asyncio.create_task(run())
    return _tail_job(job)


# ============================================================
# Ingest helpers — download MJ result and persist. These use
# session_factory (not a request-scoped db session) so they
# work inside background tasks that outlive the HTTP request.
# ============================================================


async def _ingest_grid(
    session_factory: async_sessionmaker,
    settings: Settings,
    claude_cli: str | None,
    project_id: int,
    node_id: int,
    grid_url: str,
    discord_message_id: str | None,
    discord_channel_id: str | None,
) -> dict:
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
        async with session_factory() as db:
            result = await db.execute(
                select(PromptNode)
                .where(PromptNode.id == node_id, PromptNode.project_id == project_id)
                .options(selectinload(PromptNode.image).selectinload(Image.tags))
            )
            node = result.scalar_one_or_none()
            if not node:
                return {"type": "error", "message": f"node {node_id} not found"}

            image = await save_image(
                db, settings, node, project_id, node_id, resp.content, ext
            )
            image.description = None
            image.kind = "grid"
            image.discord_message_id = discord_message_id
            image.discord_channel_id = discord_channel_id
            await db.flush()
            await db.commit()

            image_id = image.id
            filename = image.filename
    except Exception as e:
        logger.exception("failed to persist MJ grid for node_id=%s", node_id)
        return {"type": "error", "message": f"failed to save MJ grid: {e}"}

    asyncio.create_task(
        generate_description_task(
            session_factory,
            image_id,
            image_dir(settings, project_id) / filename,
            claude_cli,
        )
    )

    return {
        "type": "done",
        "image_url": grid_url,
        "image_id": image_id,
        "filename": filename,
    }


async def _ingest_action(
    session_factory: async_sessionmaker,
    settings: Settings,
    claude_cli: str | None,
    project_id: int,
    parent_node_id: int,
    parent_prompt_text: str,
    child_name: str,
    image_kind: str,
    image_url: str,
    discord_message_id: str | None,
    discord_channel_id: str | None,
) -> dict:
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
        async with session_factory() as db:
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

            image_id = image.id
            filename = image.filename
            child_id = child.id
    except Exception as e:
        logger.exception(
            "failed to persist MJ %s under parent node_id=%s",
            image_kind, parent_node_id,
        )
        return {"type": "error", "message": f"failed to save {image_kind}: {e}"}

    asyncio.create_task(
        generate_description_task(
            session_factory,
            image_id,
            image_dir(settings, project_id) / filename,
            claude_cli,
        )
    )

    return {
        "type": "done",
        "image_url": image_url,
        "image_id": image_id,
        "filename": filename,
        "node_id": child_id,
    }
