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
