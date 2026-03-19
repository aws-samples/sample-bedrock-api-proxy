"""Failover chain management routes."""
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from fastapi import APIRouter, HTTPException, status

from app.db.dynamodb import DynamoDBClient, FailoverConfigManager
from admin_portal.backend.schemas.failover import (
    FailoverChainCreate,
    FailoverChainUpdate,
    FailoverChainResponse,
    FailoverTarget,
)

router = APIRouter()


def _get_manager():
    return FailoverConfigManager(DynamoDBClient())


@router.get("/chains", response_model=List[FailoverChainResponse])
async def list_failover_chains():
    """List all failover chains."""
    mgr = _get_manager()
    items = mgr.list_chains()
    return [
        FailoverChainResponse(
            source_model=c["source_model"],
            targets=[FailoverTarget(**t) for t in c.get("targets", [])],
            created_at=c.get("created_at", ""),
            updated_at=c.get("updated_at"),
        )
        for c in items
    ]


@router.post("/chains", response_model=FailoverChainResponse, status_code=status.HTTP_201_CREATED)
async def create_failover_chain(body: FailoverChainCreate):
    """Create a new failover chain."""
    mgr = _get_manager()
    targets_dicts = [t.model_dump() for t in body.targets]
    item = mgr.create_chain(source_model=body.source_model, targets=targets_dicts)
    return FailoverChainResponse(
        source_model=item["source_model"],
        targets=[FailoverTarget(**t) for t in item.get("targets", [])],
        created_at=item.get("created_at", ""),
        updated_at=item.get("updated_at"),
    )


@router.put("/chains/{source_model}", response_model=dict)
async def update_failover_chain(source_model: str, body: FailoverChainUpdate):
    """Update a failover chain's targets."""
    mgr = _get_manager()
    targets_dicts = [t.model_dump() for t in body.targets]
    success = mgr.update_chain(source_model=source_model, targets=targets_dicts)
    if not success:
        raise HTTPException(status_code=404, detail="Failover chain not found")
    return {"success": True}


@router.delete("/chains/{source_model}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_failover_chain(source_model: str):
    """Delete a failover chain."""
    mgr = _get_manager()
    mgr.delete_chain(source_model)
