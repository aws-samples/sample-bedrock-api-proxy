"""Integration test for multi-provider flow."""
import pytest
from unittest.mock import patch
from moto import mock_aws


@mock_aws
def test_full_provider_flow():
    """Test: create provider -> create API key with provider -> verify routing."""
    # Patch settings before importing modules that use them
    with patch("app.core.config.settings.dynamodb_endpoint_url", None), \
         patch("app.core.config.settings.aws_region", "us-east-1"), \
         patch("app.core.config.settings.provider_key_encryption_secret", "test-secret"), \
         patch("app.core.config.settings.require_api_key", False):

        from app.db.dynamodb import DynamoDBClient, APIKeyManager
        from app.db.provider_manager import ProviderManager
        from app.core.config import settings

        db = DynamoDBClient()
        db.create_tables()

        mgr = ProviderManager(
            dynamodb_resource=db.dynamodb,
            table_name=settings.dynamodb_providers_table,
            encryption_secret="test-secret",
        )

        # Create provider
        provider = mgr.create_provider(
            name="Test Provider",
            aws_region="us-west-2",
            auth_type="ak_sk",
            credentials={"access_key_id": "AKID", "secret_access_key": "SECRET"},
        )
        assert provider["provider_id"]
        assert provider["is_active"] is True

        # Create API key with provider
        key_mgr = APIKeyManager(db)
        api_key = key_mgr.create_api_key(
            user_id="test",
            name="Test Key",
            provider_id=provider["provider_id"],
        )

        # Verify key has provider_id
        info = key_mgr.validate_api_key(api_key)
        assert info is not None
        assert info["provider_id"] == provider["provider_id"]

        # Verify credentials can be decrypted
        creds = mgr.get_decrypted_credentials(provider["provider_id"])
        assert creds == {"access_key_id": "AKID", "secret_access_key": "SECRET"}


@mock_aws
def test_provider_with_bearer_token():
    """Test bearer token provider flow."""
    with patch("app.core.config.settings.dynamodb_endpoint_url", None), \
         patch("app.core.config.settings.aws_region", "us-east-1"), \
         patch("app.core.config.settings.provider_key_encryption_secret", "test-secret"):

        from app.db.dynamodb import DynamoDBClient
        from app.db.provider_manager import ProviderManager
        from app.core.config import settings

        db = DynamoDBClient()
        db.create_tables()

        mgr = ProviderManager(
            dynamodb_resource=db.dynamodb,
            table_name=settings.dynamodb_providers_table,
            encryption_secret="test-secret",
        )

        provider = mgr.create_provider(
            name="Bearer Token Provider",
            aws_region="us-east-1",
            auth_type="bearer_token",
            credentials={"bearer_token": "my-bearer-token-123"},
        )

        # Verify encrypted in storage, decryptable
        raw = mgr.get_provider(provider["provider_id"])
        assert "my-bearer-token-123" not in raw["encrypted_credentials"]

        creds = mgr.get_decrypted_credentials(provider["provider_id"])
        assert creds == {"bearer_token": "my-bearer-token-123"}


@mock_aws
def test_api_key_without_provider():
    """Test backward compatibility -- API key without provider_id."""
    with patch("app.core.config.settings.dynamodb_endpoint_url", None), \
         patch("app.core.config.settings.aws_region", "us-east-1"):

        from app.db.dynamodb import DynamoDBClient, APIKeyManager

        db = DynamoDBClient()
        db.create_tables()

        key_mgr = APIKeyManager(db)
        api_key = key_mgr.create_api_key(
            user_id="test",
            name="Default Key",
        )

        info = key_mgr.validate_api_key(api_key)
        assert info is not None
        assert info.get("provider_id") is None


@mock_aws
def test_provider_crud_lifecycle():
    """Test full CRUD lifecycle of a provider."""
    with patch("app.core.config.settings.dynamodb_endpoint_url", None), \
         patch("app.core.config.settings.aws_region", "us-east-1"), \
         patch("app.core.config.settings.provider_key_encryption_secret", "test-secret"):

        from app.db.dynamodb import DynamoDBClient
        from app.db.provider_manager import ProviderManager
        from app.core.config import settings

        db = DynamoDBClient()
        db.create_tables()

        mgr = ProviderManager(
            dynamodb_resource=db.dynamodb,
            table_name=settings.dynamodb_providers_table,
            encryption_secret="test-secret",
        )

        # Create
        provider = mgr.create_provider(
            name="Lifecycle Test",
            aws_region="eu-west-1",
            auth_type="ak_sk",
            credentials={"access_key_id": "AK1", "secret_access_key": "SK1"},
        )
        pid = provider["provider_id"]

        # Read
        fetched = mgr.get_provider(pid)
        assert fetched["name"] == "Lifecycle Test"

        # Update
        mgr.update_provider(pid, name="Updated Name", aws_region="eu-central-1")
        updated = mgr.get_provider(pid)
        assert updated["name"] == "Updated Name"
        assert updated["aws_region"] == "eu-central-1"

        # Update credentials
        mgr.update_provider(pid, credentials={"access_key_id": "AK2", "secret_access_key": "SK2"})
        creds = mgr.get_decrypted_credentials(pid)
        assert creds["access_key_id"] == "AK2"

        # List
        providers = mgr.list_providers()
        assert len(providers) == 1

        # Delete
        mgr.delete_provider(pid)
        assert mgr.get_provider(pid) is None
