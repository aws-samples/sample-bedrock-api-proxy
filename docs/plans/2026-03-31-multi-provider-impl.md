# Multi Bedrock Provider Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow different API keys to route to different AWS accounts' Bedrock endpoints via Provider configs managed in Admin Portal.

**Architecture:** New `ProviderManager` stores encrypted Bedrock credentials (Bearer Token or AK/SK) in DynamoDB. `BedrockService` gains a client cache pool keyed by `provider_id`. Auth middleware passes `provider_id` from API key to the handler, which passes it to the service. Admin Portal gets a new Provider CRUD page and the API Key form gets a Provider dropdown.

**Tech Stack:** Python 3.11, FastAPI, boto3, Pydantic v2, DynamoDB, Fernet encryption (existing `app/keypool/encryption.py`)

---

### Task 1: Provider Pydantic Schemas

**Files:**
- Create: `app/schemas/provider.py`

**Step 1: Write the test**

Create `tests/unit/test_provider_schemas.py`:

```python
"""Tests for Provider schemas."""
import pytest
from app.schemas.provider import ProviderCreate, ProviderResponse, ProviderUpdate


def test_provider_create_bearer_token():
    p = ProviderCreate(
        name="Prod US",
        aws_region="us-east-1",
        auth_type="bearer_token",
        credentials={"bearer_token": "test-token-123"},
    )
    assert p.auth_type == "bearer_token"
    assert p.endpoint_url is None


def test_provider_create_ak_sk():
    p = ProviderCreate(
        name="Staging EU",
        aws_region="eu-west-1",
        auth_type="ak_sk",
        credentials={
            "access_key_id": "AKID",
            "secret_access_key": "SECRET",
        },
    )
    assert p.auth_type == "ak_sk"


def test_provider_create_invalid_auth_type():
    with pytest.raises(ValueError):
        ProviderCreate(
            name="Bad",
            aws_region="us-east-1",
            auth_type="invalid",
            credentials={"bearer_token": "x"},
        )


def test_provider_create_bearer_token_missing_key():
    with pytest.raises(ValueError):
        ProviderCreate(
            name="Bad",
            aws_region="us-east-1",
            auth_type="bearer_token",
            credentials={"access_key_id": "x"},
        )


def test_provider_create_ak_sk_missing_secret():
    with pytest.raises(ValueError):
        ProviderCreate(
            name="Bad",
            aws_region="us-east-1",
            auth_type="ak_sk",
            credentials={"access_key_id": "AKID"},
        )


def test_provider_response():
    r = ProviderResponse(
        provider_id="abc-123",
        name="Prod",
        aws_region="us-east-1",
        auth_type="bearer_token",
        masked_credentials="test****t123",
        is_active=True,
        created_at="2026-03-31T00:00:00Z",
        updated_at="2026-03-31T00:00:00Z",
    )
    assert r.provider_id == "abc-123"
    assert r.endpoint_url is None


def test_provider_update_partial():
    u = ProviderUpdate(name="New Name")
    assert u.name == "New Name"
    assert u.aws_region is None
    assert u.credentials is None
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_provider_schemas.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.schemas.provider'`

**Step 3: Write the implementation**

Create `app/schemas/provider.py`:

```python
"""Provider schemas for multi-Bedrock-account support."""
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class ProviderCreate(BaseModel):
    """Schema for creating a new provider."""

    name: str = Field(..., description="Display name, e.g. 'Production Account US'")
    aws_region: str = Field(..., description="AWS region for Bedrock, e.g. 'us-east-1'")
    auth_type: Literal["bearer_token", "ak_sk"] = Field(
        ..., description="Authentication method"
    )
    credentials: Dict[str, str] = Field(
        ..., description="Credentials dict (bearer_token or access_key_id/secret_access_key/session_token)"
    )
    endpoint_url: Optional[str] = Field(
        None, description="Custom Bedrock endpoint URL"
    )

    @model_validator(mode="after")
    def validate_credentials(self) -> "ProviderCreate":
        if self.auth_type == "bearer_token":
            if "bearer_token" not in self.credentials:
                raise ValueError("credentials must contain 'bearer_token' for auth_type='bearer_token'")
        elif self.auth_type == "ak_sk":
            if "access_key_id" not in self.credentials:
                raise ValueError("credentials must contain 'access_key_id' for auth_type='ak_sk'")
            if "secret_access_key" not in self.credentials:
                raise ValueError("credentials must contain 'secret_access_key' for auth_type='ak_sk'")
        return self


class ProviderUpdate(BaseModel):
    """Schema for updating a provider (all fields optional)."""

    name: Optional[str] = None
    aws_region: Optional[str] = None
    auth_type: Optional[Literal["bearer_token", "ak_sk"]] = None
    credentials: Optional[Dict[str, str]] = None
    endpoint_url: Optional[str] = None
    is_active: Optional[bool] = None


class ProviderResponse(BaseModel):
    """Schema for provider API responses (credentials masked)."""

    provider_id: str
    name: str
    aws_region: str
    auth_type: str
    masked_credentials: str = Field(
        ..., description="Masked credentials for display"
    )
    endpoint_url: Optional[str] = None
    is_active: bool = True
    created_at: str
    updated_at: str
    api_key_count: int = 0

    class Config:
        extra = "allow"


class ProviderListResponse(BaseModel):
    """Paginated list of providers."""

    items: list[ProviderResponse]
    count: int
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_provider_schemas.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add app/schemas/provider.py tests/unit/test_provider_schemas.py
git commit -m "feat: add Provider Pydantic schemas with validation"
```

---

### Task 2: ProviderManager (DynamoDB CRUD + Encryption)

**Files:**
- Create: `app/db/provider_manager.py`
- Create: `tests/unit/test_provider_manager.py`

**Step 1: Write the test**

Create `tests/unit/test_provider_manager.py`:

```python
"""Tests for ProviderManager."""
import json
import os
import pytest
from unittest.mock import MagicMock, patch
from moto import mock_aws

from app.db.provider_manager import ProviderManager


@pytest.fixture
def encryption_secret():
    return "test-encryption-secret-for-unit-tests"


@pytest.fixture
def mock_dynamodb(encryption_secret):
    """Set up mocked DynamoDB with providers table."""
    with mock_aws():
        import boto3
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        table = dynamodb.create_table(
            TableName="anthropic-proxy-providers",
            KeySchema=[{"AttributeName": "provider_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "provider_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        table.wait_until_exists()
        yield dynamodb


@pytest.fixture
def manager(mock_dynamodb, encryption_secret):
    """Create ProviderManager with mocked DynamoDB."""
    return ProviderManager(
        dynamodb_resource=mock_dynamodb,
        table_name="anthropic-proxy-providers",
        encryption_secret=encryption_secret,
    )


class TestProviderManagerCreate:
    def test_create_bearer_token_provider(self, manager):
        provider = manager.create_provider(
            name="Prod US",
            aws_region="us-east-1",
            auth_type="bearer_token",
            credentials={"bearer_token": "my-secret-token"},
        )
        assert provider["provider_id"]
        assert provider["name"] == "Prod US"
        assert provider["auth_type"] == "bearer_token"
        assert provider["is_active"] is True
        # Credentials should be encrypted in storage
        assert "encrypted_credentials" in provider

    def test_create_ak_sk_provider(self, manager):
        provider = manager.create_provider(
            name="Staging EU",
            aws_region="eu-west-1",
            auth_type="ak_sk",
            credentials={
                "access_key_id": "AKIAEXAMPLE",
                "secret_access_key": "SECRET123",
                "session_token": "TOKEN456",
            },
        )
        assert provider["aws_region"] == "eu-west-1"

    def test_create_with_endpoint_url(self, manager):
        provider = manager.create_provider(
            name="Custom",
            aws_region="us-west-2",
            auth_type="bearer_token",
            credentials={"bearer_token": "tok"},
            endpoint_url="https://custom.bedrock.endpoint",
        )
        assert provider["endpoint_url"] == "https://custom.bedrock.endpoint"


class TestProviderManagerRead:
    def test_get_provider(self, manager):
        created = manager.create_provider(
            name="Test", aws_region="us-east-1",
            auth_type="bearer_token", credentials={"bearer_token": "tok"},
        )
        fetched = manager.get_provider(created["provider_id"])
        assert fetched["name"] == "Test"

    def test_get_nonexistent_returns_none(self, manager):
        assert manager.get_provider("nonexistent-id") is None

    def test_list_providers(self, manager):
        manager.create_provider(
            name="A", aws_region="us-east-1",
            auth_type="bearer_token", credentials={"bearer_token": "t1"},
        )
        manager.create_provider(
            name="B", aws_region="eu-west-1",
            auth_type="ak_sk",
            credentials={"access_key_id": "AK", "secret_access_key": "SK"},
        )
        result = manager.list_providers()
        assert len(result) == 2

    def test_get_decrypted_credentials(self, manager):
        created = manager.create_provider(
            name="Test", aws_region="us-east-1",
            auth_type="bearer_token", credentials={"bearer_token": "my-secret"},
        )
        creds = manager.get_decrypted_credentials(created["provider_id"])
        assert creds == {"bearer_token": "my-secret"}


class TestProviderManagerUpdate:
    def test_update_name(self, manager):
        created = manager.create_provider(
            name="Old", aws_region="us-east-1",
            auth_type="bearer_token", credentials={"bearer_token": "tok"},
        )
        manager.update_provider(created["provider_id"], name="New")
        fetched = manager.get_provider(created["provider_id"])
        assert fetched["name"] == "New"

    def test_update_credentials(self, manager):
        created = manager.create_provider(
            name="Test", aws_region="us-east-1",
            auth_type="bearer_token", credentials={"bearer_token": "old-tok"},
        )
        manager.update_provider(
            created["provider_id"],
            credentials={"bearer_token": "new-tok"},
        )
        creds = manager.get_decrypted_credentials(created["provider_id"])
        assert creds == {"bearer_token": "new-tok"}

    def test_deactivate(self, manager):
        created = manager.create_provider(
            name="Test", aws_region="us-east-1",
            auth_type="bearer_token", credentials={"bearer_token": "tok"},
        )
        manager.update_provider(created["provider_id"], is_active=False)
        fetched = manager.get_provider(created["provider_id"])
        assert fetched["is_active"] is False


class TestProviderManagerDelete:
    def test_delete_provider(self, manager):
        created = manager.create_provider(
            name="Test", aws_region="us-east-1",
            auth_type="bearer_token", credentials={"bearer_token": "tok"},
        )
        manager.delete_provider(created["provider_id"])
        assert manager.get_provider(created["provider_id"]) is None

    def test_delete_nonexistent_returns_false(self, manager):
        assert manager.delete_provider("nonexistent") is False


class TestProviderManagerMasking:
    def test_mask_bearer_token(self, manager):
        created = manager.create_provider(
            name="Test", aws_region="us-east-1",
            auth_type="bearer_token",
            credentials={"bearer_token": "abcdefghijklmnop"},
        )
        fetched = manager.get_provider(created["provider_id"])
        assert fetched["masked_credentials"] == "abcd****mnop"

    def test_mask_ak_sk(self, manager):
        created = manager.create_provider(
            name="Test", aws_region="us-east-1",
            auth_type="ak_sk",
            credentials={"access_key_id": "AKIAEXAMPLE", "secret_access_key": "SuperSecretKey123"},
        )
        fetched = manager.get_provider(created["provider_id"])
        # AK fully visible, SK masked
        assert "AKIAEXAMPLE" in fetched["masked_credentials"]
        assert "****" in fetched["masked_credentials"]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_provider_manager.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.db.provider_manager'`

**Step 3: Write the implementation**

Create `app/db/provider_manager.py`:

```python
"""Provider manager for multi-Bedrock-account support."""
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from botocore.exceptions import ClientError

from app.keypool.encryption import KeyEncryption


class ProviderManager:
    """CRUD manager for Bedrock provider configurations with encrypted credentials."""

    def __init__(
        self,
        dynamodb_resource,
        table_name: str = "anthropic-proxy-providers",
        encryption_secret: str = "",
    ):
        self.table = dynamodb_resource.Table(table_name)
        self.table_name = table_name
        self._encryption = KeyEncryption(encryption_secret) if encryption_secret else None

    def _encrypt_credentials(self, credentials: Dict[str, str]) -> str:
        plaintext = json.dumps(credentials)
        if self._encryption:
            return self._encryption.encrypt(plaintext)
        return plaintext

    def _decrypt_credentials(self, encrypted: str) -> Dict[str, str]:
        if self._encryption:
            plaintext = self._encryption.decrypt(encrypted)
        else:
            plaintext = encrypted
        return json.loads(plaintext)

    def _mask_credentials(self, auth_type: str, credentials: Dict[str, str]) -> str:
        if auth_type == "bearer_token":
            token = credentials.get("bearer_token", "")
            return KeyEncryption.mask(token)
        elif auth_type == "ak_sk":
            ak = credentials.get("access_key_id", "")
            sk = credentials.get("secret_access_key", "")
            return f"AK: {ak}, SK: {KeyEncryption.mask(sk)}"
        return "****"

    def create_provider(
        self,
        name: str,
        aws_region: str,
        auth_type: str,
        credentials: Dict[str, str],
        endpoint_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        provider_id = uuid4().hex
        encrypted = self._encrypt_credentials(credentials)
        masked = self._mask_credentials(auth_type, credentials)

        item = {
            "provider_id": provider_id,
            "name": name,
            "aws_region": aws_region,
            "auth_type": auth_type,
            "encrypted_credentials": encrypted,
            "masked_credentials": masked,
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        }
        if endpoint_url:
            item["endpoint_url"] = endpoint_url

        self.table.put_item(Item=item)
        return item

    def get_provider(self, provider_id: str) -> Optional[Dict[str, Any]]:
        try:
            response = self.table.get_item(Key={"provider_id": provider_id})
            return response.get("Item")
        except ClientError:
            return None

    def list_providers(self) -> List[Dict[str, Any]]:
        response = self.table.scan()
        return response.get("Items", [])

    def get_decrypted_credentials(self, provider_id: str) -> Optional[Dict[str, str]]:
        provider = self.get_provider(provider_id)
        if not provider:
            return None
        return self._decrypt_credentials(provider["encrypted_credentials"])

    def update_provider(self, provider_id: str, **kwargs) -> bool:
        provider = self.get_provider(provider_id)
        if not provider:
            return False

        update_expr_parts = []
        attr_names = {}
        attr_values = {}

        # Handle credentials separately (needs encryption)
        credentials = kwargs.pop("credentials", None)
        if credentials:
            encrypted = self._encrypt_credentials(credentials)
            auth_type = kwargs.get("auth_type", provider.get("auth_type", "bearer_token"))
            masked = self._mask_credentials(auth_type, credentials)
            update_expr_parts.append("#ec = :ec")
            attr_names["#ec"] = "encrypted_credentials"
            attr_values[":ec"] = encrypted
            update_expr_parts.append("#mc = :mc")
            attr_names["#mc"] = "masked_credentials"
            attr_values[":mc"] = masked

        for key, value in kwargs.items():
            if value is not None:
                placeholder = f"#{key}"
                val_placeholder = f":{key}"
                update_expr_parts.append(f"{placeholder} = {val_placeholder}")
                attr_names[placeholder] = key
                attr_values[val_placeholder] = value

        if not update_expr_parts:
            return True

        # Always update updated_at
        update_expr_parts.append("#ua = :ua")
        attr_names["#ua"] = "updated_at"
        attr_values[":ua"] = datetime.now(timezone.utc).isoformat()

        try:
            self.table.update_item(
                Key={"provider_id": provider_id},
                UpdateExpression="SET " + ", ".join(update_expr_parts),
                ExpressionAttributeNames=attr_names,
                ExpressionAttributeValues=attr_values,
            )
            return True
        except ClientError:
            return False

    def delete_provider(self, provider_id: str) -> bool:
        provider = self.get_provider(provider_id)
        if not provider:
            return False
        try:
            self.table.delete_item(Key={"provider_id": provider_id})
            return True
        except ClientError:
            return False
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_provider_manager.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add app/db/provider_manager.py tests/unit/test_provider_manager.py
git commit -m "feat: add ProviderManager with encrypted credential storage"
```

---

### Task 3: Register Provider Table in DynamoDB Client + Config

**Files:**
- Modify: `app/core/config.py` (add `dynamodb_providers_table` setting)
- Modify: `app/db/dynamodb.py` (add table init + `provider_manager` property)
- Modify: `scripts/setup_tables.py` (print new table name)

**Step 1: Add config setting**

In `app/core/config.py`, after line 82 (`dynamodb_usage_stats_table`), add:

```python
    dynamodb_providers_table: str = Field(
        default="anthropic-proxy-providers", alias="DYNAMODB_PROVIDERS_TABLE"
    )
```

**Step 2: Update DynamoDBClient**

In `app/db/dynamodb.py`, in `DynamoDBClient.__init__()` (after line 42), add:

```python
        self.providers_table_name = settings.dynamodb_providers_table
```

In `DynamoDBClient.create_tables()` method, add call:

```python
        self._create_providers_table()
```

Add the `_create_providers_table` method (after `_create_smart_routing_config_table`):

```python
    def _create_providers_table(self):
        """Create providers table for multi-Bedrock-account support."""
        try:
            table = self.dynamodb.create_table(
                TableName=self.providers_table_name,
                KeySchema=[
                    {"AttributeName": "provider_id", "KeyType": "HASH"},
                ],
                AttributeDefinitions=[
                    {"AttributeName": "provider_id", "AttributeType": "S"},
                ],
                BillingMode="PAY_PER_REQUEST",
            )
            table.wait_until_exists()
            print(f"Created table: {self.providers_table_name}")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceInUseException":
                print(f"Table already exists: {self.providers_table_name}")
            else:
                raise
```

**Step 3: Update `scripts/setup_tables.py`**

Add after line 34:

```python
    print(f"  - {dynamodb_client.providers_table_name}")
```

**Step 4: Run existing tests to confirm no regressions**

Run: `uv run pytest tests/unit/ -v --timeout=30`
Expected: All existing tests still pass

**Step 5: Commit**

```bash
git add app/core/config.py app/db/dynamodb.py scripts/setup_tables.py
git commit -m "feat: register providers table in DynamoDB client and config"
```

---

### Task 4: API Key Table Extension (provider_id field)

**Files:**
- Modify: `app/db/dynamodb.py` — `APIKeyManager.create_api_key()` (add `provider_id` param)
- Modify: `admin_portal/backend/schemas/api_key.py` — add `provider_id` to schemas
- Create: `tests/unit/test_api_key_provider_id.py`

**Step 1: Write the test**

Create `tests/unit/test_api_key_provider_id.py`:

```python
"""Tests for provider_id field on API keys."""
import pytest
from moto import mock_aws
import boto3

from app.db.dynamodb import DynamoDBClient, APIKeyManager


@pytest.fixture
def mock_db():
    with mock_aws():
        # Patch settings for moto
        import app.core.config as cfg
        original_endpoint = cfg.settings.dynamodb_endpoint_url
        cfg.settings.dynamodb_endpoint_url = None
        original_region = cfg.settings.aws_region
        cfg.settings.aws_region = "us-east-1"

        db = DynamoDBClient()
        db.create_tables()
        yield db

        cfg.settings.dynamodb_endpoint_url = original_endpoint
        cfg.settings.aws_region = original_region


@pytest.fixture
def api_key_manager(mock_db):
    return APIKeyManager(mock_db)


def test_create_api_key_without_provider_id(api_key_manager):
    key = api_key_manager.create_api_key(
        user_id="test-user", name="Default Provider Key"
    )
    info = api_key_manager.get_api_key(key)
    assert info["provider_id"] is None or info.get("provider_id") is None


def test_create_api_key_with_provider_id(api_key_manager):
    key = api_key_manager.create_api_key(
        user_id="test-user", name="Custom Provider Key",
        provider_id="abc-provider-123",
    )
    info = api_key_manager.get_api_key(key)
    assert info["provider_id"] == "abc-provider-123"


def test_validate_api_key_returns_provider_id(api_key_manager):
    key = api_key_manager.create_api_key(
        user_id="test-user", name="Test",
        provider_id="my-provider",
    )
    validated = api_key_manager.validate_api_key(key)
    assert validated is not None
    assert validated["provider_id"] == "my-provider"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_api_key_provider_id.py -v`
Expected: FAIL (create_api_key doesn't accept provider_id yet)

**Step 3: Implement changes**

In `app/db/dynamodb.py`, `APIKeyManager.create_api_key()`:
- Add `provider_id: Optional[str] = None` parameter
- Add `"provider_id": provider_id,` to the item dict

In `admin_portal/backend/schemas/api_key.py`:
- Add `provider_id: Optional[str] = Field(None, description="Provider ID for Bedrock routing")` to `ApiKeyCreate`
- Add `provider_id: Optional[str] = None` to `ApiKeyUpdate`
- Add `provider_id: Optional[str] = None` to `ApiKeyResponse`

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_api_key_provider_id.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add app/db/dynamodb.py admin_portal/backend/schemas/api_key.py tests/unit/test_api_key_provider_id.py
git commit -m "feat: add provider_id field to API key creation and schemas"
```

---

### Task 5: BedrockService Client Cache Pool

**Files:**
- Modify: `app/services/bedrock_service.py`
- Create: `tests/unit/test_bedrock_provider_cache.py`

**Step 1: Write the test**

Create `tests/unit/test_bedrock_provider_cache.py`:

```python
"""Tests for BedrockService multi-provider client cache."""
import json
import os
import threading
import pytest
from unittest.mock import MagicMock, patch, PropertyMock


class TestBedrockServiceGetClient:
    """Test get_client() returns correct client for provider_id."""

    def test_get_client_no_provider_returns_default(self):
        """No provider_id → default self.client."""
        from app.services.bedrock_service import BedrockService

        with patch.object(BedrockService, '__init__', lambda self, **kwargs: None):
            service = BedrockService.__new__(BedrockService)
            service.client = MagicMock(name="default_client")
            service._provider_clients = {}
            service._provider_clients_lock = threading.Lock()
            service._provider_manager = None

            result = service.get_client(provider_id=None)
            assert result is service.client

    def test_get_client_with_provider_creates_and_caches(self):
        """First call with provider_id creates client, second call returns cached."""
        from app.services.bedrock_service import BedrockService

        with patch.object(BedrockService, '__init__', lambda self, **kwargs: None):
            service = BedrockService.__new__(BedrockService)
            service.client = MagicMock(name="default_client")
            service._provider_clients = {}
            service._provider_clients_lock = threading.Lock()

            mock_client = MagicMock(name="provider_client")
            service._create_provider_client = MagicMock(return_value=mock_client)

            # First call
            result1 = service.get_client(provider_id="prov-123")
            assert result1 is mock_client
            service._create_provider_client.assert_called_once_with("prov-123")

            # Second call returns cached
            result2 = service.get_client(provider_id="prov-123")
            assert result2 is mock_client
            assert service._create_provider_client.call_count == 1  # not called again

    def test_invalidate_provider_client(self):
        """invalidate_provider_client() removes cached client."""
        from app.services.bedrock_service import BedrockService

        with patch.object(BedrockService, '__init__', lambda self, **kwargs: None):
            service = BedrockService.__new__(BedrockService)
            service._provider_clients = {"prov-123": MagicMock()}
            service._provider_clients_lock = threading.Lock()

            service.invalidate_provider_client("prov-123")
            assert "prov-123" not in service._provider_clients
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_bedrock_provider_cache.py -v`
Expected: FAIL (get_client / _provider_clients don't exist yet)

**Step 3: Implement changes**

In `app/services/bedrock_service.py`, modify `BedrockService.__init__()`:

After line 94 (`config=config,`) and before the DynamoDB init section, add:

```python
        # Multi-provider client cache
        self._provider_clients: Dict[str, Any] = {}
        self._provider_clients_lock = threading.Lock()
        self._provider_manager = None  # Lazy-loaded
```

Add these methods to `BedrockService`:

```python
    def _get_provider_manager(self):
        """Lazy-load ProviderManager."""
        if self._provider_manager is None:
            from app.db.provider_manager import ProviderManager
            from app.core.config import settings
            self._provider_manager = ProviderManager(
                dynamodb_resource=self.dynamodb_client.dynamodb
                if hasattr(self.dynamodb_client, 'dynamodb')
                else self.dynamodb_client,
                table_name=settings.dynamodb_providers_table,
                encryption_secret=settings.provider_key_encryption_secret or "",
            )
        return self._provider_manager

    def get_client(self, provider_id: Optional[str] = None):
        """Get boto3 bedrock-runtime client for a provider.

        Args:
            provider_id: Provider ID, or None for default client.

        Returns:
            boto3 bedrock-runtime client
        """
        if not provider_id:
            return self.client
        with self._provider_clients_lock:
            if provider_id not in self._provider_clients:
                self._provider_clients[provider_id] = self._create_provider_client(provider_id)
            return self._provider_clients[provider_id]

    def _create_provider_client(self, provider_id: str):
        """Create a boto3 bedrock-runtime client for a specific provider."""
        mgr = self._get_provider_manager()
        provider = mgr.get_provider(provider_id)
        if not provider or not provider.get("is_active", False):
            raise ValueError(f"Provider {provider_id} not found or inactive")

        creds = mgr.get_decrypted_credentials(provider_id)
        region = provider.get("aws_region", settings.aws_region)
        endpoint_url = provider.get("endpoint_url")

        config = Config(
            read_timeout=settings.bedrock_timeout,
            connect_timeout=30,
            retries={"max_attempts": 3, "mode": "standard"},
        )

        auth_type = provider.get("auth_type", "ak_sk")

        if auth_type == "ak_sk":
            return boto3.client(
                "bedrock-runtime",
                region_name=region,
                endpoint_url=endpoint_url,
                aws_access_key_id=creds.get("access_key_id"),
                aws_secret_access_key=creds.get("secret_access_key"),
                aws_session_token=creds.get("session_token"),
                config=config,
            )
        elif auth_type == "bearer_token":
            # Bearer token: set env var, create client, then restore
            # Thread-safe via _provider_clients_lock (already held by caller)
            old_val = os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
            try:
                os.environ["AWS_BEARER_TOKEN_BEDROCK"] = creds["bearer_token"]
                return boto3.client(
                    "bedrock-runtime",
                    region_name=region,
                    endpoint_url=endpoint_url,
                    config=config,
                )
            finally:
                if old_val is not None:
                    os.environ["AWS_BEARER_TOKEN_BEDROCK"] = old_val
                else:
                    os.environ.pop("AWS_BEARER_TOKEN_BEDROCK", None)

        raise ValueError(f"Unknown auth_type: {auth_type}")

    def invalidate_provider_client(self, provider_id: str):
        """Remove a cached provider client (call when provider is updated/deleted)."""
        with self._provider_clients_lock:
            self._provider_clients.pop(provider_id, None)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_bedrock_provider_cache.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add app/services/bedrock_service.py tests/unit/test_bedrock_provider_cache.py
git commit -m "feat: add multi-provider client cache pool to BedrockService"
```

---

### Task 6: Wire provider_id Through Request Chain

**Files:**
- Modify: `app/middleware/auth.py` — ensure `api_key_info` already contains `provider_id` (no change needed; DynamoDB returns all fields)
- Modify: `app/api/messages.py` — pass `provider_id` to bedrock service calls
- Modify: `app/services/bedrock_service.py` — `invoke_model()` and `invoke_model_stream()` accept and use `provider_id`

**Step 1: Modify `invoke_model` and `invoke_model_stream` signatures**

In `app/services/bedrock_service.py`:

Add `provider_id: Optional[str] = None` parameter to:
- `invoke_model()` (line 487)
- `_invoke_model_sync()` (line 537)
- `invoke_model_stream()` (around line 866)
- `_invoke_model_stream_sync()` (the streaming sync method)
- `count_tokens()` (line 1462)

In `_invoke_model_sync`, replace `self.client.invoke_model(...)` with `self.get_client(provider_id).invoke_model(...)`.

Same pattern for streaming: replace `self.client.invoke_model_with_response_stream(...)` with `self.get_client(provider_id).invoke_model_with_response_stream(...)`.

For Converse API calls: replace `self.client.converse(...)` and `self.client.converse_stream(...)` with `self.get_client(provider_id).converse(...)` and `self.get_client(provider_id).converse_stream(...)`.

**Step 2: Modify `app/api/messages.py`**

In `create_message()`, extract `provider_id` from `api_key_info`:

```python
    provider_id = api_key_info.get("provider_id") if api_key_info else None
```

Pass `provider_id=provider_id` to all `bedrock_service.invoke_model()` and `bedrock_service.invoke_model_stream()` calls in the function.

**Step 3: Run all tests**

Run: `uv run pytest tests/ -v --timeout=30`
Expected: All PASS (existing tests pass provider_id=None by default)

**Step 4: Commit**

```bash
git add app/services/bedrock_service.py app/api/messages.py
git commit -m "feat: wire provider_id through request chain to Bedrock client"
```

---

### Task 7: Admin Portal Backend — Provider CRUD API

**Files:**
- Create: `admin_portal/backend/api/providers.py`
- Create: `admin_portal/backend/schemas/provider.py`
- Modify: `admin_portal/backend/main.py` — register provider routes

**Step 1: Create admin schemas**

Create `admin_portal/backend/schemas/provider.py`:

```python
"""Admin portal schemas for Provider management."""
from app.schemas.provider import ProviderCreate, ProviderUpdate, ProviderResponse, ProviderListResponse

# Re-export from core schemas — admin uses same models
__all__ = ["ProviderCreate", "ProviderUpdate", "ProviderResponse", "ProviderListResponse"]
```

**Step 2: Create provider routes**

Create `admin_portal/backend/api/providers.py`:

```python
"""Provider management routes for admin portal."""
import sys
from pathlib import Path
from typing import Optional

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


def get_provider_manager() -> ProviderManager:
    db = DynamoDBClient()
    return ProviderManager(
        dynamodb_resource=db.dynamodb,
        table_name=settings.dynamodb_providers_table,
        encryption_secret=settings.provider_key_encryption_secret or "",
    )


@router.get("", response_model=ProviderListResponse)
async def list_providers():
    """List all providers."""
    mgr = get_provider_manager()
    items = mgr.list_providers()
    return ProviderListResponse(
        items=[ProviderResponse(**item) for item in items],
        count=len(items),
    )


@router.get("/{provider_id}", response_model=ProviderResponse)
async def get_provider(provider_id: str):
    """Get provider details (credentials masked)."""
    mgr = get_provider_manager()
    item = mgr.get_provider(provider_id)
    if not item:
        raise HTTPException(status_code=404, detail="Provider not found")
    return ProviderResponse(**item)


@router.post("", response_model=ProviderResponse, status_code=status.HTTP_201_CREATED)
async def create_provider(request: ProviderCreate):
    """Create a new provider."""
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
    """Update a provider."""
    mgr = get_provider_manager()
    existing = mgr.get_provider(provider_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Provider not found")

    update_data = request.model_dump(exclude_none=True)
    if update_data:
        mgr.update_provider(provider_id, **update_data)

    # Invalidate cached client
    try:
        from app.services.bedrock_service import BedrockService
        # Note: This only works if there's a singleton. For now, cache is per-instance.
        # In production, consider a shared invalidation mechanism.
    except ImportError:
        pass

    item = mgr.get_provider(provider_id)
    return ProviderResponse(**item)


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(provider_id: str):
    """Delete a provider. Fails if API keys reference it."""
    mgr = get_provider_manager()
    existing = mgr.get_provider(provider_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Provider not found")

    # Check if any API keys reference this provider
    db = DynamoDBClient()
    api_key_mgr = APIKeyManager(db)
    all_keys = api_key_mgr.list_all_api_keys(limit=1000)
    referencing = [
        k for k in all_keys.get("items", [])
        if k.get("provider_id") == provider_id
    ]
    if referencing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot delete: {len(referencing)} API key(s) reference this provider",
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
        import os
        if auth_type == "bearer_token":
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
                "bedrock",
                region_name=region,
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
```

**Step 3: Register routes in admin portal main**

In `admin_portal/backend/main.py`, add:

```python
from admin_portal.backend.api.providers import router as providers_router
app.include_router(providers_router, prefix="/api/providers", tags=["providers"])
```

**Step 4: Update admin API keys endpoint to pass `provider_id`**

In `admin_portal/backend/api/api_keys.py`, in `create_api_key()`, add `provider_id=request.provider_id` to the `api_key_manager.create_api_key()` call.

**Step 5: Run tests**

Run: `uv run pytest tests/ -v --timeout=30`
Expected: All PASS

**Step 6: Commit**

```bash
git add admin_portal/backend/api/providers.py admin_portal/backend/schemas/provider.py admin_portal/backend/main.py admin_portal/backend/api/api_keys.py
git commit -m "feat: add Provider CRUD API to admin portal"
```

---

### Task 8: Admin Portal Frontend — Provider Management Page

**Files:**
- Modify: `admin_portal/frontend/index.html` — add Providers tab/section and Provider dropdown to API Key form

**Step 1: Examine current frontend structure**

Read `admin_portal/frontend/index.html` to understand the existing tab structure, API call patterns, and form layout.

**Step 2: Add Providers section**

Add a new navigation tab "Providers" and a corresponding section with:
- Provider list table (name, region, auth_type, status, actions)
- Create Provider modal/form with dynamic credential fields based on auth_type
- Edit/Delete actions
- Test Connection button

**Step 3: Add Provider dropdown to API Key form**

In the API Key create/edit form, add a `<select>` that fetches from `/api/providers` and includes a "Default (Environment Config)" option.

**Step 4: Manual testing**

Start the admin portal and verify:
1. Provider list loads
2. Can create provider with bearer_token
3. Can create provider with ak_sk
4. Can test connection
5. API Key form shows provider dropdown
6. Creating API key with provider_id works

**Step 5: Commit**

```bash
git add admin_portal/frontend/index.html
git commit -m "feat: add Provider management UI and provider selector to API Key form"
```

---

### Task 9: Integration Test

**Files:**
- Create: `tests/integration/test_multi_provider.py`

**Step 1: Write integration test**

```python
"""Integration test for multi-provider flow."""
import pytest
from moto import mock_aws
from fastapi.testclient import TestClient


@mock_aws
def test_full_provider_flow():
    """Test: create provider → create API key with provider → verify routing."""
    import app.core.config as cfg
    cfg.settings.dynamodb_endpoint_url = None
    cfg.settings.aws_region = "us-east-1"
    cfg.settings.provider_key_encryption_secret = "test-secret"
    cfg.settings.require_api_key = False

    from app.db.dynamodb import DynamoDBClient
    db = DynamoDBClient()
    db.create_tables()

    from app.db.provider_manager import ProviderManager
    mgr = ProviderManager(
        dynamodb_resource=db.dynamodb,
        table_name=cfg.settings.dynamodb_providers_table,
        encryption_secret="test-secret",
    )

    # Create provider
    provider = mgr.create_provider(
        name="Test Provider",
        aws_region="us-west-2",
        auth_type="ak_sk",
        credentials={"access_key_id": "AKID", "secret_access_key": "SECRET"},
    )

    # Create API key with provider
    from app.db.dynamodb import APIKeyManager
    key_mgr = APIKeyManager(db)
    api_key = key_mgr.create_api_key(
        user_id="test", name="Test Key",
        provider_id=provider["provider_id"],
    )

    # Verify key has provider_id
    info = key_mgr.validate_api_key(api_key)
    assert info["provider_id"] == provider["provider_id"]
```

**Step 2: Run integration test**

Run: `uv run pytest tests/integration/test_multi_provider.py -v`
Expected: PASS

**Step 3: Run full test suite**

Run: `uv run pytest tests/ -v --timeout=60`
Expected: All PASS

**Step 4: Commit**

```bash
git add tests/integration/test_multi_provider.py
git commit -m "test: add integration test for multi-provider flow"
```

---

### Summary of Implementation Order

| Task | Component | Files Changed |
|------|-----------|---------------|
| 1 | Provider Pydantic schemas | `app/schemas/provider.py` |
| 2 | ProviderManager (DynamoDB CRUD) | `app/db/provider_manager.py` |
| 3 | Config + DynamoDB table registration | `app/core/config.py`, `app/db/dynamodb.py`, `scripts/setup_tables.py` |
| 4 | API Key provider_id field | `app/db/dynamodb.py`, `admin_portal/backend/schemas/api_key.py` |
| 5 | BedrockService client cache pool | `app/services/bedrock_service.py` |
| 6 | Wire provider_id through request chain | `app/api/messages.py`, `app/services/bedrock_service.py` |
| 7 | Admin Portal backend API | `admin_portal/backend/api/providers.py`, `admin_portal/backend/main.py` |
| 8 | Admin Portal frontend | `admin_portal/frontend/index.html` |
| 9 | Integration test | `tests/integration/test_multi_provider.py` |
