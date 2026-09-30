"""Metadata-only Responses attribution in the existing context table.

AUTH#v1 rows never contain prompts, outputs or credentials. Only model-restricted
callers consult them. DynamoDB TTL is garbage collection, not authorization.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from botocore.exceptions import ClientError

from app.api.openai_passthrough.context_store import _api_key_hash
from app.core.access_policy import ParsedAccessPolicy, require_model
from app.core.config import settings


class ResponseAccessError(Exception):
    """Secret-safe protocol error, also usable after streaming headers."""

    def __init__(self, status_code: int = 503) -> None:
        self.status_code = status_code
        self.error_type = "api_error" if status_code == 503 else "invalid_request_error"
        super().__init__(
            {
                400: "Operation is not supported for this response",
                404: "Response was not found",
                503: "Response authorization metadata is unavailable",
            }[status_code]
        )

    def body(self) -> dict[str, Any]:
        return {"error": {"message": str(self), "type": self.error_type}}


@dataclass(frozen=True)
class BackendTarget:
    """One configuration snapshot and fresh credentials; only identity is stored."""

    base_url: str
    api_key: str | None
    identity: dict[str, str]
    extensions: dict[str, Any] = field(default_factory=dict)


class ResponseAuthorizationStore:
    """Conditional, idempotent metadata writes with strongly consistent reads."""

    def __init__(self, table: Any, ttl_seconds: int | None = None) -> None:
        self.table = table
        self.ttl_seconds = (
            settings.response_context_ttl_seconds
            if ttl_seconds is None
            else ttl_seconds
        )

    @staticmethod
    def key(response_id: str) -> dict[str, str]:
        return {"response_id": response_id, "chunk_id": "AUTH#v1"}

    def _read(self, response_id: str) -> dict[str, Any]:
        try:
            return (
                self.table.get_item(Key=self.key(response_id), ConsistentRead=True).get(
                    "Item"
                )
                or {}
            )
        except Exception:
            raise ResponseAccessError() from None

    def register(
        self,
        response_id: str,
        *,
        api_key: str,
        model: str,
        backend: dict[str, str],
        kind: str,
    ) -> None:
        now = int(time.time())
        immutable = {
            "version": 1,
            "owner": _api_key_hash(api_key),
            "model": model,
            "backend": backend,
            "kind": kind,
        }
        item = {
            **self.key(response_id),
            **immutable,
            "created_at": now,
            "expires_at": now + self.ttl_seconds,
            "deleted": False,
        }
        try:
            self.table.put_item(
                Item=item, ConditionExpression="attribute_not_exists(response_id)"
            )
            return
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise ResponseAccessError() from None
        except Exception:
            raise ResponseAccessError() from None
        old = self._read(response_id)
        # Do not extend TTL, revive a tombstone, or rebind an existing ID.
        if not self._live(old) or any(old.get(k) != v for k, v in immutable.items()):
            raise ResponseAccessError()

    @staticmethod
    def _live(item: dict[str, Any]) -> bool:
        try:
            return (
                item.get("version") == 1
                and item.get("deleted") is False
                and int(item["expires_at"]) > time.time()
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            return False

    def authorize(
        self, response_id: str, *, api_key: str, policy: ParsedAccessPolicy
    ) -> dict[str, Any]:
        item = self._read(response_id)
        if (
            not self._live(item)
            or item.get("owner") != _api_key_hash(api_key)
            or not isinstance(item.get("model"), str)
            or not isinstance(item.get("backend"), dict)
            or item.get("kind") not in ("upstream", "proxy")
        ):
            raise ResponseAccessError(404)
        require_model(policy, item["model"])
        return item

    def mark_deleted(self, item: dict[str, Any]) -> None:
        try:
            self.table.update_item(
                Key=self.key(item["response_id"]),
                UpdateExpression="SET deleted = :deleted",
                ConditionExpression="#owner = :owner AND backend = :backend AND expires_at = :expiry",
                ExpressionAttributeNames={"#owner": "owner"},
                ExpressionAttributeValues={
                    ":deleted": True,
                    ":owner": item["owner"],
                    ":backend": item["backend"],
                    ":expiry": item["expires_at"],
                },
            )
        except Exception:
            raise ResponseAccessError() from None


def response_id_from_payload(payload: Any) -> str | None:
    """Only response IDs, never output-item/function/tool-call IDs."""
    if not isinstance(payload, dict):
        return None
    event_type = payload.get("type", "")
    value = None
    if isinstance(event_type, str) and event_type.startswith("response."):
        response = payload.get("response")
        if isinstance(response, dict):
            value = response.get("id")
        value = value or payload.get("response_id")
        if event_type in {
            "response.created",
            "response.in_progress",
            "response.completed",
            "response.failed",
            "response.incomplete",
            "response.cancelled",
        }:
            value = value or payload.get("id")
    if payload.get("object") == "response":
        value = payload.get("id")
    return value if isinstance(value, str) and value else None


async def registered_sse_lines(
    resp, on_response_id, usage_callback, *, normalize=False
):
    """Gate one SSE frame, never a complete stream (supports multiline data).

    Retain frame bytes on passthrough. Restricted malformed/oversized data fails
    closed; unrestricted streams retain their historical line compatibility.
    """
    required = bool(
        isinstance(on_response_id, ResponseRegistration)
        and on_response_id.policy.model_enabled
    )
    if on_response_id is None:
        async for line in resp.aiter_lines():
            usage_callback(line)
            yield line
        return

    async def prepare_frame(lines, *, register=True):
        # SSE fields remove exactly one leading space; repeated event fields
        # overwrite, including a bare/empty `event` which resets the name.
        data = []
        event = ""
        for part in lines:
            name, _, value = part.partition(":")
            value = value.removeprefix(" ")
            if name == "data":
                data.append(value)
            elif name == "event":
                event = value
        raw = "\n".join(data)
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            if required and raw and raw != "[DONE]":
                raise ResponseAccessError() from None
            return lines
        if not isinstance(payload, dict):
            if required:
                raise ResponseAccessError()
            return lines
        # Named-event clients dispatch on `event`; data-oriented SDK clients
        # may dispatch on payload.type instead. Gate both interpretations when
        # they disagree, without changing the original passthrough bytes.
        event_response_id = response_id_from_payload({**payload, "type": event})
        if "type" not in payload:
            payload = {**payload, "type": event}
        usage_callback("data: " + json.dumps(payload))
        response = payload.get("response")
        if isinstance(response, dict) and isinstance(response.get("usage"), dict):
            # Account for observed usage even if registration fails on this frame.
            usage_callback(
                "data: "
                + json.dumps({"type": "response.completed", "response": response})
            )
        response_id = response_id_from_payload(payload)
        if register:
            for value in dict.fromkeys((response_id, event_response_id)):
                if value:
                    await on_response_id(value)
        if normalize and required:
            return ["data: " + json.dumps(payload), ""]
        return lines

    lines = []
    size = 0
    oversized = False
    source = resp.aiter_lines()
    async for line in source:
        if not required:
            # Legacy tolerates line-oriented JSON even without SSE separators.
            # Keep its usage extraction and best-effort ID capture in that case.
            usage_callback(line)
            if line.startswith("data:"):
                try:
                    single = json.loads(line[5:].strip())
                except (ValueError, TypeError):
                    single = None
                single_id = response_id_from_payload(single)
                if single_id:
                    await on_response_id(single_id)
        if oversized:
            yield line
            if not line:
                oversized = False
            continue
        lines.append(line)
        size += len(line.encode("utf-8"))
        if size > 1024 * 1024:
            if required:
                raise ResponseAccessError()
            # Best effort must not break unrestricted streams or buffer forever.
            logging.getLogger(__name__).warning("Response metadata frame too large")
            for part in lines:
                yield part
            lines, size, oversized = [], 0, bool(line)
            continue
        if line:
            continue
        try:
            prepared_lines = await prepare_frame(lines)
        except ResponseAccessError:
            # Creation may already be billed. Consume incrementally for usage,
            # but never forward another ID/success or retain the full stream.
            async def collect_usage():
                tail = []
                tail_size = 0
                async for tail_line in source:
                    if tail_line:
                        tail.append(tail_line)
                        tail_size += len(tail_line.encode("utf-8"))
                        if tail_size > 1024 * 1024:
                            return
                    else:
                        await prepare_frame(tail, register=False)
                        tail, tail_size = [], 0
                if tail:
                    await prepare_frame(tail, register=False)

            try:
                # Bound cleanup even for a provider that never completes.
                await asyncio.wait_for(collect_usage(), timeout=5.0)
            except Exception:
                pass
            raise
        for part in prepared_lines:
            yield part
        lines, size = [], 0
    if lines:
        # EOF without a separator can still carry an ID; gate it before delivery.
        for part in await prepare_frame(lines):
            yield part


class ResponseRegistration:
    """Request-owned registration callback; all blocking work stays off the loop."""

    def __init__(
        self,
        store: ResponseAuthorizationStore,
        policy: ParsedAccessPolicy,
        api_key: str,
        model: str,
        backend: dict[str, str] | None,
        kind: str = "upstream",
    ) -> None:
        self.store = store
        self.policy = policy
        self.api_key = api_key
        self.model = model
        self.backend = backend
        self.kind = kind
        # Transient; never written to DynamoDB.
        self.target: BackendTarget | None = None
        self.seen: set[str] = set()

    async def __call__(self, response_id: str) -> None:
        if response_id in self.seen:
            return
        try:
            if self.backend is None or len(self.seen) >= 100:
                raise ResponseAccessError()
            await asyncio.to_thread(
                self.store.register,
                response_id,
                api_key=self.api_key,
                model=self.model,
                backend=self.backend,
                kind=self.kind,
            )
            self.seen.add(response_id)
        except Exception:
            logging.getLogger(__name__).warning("Response metadata registration failed")
            if self.policy.model_enabled:
                raise ResponseAccessError() from None

    async def json(self, data: Any) -> None:
        # Ordinary Responses JSON sometimes omits object, but its root ID is
        # still the response ID. Never search arbitrary nested objects.
        response_id = data.get("id") if isinstance(data, dict) else None
        if isinstance(response_id, str) and response_id:
            await self(response_id)
        elif self.policy.model_enabled:
            raise ResponseAccessError()
