"""Inference profile resolution errors map to HTTP responses."""
import pytest
from botocore.exceptions import ClientError
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services.inference_profile_resolver import (
    InferenceProfileResolutionError,
)


@pytest.fixture
def client():
    """Minimal FastAPI app with the resolution error handler registered."""
    from app.main import inference_profile_resolution_handler

    app = FastAPI()
    app.add_exception_handler(
        InferenceProfileResolutionError, inference_profile_resolution_handler
    )

    @app.get("/boom/{code}")
    def boom(code: str):
        if code == "access":
            cause = ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "no"}},
                "GetInferenceProfile",
            )
        elif code == "notfound":
            cause = ClientError(
                {"Error": {"Code": "ResourceNotFoundException", "Message": "x"}},
                "GetInferenceProfile",
            )
        elif code == "validation":
            cause = ClientError(
                {"Error": {"Code": "ValidationException", "Message": "bad"}},
                "GetInferenceProfile",
            )
        else:
            cause = RuntimeError("net")
        raise InferenceProfileResolutionError("arn:x", f"fail: {code}", cause=cause)

    return TestClient(app)


def test_access_denied_maps_to_502(client):
    resp = client.get("/boom/access")
    assert resp.status_code == 502
    body = resp.json()
    assert body["error"]["type"] == "inference_profile_resolution_error"
    assert "arn:x" in body["error"]["message"]


def test_not_found_maps_to_400(client):
    resp = client.get("/boom/notfound")
    assert resp.status_code == 400
    assert resp.json()["error"]["type"] == "inference_profile_resolution_error"


def test_validation_maps_to_400(client):
    resp = client.get("/boom/validation")
    assert resp.status_code == 400


def test_generic_error_maps_to_502(client):
    resp = client.get("/boom/other")
    assert resp.status_code == 502
