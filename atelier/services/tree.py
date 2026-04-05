from collections import defaultdict

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from atelier.models import PromptNode
from atelier.schemas import PromptTreeNode, TagResponse


async def get_project_tree(
    db: AsyncSession, project_id: int
) -> list[PromptTreeNode]:
    """Load all nodes for a project and assemble into a nested tree."""
    result = await db.execute(
        select(PromptNode)
        .where(PromptNode.project_id == project_id)
        .options(selectinload(PromptNode.image), selectinload(PromptNode.tags))
        .order_by(PromptNode.created_at)
    )
    nodes = result.scalars().all()

    children_map: dict[int | None, list[PromptNode]] = defaultdict(list)
    for node in nodes:
        children_map[node.parent_id].append(node)

    def build(node: PromptNode) -> PromptTreeNode:
        return PromptTreeNode(
            id=node.id,
            parent_id=node.parent_id,
            prompt_text=node.prompt_text,
            notes=node.notes,
            is_starred=node.is_starred,
            created_at=node.created_at,
            has_image=node.image is not None,
            tags=[TagResponse.model_validate(t) for t in node.tags],
            children=[build(c) for c in children_map.get(node.id, [])],
        )

    return [build(r) for r in children_map.get(None, [])]


async def get_ancestors(
    db: AsyncSession, node_id: int
) -> list[PromptNode]:
    """Walk parent_id chain to root using recursive CTE. Returns root-first order."""
    cte_query = text("""
        WITH RECURSIVE ancestors AS (
            SELECT id, parent_id, 0 AS depth FROM prompt_node WHERE id = :node_id
            UNION ALL
            SELECT pn.id, pn.parent_id, a.depth + 1
            FROM prompt_node pn
            JOIN ancestors a ON pn.id = a.parent_id
        )
        SELECT id FROM ancestors ORDER BY depth DESC
    """)
    result = await db.execute(cte_query, {"node_id": node_id})
    ancestor_ids = [row[0] for row in result.fetchall()]

    if not ancestor_ids:
        return []

    # Load full ORM objects with relationships
    result = await db.execute(
        select(PromptNode)
        .where(PromptNode.id.in_(ancestor_ids))
        .options(
            selectinload(PromptNode.image),
            selectinload(PromptNode.tags),
        )
    )
    nodes_by_id = {n.id: n for n in result.scalars().all()}
    return [nodes_by_id[aid] for aid in ancestor_ids if aid in nodes_by_id]
