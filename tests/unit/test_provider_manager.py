"""Tests for ProviderManager."""
import pytest
from moto import mock_aws

from app.db.provider_manager import ProviderManager


@pytest.fixture
def encryption_secret():
    return "test-encryption-secret-for-unit-tests"


@pytest.fixture
def mock_dynamodb(encryption_secret):
    with mock_aws():
        import boto3
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        dynamodb.create_table(
            TableName="anthropic-proxy-providers",
            KeySchema=[{"AttributeName": "provider_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "provider_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield dynamodb


@pytest.fixture
def manager(mock_dynamodb, encryption_secret):
    return ProviderManager(
        dynamodb_resource=mock_dynamodb,
        table_name="anthropic-proxy-providers",
        encryption_secret=encryption_secret,
    )


class TestProviderManagerCreate:
    def test_create_bearer_token_provider(self, manager):
        provider = manager.create_provider(
            name="Prod US", aws_region="us-east-1",
            auth_type="bearer_token", credentials={"bearer_token": "my-secret-token"},
        )
        assert provider["provider_id"]
        assert provider["name"] == "Prod US"
        assert provider["auth_type"] == "bearer_token"
        assert provider["is_active"] is True
        assert "encrypted_credentials" in provider

    def test_create_ak_sk_provider(self, manager):
        provider = manager.create_provider(
            name="Staging EU", aws_region="eu-west-1", auth_type="ak_sk",
            credentials={"access_key_id": "AKIAEXAMPLE", "secret_access_key": "SECRET123", "session_token": "TOKEN456"},
        )
        assert provider["aws_region"] == "eu-west-1"

    def test_create_with_endpoint_url(self, manager):
        provider = manager.create_provider(
            name="Custom", aws_region="us-west-2", auth_type="bearer_token",
            credentials={"bearer_token": "tok"}, endpoint_url="https://custom.bedrock.endpoint",
        )
        assert provider["endpoint_url"] == "https://custom.bedrock.endpoint"


class TestProviderManagerRead:
    def test_get_provider(self, manager):
        created = manager.create_provider(name="Test", aws_region="us-east-1", auth_type="bearer_token", credentials={"bearer_token": "tok"})
        fetched = manager.get_provider(created["provider_id"])
        assert fetched["name"] == "Test"

    def test_get_nonexistent_returns_none(self, manager):
        assert manager.get_provider("nonexistent-id") is None

    def test_list_providers(self, manager):
        manager.create_provider(name="A", aws_region="us-east-1", auth_type="bearer_token", credentials={"bearer_token": "t1"})
        manager.create_provider(name="B", aws_region="eu-west-1", auth_type="ak_sk", credentials={"access_key_id": "AK", "secret_access_key": "SK"})
        result = manager.list_providers()
        assert len(result) == 2

    def test_get_decrypted_credentials(self, manager):
        created = manager.create_provider(name="Test", aws_region="us-east-1", auth_type="bearer_token", credentials={"bearer_token": "my-secret"})
        creds = manager.get_decrypted_credentials(created["provider_id"])
        assert creds == {"bearer_token": "my-secret"}


class TestProviderManagerUpdate:
    def test_update_name(self, manager):
        created = manager.create_provider(name="Old", aws_region="us-east-1", auth_type="bearer_token", credentials={"bearer_token": "tok"})
        manager.update_provider(created["provider_id"], name="New")
        fetched = manager.get_provider(created["provider_id"])
        assert fetched["name"] == "New"

    def test_update_credentials(self, manager):
        created = manager.create_provider(name="Test", aws_region="us-east-1", auth_type="bearer_token", credentials={"bearer_token": "old-tok"})
        manager.update_provider(created["provider_id"], credentials={"bearer_token": "new-tok"})
        creds = manager.get_decrypted_credentials(created["provider_id"])
        assert creds == {"bearer_token": "new-tok"}

    def test_deactivate(self, manager):
        created = manager.create_provider(name="Test", aws_region="us-east-1", auth_type="bearer_token", credentials={"bearer_token": "tok"})
        manager.update_provider(created["provider_id"], is_active=False)
        fetched = manager.get_provider(created["provider_id"])
        assert fetched["is_active"] is False


class TestProviderManagerDelete:
    def test_delete_provider(self, manager):
        created = manager.create_provider(name="Test", aws_region="us-east-1", auth_type="bearer_token", credentials={"bearer_token": "tok"})
        manager.delete_provider(created["provider_id"])
        assert manager.get_provider(created["provider_id"]) is None

    def test_delete_nonexistent_returns_false(self, manager):
        assert manager.delete_provider("nonexistent") is False


class TestProviderManagerMasking:
    def test_mask_bearer_token(self, manager):
        created = manager.create_provider(name="Test", aws_region="us-east-1", auth_type="bearer_token", credentials={"bearer_token": "abcdefghijklmnop"})
        fetched = manager.get_provider(created["provider_id"])
        assert fetched["masked_credentials"] == "abcd****mnop"

    def test_mask_ak_sk(self, manager):
        created = manager.create_provider(name="Test", aws_region="us-east-1", auth_type="ak_sk", credentials={"access_key_id": "AKIAEXAMPLE", "secret_access_key": "SuperSecretKey123"})
        fetched = manager.get_provider(created["provider_id"])
        assert "AKIAEXAMPLE" in fetched["masked_credentials"]
        assert "****" in fetched["masked_credentials"]


class TestProviderCredentialEncryptionIsMandatory:
    def test_create_without_secret_refuses_plaintext(self, mock_dynamodb):
        from app.db.provider_manager import ProviderEncryptionNotConfigured

        mgr = ProviderManager(dynamodb_resource=mock_dynamodb, encryption_secret=None)
        with pytest.raises(ProviderEncryptionNotConfigured):
            mgr.create_provider(
                name="x", aws_region="us-east-1",
                auth_type="bearer_token", credentials={"bearer_token": "tok"},
            )
        assert mgr.list_providers() == []

    def test_stored_credentials_are_not_plaintext(self, manager, mock_dynamodb):
        p = manager.create_provider(
            name="x", aws_region="us-east-1",
            auth_type="ak_sk",
            credentials={"access_key_id": "AKIAEXAMPLE", "secret_access_key": "s3cr3t"},
        )
        raw = mock_dynamodb.Table("anthropic-proxy-providers").get_item(
            Key={"provider_id": p["provider_id"]})["Item"]["encrypted_credentials"]
        assert "s3cr3t" not in raw and "AKIAEXAMPLE" not in raw

    def test_legacy_plaintext_row_still_readable(self, manager, mock_dynamodb):
        mock_dynamodb.Table("anthropic-proxy-providers").put_item(Item={
            "provider_id": "legacy",
            "encrypted_credentials": '{"bearer_token": "old"}',
        })
        assert manager.get_decrypted_credentials("legacy") == {"bearer_token": "old"}
