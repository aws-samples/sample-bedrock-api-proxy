import importlib
from unittest.mock import MagicMock, patch


def test_openai_passthrough_provider_resolution_does_not_log_exception_secret(
    caplog, monkeypatch
):
    secret = "sk-provider-secret-DO-NOT-LOG"  # nosec B105 - fake test fixture value, not a real credential

    class FailingProviderManager:
        def get_provider(self, provider_id):
            raise RuntimeError(f"upstream credential failed: {secret}")

    router = importlib.import_module("app.api.openai_passthrough.router")

    monkeypatch.setattr(router, "_provider_manager", lambda: FailingProviderManager())

    provider_id = f"provider-{secret}"

    base_url, api_key = router._resolve_upstream_target({"provider_id": provider_id})

    assert (base_url, api_key) == (None, None)
    assert secret not in caplog.text


def test_openai_compat_constructor_does_not_print_secret_bearing_endpoint(capfd):
    secret = "endpoint-secret-DO-NOT-PRINT"  # nosec B105 - fake test fixture value, not a real credential

    with patch("app.services.openai_compat_service.OpenAI"):
        from app.services.openai_compat_service import OpenAICompatService

        OpenAICompatService(
            base_url=f"https://user:{secret}@provider.test/openai/v1?token={secret}",
            api_key="provider-key",
        )

    captured = capfd.readouterr().out
    assert secret not in captured
    assert "provider.test" not in captured


def test_bedrock_service_constructor_does_not_print_secret_bearing_endpoint(capfd):
    secret = "endpoint-secret-DO-NOT-PRINT"  # nosec B105 - fake test fixture value, not a real credential

    with (
        patch("boto3.client", return_value=MagicMock()),
        patch(
            "app.services.openai_compat_service.OpenAICompatService",
            return_value=MagicMock(),
        ),
    ):
        from app.services.bedrock_service import BedrockService

        BedrockService(
            dynamodb_client=MagicMock(),
            openai_base_url=(
                f"https://user:{secret}@provider.test/openai/v1?token={secret}"
            ),
            openai_api_key="provider-key",
            openai_use_responses=True,
        )

    captured = capfd.readouterr().out
    assert secret not in captured
    assert "provider.test" not in captured
