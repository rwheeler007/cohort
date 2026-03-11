"""Lightweight agent list endpoint with optional full config."""

from typing import Optional, List
from sqlalchemy.orm import selectinload
from fastapi import APIRouter, Query, HTTPException
from models.agent import AgentSummary, AgentFull

router = APIRouter(prefix="/agents", tags=["Agents"])


@router.get("/summary/", response_model=List[AgentSummary])
async def get_agents_summary(
    cursor: Optional[str] = None,
    limit: int = Query(default=20, ge=1, le=100),
    status: Optional[str] = None,
) -> List[AgentSummary]:
    """Get lightweight agent summaries for list view.

    Returns only essential fields (id, name, status, created_at).
    Use ?include=full to fetch complete agent configs.

    Args:
        cursor: Keyset pagination cursor (created_at timestamp from previous page)
        limit: Number of results per page (1-100)
        status: Optional filter by agent status (active/inactive/maintenance)

    Returns:
        List of AgentSummary objects with lightweight schema
    """
    # Query optimization: only select summary fields
    query = select(AgentSummary).order_by(AgentSummary.created_at.desc())

    if status:
        query = query.where(AgentSummary.status == status)

    if cursor:
        try:
            cursor_ts = datetime.fromisoformat(cursor.replace("Z", "+00:00"))
            query = query.where(AgentSummary.created_at < cursor_ts)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid cursor format")

    results = await db.execute(query.limit(limit))
    return [row._mapping for row in results]


@router.get("/full/", response_model=List[AgentFull])
async def get_agents_full(
    cursor: Optional[str] = None,
    limit: int = Query(default=20, ge=1, le=100),
    status: Optional[str] = None,
) -> List[AgentFull]:
    """Get full agent configs with all fields.

    Use only when detailed config is explicitly needed.
    Default endpoint returns lightweight summaries.

    Args:
        cursor: Keyset pagination cursor
        limit: Number of results per page (1-100)
        status: Optional filter by agent status

    Returns:
        List of AgentFull objects with complete schema
    """
    query = select(AgentFull).options(selectinload(AgentFull.config)).order_by(
        AgentFull.created_at.desc()
    )

    if status:
        query = query.where(AgentFull.status == status)

    if cursor:
        try:
            cursor_ts = datetime.fromisoformat(cursor.replace("Z", "+00:00"))
            query = query.where(AgentFull.created_at < cursor_ts)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid cursor format")

    results = await db.execute(query.limit(limit))
    return [row._mapping for row in results]


@router.get("/{agent_id}/summary", response_model=AgentSummary)
async def get_agent_summary(agent_id: int) -> AgentSummary:
    """Get a single agent's lightweight summary."""
    agent = await db.get(AgentFull, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return AgentSummary(
        id=agent.id,
        name=agent.name,
        status=agent.status,
        created_at=agent.created_at,
    )


@router.get("/{agent_id}/full", response_model=AgentFull)
async def get_agent_full(agent_id: int) -> AgentFull:
    """Get a single agent's full config."""
    agent = await db.get(AgentFull, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent
