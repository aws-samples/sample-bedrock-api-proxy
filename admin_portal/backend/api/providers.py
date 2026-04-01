"""Provider management routes for admin portal."""
import os
import threading
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from fastapi import APIRouter, HTTPException, status

from app.core.config import settings
from app.db.dynamodb import DynamoDBClient, APIKeyManager
from app.db.provider_manager import ProviderManager
from admin_portal.backend.schemas.provider import (
    ProviderCreate,
    ProviderUpdate,
    ProviderResponse,
    ProviderListResponse,
)

router = APIRouter()

# Lock for thread-safe bearer token env var manipulation in test_provider_connection
_bearer_token_lock = threading.Lock()


def _count_api_keys_by_provider() -> dict[str, int]:
    """Scan all API keys (with pagination) and return {provider_id: count}."""
    db = DynamoDBClient()
    api_key_mgr = APIKeyManager(db)
    counts: dict[str, int] = {}
    last_key = None
    while True:
        result = api_key_mgr.list_all_api_keys(limit=1000, last_key=last_key)
        for k in result.get("items", []):
            pid = k.get("provider_id")
            if pid:
                counts[pid] = counts.get(pid, 0) + 1
        last_key = result.get("last_key")
        if not last_key:
            break
    return counts


def get_provider_manager() -> ProviderManager:
    db = DynamoDBClient()
    return ProviderManager(
        dynamodb_resource=db.dynamodb,
        table_name=settings.dynamodb_providers_table,
        encryption_secret=settings.provider_key_encryption_secret or "",
    )


@router.get("", response_model=ProviderListResponse)
async def list_providers():
    mgr = get_provider_manager()
    items = mgr.list_providers()
    key_counts = _count_api_keys_by_provider()

    providers = []
    for item in items:
        providers.append(ProviderResponse(**{**item, "api_key_count": key_counts.get(item["provider_id"], 0)}))

    return ProviderListResponse(
        items=providers,
        count=len(items),
    )


@router.get("/{provider_id}", response_model=ProviderResponse)
async def get_provider(provider_id: str):
    mgr = get_provider_manager()
    item = mgr.get_provider(provider_id)
    if not item:
        raise HTTPException(status_code=404, detail="Provider not found")

    key_counts = _count_api_keys_by_provider()
    return ProviderResponse(**{**item, "api_key_count": key_counts.get(provider_id, 0)})


@router.post("", response_model=ProviderResponse, status_code=status.HTTP_201_CREATED)
async def create_provider(request: ProviderCreate):
    mgr = get_provider_manager()
    item = mgr.create_provider(
        name=request.name,
        aws_region=request.aws_region,
        auth_type=request.auth_type,
        credentials=request.credentials,
        endpoint_url=request.endpoint_url,
    )
    return ProviderResponse(**item)


@router.put("/{provider_id}", response_model=ProviderResponse)
async def update_provider(provider_id: str, request: ProviderUpdate):
    mgr = get_provider_manager()
    existing = mgr.get_provider(provider_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Provider not found")

    update_data = request.model_dump(exclude_none=True)
    if update_data:
        mgr.update_provider(provider_id, **update_data)

    item = mgr.get_provider(provider_id)
    return ProviderResponse(**item)


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(provider_id: str):
    mgr = get_provider_manager()
    existing = mgr.get_provider(provider_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Provider not found")

    # Check if any API keys reference this provider
    key_counts = _count_api_keys_by_provider()
    ref_count = key_counts.get(provider_id, 0)
    if ref_count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot delete: {ref_count} API key(s) reference this provider",
        )

    mgr.delete_provider(provider_id)


@router.post("/{provider_id}/test")
async def test_provider_connection(provider_id: str):
    """Test provider connectivity by calling Bedrock ListFoundationModels."""
    import boto3
    from botocore.config import Config

    mgr = get_provider_manager()
    provider = mgr.get_provider(provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    creds = mgr.get_decrypted_credentials(provider_id)
    region = provider.get("aws_region", "us-east-1")
    auth_type = provider.get("auth_type")

    config = Config(connect_timeout=10, read_timeout=10)

    try:
        if auth_type == "bearer_token":
            with _bearer_token_lock:
                old_val = os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
                try:
                    os.environ["AWS_BEARER_TOKEN_BEDROCK"] = creds["bearer_token"]
                    client = boto3.client("bedrock", region_name=region, config=config)
                    resp = client.list_foundation_models(byOutputModality="TEXT")
                finally:
                    if old_val is not None:
                        os.environ["AWS_BEARER_TOKEN_BEDROCK"] = old_val
                    else:
                        os.environ.pop("AWS_BEARER_TOKEN_BEDROCK", None)
        else:
            client = boto3.client(
                "bedrock", region_name=region,
                aws_access_key_id=creds.get("access_key_id"),
                aws_secret_access_key=creds.get("secret_access_key"),
                aws_session_token=creds.get("session_token"),
                config=config,
            )
            resp = client.list_foundation_models(byOutputModality="TEXT")

        model_count = len(resp.get("modelSummaries", []))
        return {"status": "ok", "message": f"Connected successfully. Found {model_count} text models."}
    except Exception as e:
        return {"status": "error", "message": str(e)}
