from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from atelier.dependencies import get_db
from atelier.models import Image, PromptNode, Tag
from atelier.schemas import TagCreate, TagResponse

router = APIRouter(prefix="/api", tags=["tags"])


async def _get_or_create_tag(db: AsyncSession, name: str) -> Tag:
    result = await db.execute(select(Tag).where(Tag.name == name))
    tag = result.scalar_one_or_none()
    if not tag:
        tag = Tag(name=name)
        db.add(tag)
        await db.flush()
    return tag


@router.get("/tags", response_model=list[TagResponse])
async def list_tags(
    q: str | None = None, db: AsyncSession = Depends(get_db)
):
    stmt = select(Tag).order_by(Tag.name)
    if q:
        stmt = stmt.where(Tag.name.ilike(f"%{q}%"))
    result = await db.execute(stmt)
    return result.scalars().all()


# --- Node tags ---


@router.post(
    "/projects/{project_id}/nodes/{node_id}/tags",
    response_model=TagResponse,
    status_code=201,
)
async def add_node_tag(
    project_id: int,
    node_id: int,
    body: TagCreate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PromptNode)
        .where(PromptNode.id == node_id, PromptNode.project_id == project_id)
        .options(selectinload(PromptNode.tags))
    )
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(404, "Node not found")

    tag = await _get_or_create_tag(db, body.name.strip().lower())
    if tag not in node.tags:
        node.tags.append(tag)
    return tag


@router.delete(
    "/projects/{project_id}/nodes/{node_id}/tags/{tag_name}", status_code=204
)
async def remove_node_tag(
    project_id: int,
    node_id: int,
    tag_name: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PromptNode)
        .where(PromptNode.id == node_id, PromptNode.project_id == project_id)
        .options(selectinload(PromptNode.tags))
    )
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(404, "Node not found")

    node.tags = [t for t in node.tags if t.name != tag_name]


# --- Image tags ---


@router.post("/images/{image_id}/tags", response_model=TagResponse, status_code=201)
async def add_image_tag(
    image_id: int, body: TagCreate, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Image).where(Image.id == image_id).options(selectinload(Image.tags))
    )
    image = result.scalar_one_or_none()
    if not image:
        raise HTTPException(404, "Image not found")

    tag = await _get_or_create_tag(db, body.name.strip().lower())
    if tag not in image.tags:
        image.tags.append(tag)
    return tag


@router.delete("/images/{image_id}/tags/{tag_name}", status_code=204)
async def remove_image_tag(
    image_id: int, tag_name: str, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Image).where(Image.id == image_id).options(selectinload(Image.tags))
    )
    image = result.scalar_one_or_none()
    if not image:
        raise HTTPException(404, "Image not found")

    image.tags = [t for t in image.tags if t.name != tag_name]
