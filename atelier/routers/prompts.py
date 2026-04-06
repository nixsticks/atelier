from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from atelier.config import Settings
from atelier.dependencies import get_db, get_settings
from atelier.models import Image, Project, PromptNode
from atelier.schemas import (
    PromptNodeCreate,
    PromptNodeResponse,
    PromptNodeUpdate,
    PromptTreeNode,
)
from atelier.services.tree import (
    get_ancestors,
    get_descendant_ids,
    get_project_tree,
)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["prompts"])


async def _get_node(
    db: AsyncSession, project_id: int, node_id: int
) -> PromptNode:
    result = await db.execute(
        select(PromptNode)
        .where(PromptNode.id == node_id, PromptNode.project_id == project_id)
        .options(
            selectinload(PromptNode.image).selectinload(Image.tags),
            selectinload(PromptNode.tags),
        )
    )
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(404, "Node not found")
    return node


@router.get("/tree", response_model=list[PromptTreeNode])
async def get_tree(project_id: int, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return await get_project_tree(db, project_id)


@router.post("/nodes", response_model=PromptNodeResponse, status_code=201)
async def create_node(
    project_id: int,
    body: PromptNodeCreate,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    if body.parent_id is not None:
        parent = await db.get(PromptNode, body.parent_id)
        if not parent or parent.project_id != project_id:
            raise HTTPException(400, "Parent node not found in this project")

    node = PromptNode(
        project_id=project_id,
        parent_id=body.parent_id,
        name=body.name,
        prompt_text=body.prompt_text,
        notes=body.notes,
    )
    db.add(node)
    await db.flush()
    await db.refresh(node)
    return await _get_node(db, project_id, node.id)


@router.get("/nodes/{node_id}", response_model=PromptNodeResponse)
async def get_node(
    project_id: int, node_id: int, db: AsyncSession = Depends(get_db)
):
    return await _get_node(db, project_id, node_id)


@router.patch("/nodes/{node_id}", response_model=PromptNodeResponse)
async def update_node(
    project_id: int,
    node_id: int,
    body: PromptNodeUpdate,
    db: AsyncSession = Depends(get_db),
):
    node = await _get_node(db, project_id, node_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(node, field, value)
    await db.flush()
    await db.refresh(node)
    return await _get_node(db, project_id, node.id)


@router.delete("/nodes/{node_id}", status_code=204)
async def delete_node(
    project_id: int,
    node_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    node = await _get_node(db, project_id, node_id)

    # Collect every image filename under this subtree before the cascade
    # delete wipes the rows. We unlink the files after the DB operation
    # commits so a failed delete doesn't lose the on-disk originals.
    descendant_ids = await get_descendant_ids(db, node_id)
    img_result = await db.execute(
        select(Image).where(Image.prompt_node_id.in_(descendant_ids))
    )
    image_filenames = [img.filename for img in img_result.scalars().all()]

    await db.delete(node)
    await db.flush()

    project_image_dir = settings.image_dir / str(project_id)
    for fn in image_filenames:
        path = project_image_dir / fn
        if path.exists():
            path.unlink()


@router.get("/nodes/{node_id}/ancestors", response_model=list[PromptNodeResponse])
async def get_node_ancestors(
    project_id: int, node_id: int, db: AsyncSession = Depends(get_db)
):
    await _get_node(db, project_id, node_id)
    ancestors = await get_ancestors(db, node_id)
    return ancestors
