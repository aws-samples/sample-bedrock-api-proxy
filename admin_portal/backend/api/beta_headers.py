"""Beta Headers management routes."""
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from fastapi import APIRouter, HTTPException, Query, status

from app.db.dynamodb import DynamoDBClient, BetaHeaderManager
from app.core.config import settings
from admin_portal.backend.schemas.beta_headers import (
    BetaHeaderCreate,
    BetaHeaderUpdate,
    BetaHeaderResponse,
    BetaHeaderListResponse,
)

router = APIRouter()

# Track whether we've seeded defaults this process.
# Not locked — concurrent requests may double-seed, but put_item is idempotent
# with identical data, so this is safe.
_defaults_seeded = False


def get_manager():
    db_client = DynamoDBClient()
    return BetaHeaderManager(db_client)


def _ensure_defaults_seeded(manager: BetaHeaderManager):
    """Seed config defaults into DynamoDB if the table is empty (once per process)."""
    global _defaults_seeded
    if _defaults_seeded:
        return

    items = manager.list_all()
    if items:
        _defaults_seeded = True
        return

    # Table is empty — seed defaults from config
    for header_name in settings.beta_headers_blocklist:
        manager.create(
            header_name=header_name,
            header_type="blocklist",
            description="Default blocklist from config",
        )

    for header_name, mapped_to in settings.beta_header_mapping.items():
        manager.create(
            header_name=header_name,
            header_type="mapping",
            mapped_to=mapped_to,
            description="Default mapping from config",
        )

    _defaults_seeded = True


def _item_to_response(item: dict) -> BetaHeaderResponse:
    return BetaHeaderResponse(
        header_name=item.get("header_name", ""),
        header_type=item.get("header_type", ""),
        mapped_to=item.get("mapped_to", []),
        description=item.get("description", ""),
        created_at=item.get("created_at", ""),
        updated_at=item.get("updated_at", ""),
    )


@router.get("", response_model=BetaHeaderListResponse)
async def list_beta_headers(
    type: Optional[str] = Query(default=None, description="Filter by type: mapping or blocklist"),
    search: Optional[str] = Query(default=None, description="Search by header name"),
):
    """List all beta header rules."""
    manager = get_manager()
    _ensure_defaults_seeded(manager)

    items = manager.list_all()
    results = [_item_to_response(item) for item in items]

    if type:
        results = [r for r in results if r.header_type == type]

    if search:
        search_lower = search.lower()
        results = [r for r in results if search_lower in r.header_name.lower()]

    results.sort(key=lambda x: (0 if x.header_type == "blocklist" else 1, x.header_name))

    return BetaHeaderListResponse(items=results, count=len(results))


@router.get("/{header_name:path}", response_model=BetaHeaderResponse)
async def get_beta_header(header_name: str):
    """Get a specific beta header rule."""
    header_name = unquote(header_name)
    manager = get_manager()
    item = manager.get(header_name)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Beta header not found")

    return _item_to_response(item)


@router.post("", response_model=BetaHeaderResponse, status_code=status.HTTP_201_CREATED)
async def create_beta_header(request: BetaHeaderCreate):
    """Create a new beta header rule."""
    manager = get_manager()

    existing = manager.get(request.header_name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Beta header already exists. Use PUT to update.",
        )

    if request.header_type == "mapping" and not request.mapped_to:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="mapped_to is required for mapping type headers.",
        )

    item = manager.create(
        header_name=request.header_name,
        header_type=request.header_type,
        mapped_to=request.mapped_to,
        description=request.description,
    )

    return _item_to_response(item)


@router.put("/{header_name:path}", response_model=BetaHeaderResponse)
async def update_beta_header(header_name: str, request: BetaHeaderUpdate):
    """Update an existing beta header rule."""
    header_name = unquote(header_name)
    manager = get_manager()

    existing = manager.get(header_name)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Beta header not found")

    updates = {}
    if request.header_type is not None:
        updates["header_type"] = request.header_type
    if request.mapped_to is not None:
        updates["mapped_to"] = request.mapped_to
    if request.description is not None:
        updates["description"] = request.description

    if not updates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update")

    final_type = updates.get("header_type", existing.get("header_type"))
    final_mapped_to = updates.get("mapped_to", existing.get("mapped_to", []))
    if final_type == "mapping" and not final_mapped_to:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="mapped_to is required for mapping type headers.",
        )

    updated = manager.update(header_name, updates)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Beta header not found")

    return _item_to_response(updated)


@router.delete("/{header_name:path}")
async def delete_beta_header(header_name: str):
    """Delete a beta header rule. Deleted headers will pass through to Bedrock."""
    header_name = unquote(header_name)
    manager = get_manager()

    deleted = manager.delete(header_name)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Beta header not found")

    return {"message": "Beta header deleted successfully"}
