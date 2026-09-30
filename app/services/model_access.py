"""Explicit, request-owned model authorization and target pinning.

Never attach this facade to app.state or a shared provider/service. Tool loops
receive it as their existing bedrock_service argument; executor workers receive
its immutable policy and PreparedModel explicitly, not via ContextVar.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

from app.core.access_policy import AccessPolicyDenied, ParsedAccessPolicy, require_model

ModelAPI = Literal["native", "converse", "runtime", "chat", "responses"]


@dataclass(frozen=True, slots=True)
class PreparedModel:
    target: str
    api: ModelAPI


@dataclass(frozen=True)
class ModelAccessService:
    """One admitted request's facade, including all its tool iterations.

    Only the local target memo is mutable. The admitted policy is frozen; no
    authorization result or target memo is shared between requests.
    """

    service: Any
    policy: ParsedAccessPolicy
    provider_id: str | None = None
    _targets: dict[str, PreparedModel] = field(default_factory=dict, init=False)

    def prepare(self, model: str) -> PreparedModel:
        if model not in self._targets:
            self._targets[model] = self.service.prepare_model(model, self.policy)
        prepared = self._targets[model]
        require_model(self.policy, prepared.target)
        return prepared

    def bind(self, model: str, prepared: PreparedModel) -> None:
        require_model(self.policy, prepared.target)
        self._targets[model] = prepared

    def allows_candidate(self, provider: str, model: str) -> bool:
        # Bedrock is currently the only implemented inference provider. Unknown
        # adapters cannot claim an exact wire-target contract.
        if provider != "bedrock":
            return False
        try:
            self.prepare(model)
        except AccessPolicyDenied:
            return False
        return True

    async def invoke_model(self, request, *args, **kwargs):
        kwargs.update(
            access_policy=self.policy, prepared_model=self.prepare(request.model)
        )
        kwargs.setdefault("provider_id", self.provider_id)
        return await self.service.invoke_model(request, *args, **kwargs)

    async def invoke_model_stream(self, request, *args, **kwargs):
        kwargs.update(
            access_policy=self.policy, prepared_model=self.prepare(request.model)
        )
        kwargs.setdefault("provider_id", self.provider_id)
        async for event in self.service.invoke_model_stream(request, *args, **kwargs):
            yield event

    async def count_tokens(self, request, **kwargs):
        prepared = self.service.prepare_count_model(request.model, self.policy)
        kwargs.update(access_policy=self.policy, prepared_model=prepared)
        kwargs.setdefault("provider_id", self.provider_id)
        return await self.service.count_tokens(request, **kwargs)


def preflight_model(service: Any, model: str, state: Any = None) -> None:
    """Authorize before tool side effects, including a saved PTC target.

    Legacy PTC state has only a model name: resolve/check it under this request's
    snapshot. New state pins the historical literal target AND API mode.
    """
    if not isinstance(service, ModelAccessService):
        return
    if state is not None and state.original_model:
        model = state.original_model
        if state.original_target and state.original_api:
            service.bind(
                model, PreparedModel(state.original_target, state.original_api)
            )
    service.prepare(model)


class SavedModelFields(TypedDict, total=False):
    original_target: str
    original_api: ModelAPI


def saved_model_fields(service: Any, model: str) -> SavedModelFields:
    """Save a target, never the earlier request's authorization policy."""
    if not isinstance(service, ModelAccessService):
        return {}
    prepared = service.prepare(model)
    return {"original_target": prepared.target, "original_api": prepared.api}


def model_denial_event(exc: AccessPolicyDenied) -> str:
    return (
        "event: error\ndata: "
        + json.dumps(
            {
                "type": "error",
                "error": {"type": "permission_error", "message": str(exc)},
            }
        )
        + "\n\n"
    )


async def guard_model_stream(events, response_model: str):
    """Terminate late denials and retain the client name on tool/provider SSE."""
    try:
        async for event in events:
            if event.startswith("event: message_start\n"):
                payload = json.loads(event.split("data: ", 1)[1])
                payload["message"]["model"] = response_model
                event = f"event: message_start\ndata: {json.dumps(payload)}\n\n"
            yield event
    except AccessPolicyDenied as exc:
        yield model_denial_event(exc)
