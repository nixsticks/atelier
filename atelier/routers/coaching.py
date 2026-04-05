import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from atelier.config import Settings
from atelier.dependencies import get_db, get_knowledge, get_settings
from atelier.models import CoachingMessage, PromptNode
from atelier.schemas import CoachingMessageResponse, CoachingRequest
from atelier.services.coaching import CoachingService

router = APIRouter(
    prefix="/api/projects/{project_id}/nodes/{node_id}/coaching",
    tags=["coaching"],
)


async def _get_node(db: AsyncSession, project_id: int, node_id: int) -> PromptNode:
    result = await db.execute(
        select(PromptNode)
        .where(PromptNode.id == node_id, PromptNode.project_id == project_id)
        .options(
            selectinload(PromptNode.image),
            selectinload(PromptNode.coaching_messages),
        )
    )
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(404, "Node not found")
    return node


@router.get("", response_model=list[CoachingMessageResponse])
async def get_coaching_history(
    project_id: int, node_id: int, db: AsyncSession = Depends(get_db)
):
    node = await _get_node(db, project_id, node_id)
    return node.coaching_messages


@router.post("")
async def coach(
    project_id: int,
    node_id: int,
    body: CoachingRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    knowledge: dict[str, str] = Depends(get_knowledge),
):
    node = await _get_node(db, project_id, node_id)

    svc = CoachingService(settings=settings, knowledge=knowledge)

    user_msg = CoachingMessage(
        prompt_node_id=node_id, role="user", content=body.message
    )
    db.add(user_msg)
    await db.flush()

    async def stream():
        chunks: list[str] = []
        try:
            async for token in svc.stream_response(db, node, body.message):
                chunks.append(token)
                yield f"data: {json.dumps(token)}\n\n"
        finally:
            # Save assistant message even if client disconnects mid-stream
            if chunks:
                assistant_content = "".join(chunks)
                assistant_msg = CoachingMessage(
                    prompt_node_id=node_id,
                    role="assistant",
                    content=assistant_content,
                )
                db.add(assistant_msg)
                await db.commit()
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")
