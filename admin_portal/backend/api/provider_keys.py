"""Provider Keys management routes."""
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from fastapi import APIRouter, HTTPException, status

from app.core.config import settings
from app.db.dynamodb import DynamoDBClient, ProviderKeyManager
from app.keypool.encryption import KeyEncryption
from admin_portal.backend.schemas.provider_key import (
    ProviderKeyCreate,
    ProviderKeyUpdate,
    ProviderKeyResponse,
)

router = APIRouter()


def _get_manager():
    db_client = DynamoDBClient()
    return ProviderKeyManager(db_client)


def _get_encryption():
    secret = settings.provider_key_encryption_secret
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="PROVIDER_KEY_ENCRYPTION_SECRET not configured",
        )
    return KeyEncryption(secret)


@router.get("", response_model=List[ProviderKeyResponse])
async def list_provider_keys():
    """List all provider keys (keys are masked)."""
    mgr = _get_manager()
    encryption = _get_encryption()
    items = mgr.list_keys()
    result = []
    for item in items:
        masked = KeyEncryption.mask(
            encryption.decrypt(item["encrypted_api_key"])
        ) if item.get("encrypted_api_key") else "****"
        result.append(ProviderKeyResponse(
            key_id=item["key_id"],
            provider=item["provider"],
            api_key_masked=masked,
            models=item.get("models", []),
            is_enabled=item.get("is_enabled", True),
            status=item.get("status", "available"),
            created_at=item.get("created_at", ""),
            updated_at=item.get("updated_at"),
        ))
    return result


@router.post("", response_model=ProviderKeyResponse, status_code=status.HTTP_201_CREATED)
async def create_provider_key(body: ProviderKeyCreate):
    """Create a new provider key (encrypts before storage)."""
    mgr = _get_manager()
    encryption = _get_encryption()
    encrypted = encryption.encrypt(body.api_key)
    item = mgr.create_key(
        provider=body.provider,
        encrypted_api_key=encrypted,
        models=body.models,
    )
    return ProviderKeyResponse(
        key_id=item["key_id"],
        provider=item["provider"],
        api_key_masked=KeyEncryption.mask(body.api_key),
        models=item.get("models", []),
        is_enabled=item.get("is_enabled", True),
        status=item.get("status", "available"),
        created_at=item.get("created_at", ""),
        updated_at=item.get("updated_at"),
    )


@router.put("/{key_id}", response_model=dict)
async def update_provider_key(key_id: str, body: ProviderKeyUpdate):
    """Update a provider key's models or enabled status."""
    mgr = _get_manager()
    success = mgr.update_key(
        key_id=key_id,
        models=body.models,
        is_enabled=body.is_enabled,
    )
    if not success:
        raise HTTPException(status_code=404, detail="Provider key not found")
    return {"success": True}


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider_key(key_id: str):
    """Delete a provider key."""
    mgr = _get_manager()
    mgr.delete_key(key_id)
