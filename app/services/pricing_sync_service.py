"""
Model pricing sync from the LiteLLM price table.

Pulls https://github.com/BerriAI/litellm model_prices_and_context_window.json
(URL configurable via PRICING_SYNC_URL) and upserts Bedrock model pricing into
the anthropic-proxy-model-pricing table.

Rules:
- Only entries whose litellm_provider is in PRICING_SYNC_PROVIDERS and whose
  mode is chat/responses are imported. LiteLLM costs are USD per token; the
  pricing table stores USD per 1M tokens.
- Rows created by the sync are marked pricing_source="litellm" and are kept in
  sync on subsequent runs. Rows without that marker (manually created or since
  edited in the admin portal) are never touched unless overwrite_manual=True.
- Existing rows whose model_id is a region-prefixed variant (e.g.
  "global.anthropic....") are matched against the un-prefixed source entry.
- Rows are never deleted.
"""

import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_MANTLE_KEY_PREFIX = "bedrock_mantle/"
_REGION_PREFIXES = (
    "us.",
    "eu.",
    "apac.",
    "global.",
    "jp.",
    "au.",
    "ca.",
    "sa.",
    "us-gov.",
)
_SYNCED_MODES = {"chat", "responses"}
_PRICE_FIELDS = ("input_price", "output_price", "cache_read_price", "cache_write_price")

_VENDOR_LABELS = {
    "ai21": "AI21",
    "amazon": "Amazon",
    "anthropic": "Anthropic",
    "cohere": "Cohere",
    "deepseek": "DeepSeek",
    "google": "Google",
    "meta": "Meta",
    "minimax": "MiniMax",
    "mistral": "Mistral",
    "moonshotai": "Moonshot AI",
    "nvidia": "NVIDIA",
    "openai": "OpenAI",
    "qwen": "Qwen",
    "writer": "Writer",
    "xai": "xAI",
}


def fetch_litellm_pricing(
    url: Optional[str] = None, timeout: float = 30.0
) -> Dict[str, Any]:
    """Download and parse the LiteLLM pricing JSON."""
    source_url = url or settings.pricing_sync_url
    response = httpx.get(source_url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError(
            f"Unexpected pricing payload from {source_url}: not a JSON object"
        )
    return data


def _to_price_per_million(cost_per_token: Any) -> Optional[Decimal]:
    """Convert a LiteLLM per-token cost to USD per 1M tokens."""
    if cost_per_token is None:
        return None
    try:
        cost = Decimal(str(cost_per_token))
    except (InvalidOperation, ValueError):
        return None
    if cost < 0:
        return None
    return cost * Decimal(1_000_000)


def _strip_region_prefix(model_id: str) -> str:
    for prefix in _REGION_PREFIXES:
        if model_id.startswith(prefix):
            return model_id[len(prefix) :]
    return model_id


def _vendor_label(model_id: str) -> str:
    segment = _strip_region_prefix(model_id).split(".", 1)[0]
    return _VENDOR_LABELS.get(segment.lower(), segment.capitalize() or "Unknown")


def extract_bedrock_pricing(
    data: Dict[str, Any], providers: Optional[Iterable[str]] = None
) -> Dict[str, Dict[str, Optional[Decimal]]]:
    """
    Extract {bedrock_model_id: prices-per-1M} from the LiteLLM payload.

    Keys containing "/" are LiteLLM region-scoped aliases, not Bedrock model
    IDs — skipped, except "bedrock_mantle/<id>" where <id> is exactly the
    model ID the OpenAI passthrough uses.
    """
    provider_set = set(
        providers if providers is not None else settings.pricing_sync_providers
    )
    prices: Dict[str, Dict[str, Optional[Decimal]]] = {}

    for key, spec in data.items():
        if not isinstance(spec, dict) or key == "sample_spec":
            continue
        if spec.get("litellm_provider") not in provider_set:
            continue
        if spec.get("mode") not in _SYNCED_MODES:
            continue

        model_id = key
        if "/" in model_id:
            if not model_id.startswith(_MANTLE_KEY_PREFIX):
                continue
            model_id = model_id[len(_MANTLE_KEY_PREFIX) :]
        # Bedrock model IDs always carry a vendor prefix ("anthropic.", "amazon.", ...)
        if (
            "." not in _strip_region_prefix(model_id).split(":", 1)[0]
            or "@" in model_id
        ):
            continue

        input_price = _to_price_per_million(spec.get("input_cost_per_token"))
        output_price = _to_price_per_million(spec.get("output_cost_per_token"))
        if input_price is None or output_price is None:
            continue

        prices[model_id] = {
            "input_price": input_price,
            "output_price": output_price,
            "cache_read_price": _to_price_per_million(
                spec.get("cache_read_input_token_cost")
            ),
            "cache_write_price": _to_price_per_million(
                spec.get("cache_creation_input_token_cost")
            ),
        }

    return prices


def _lookup_price(
    prices: Dict[str, Dict[str, Optional[Decimal]]], model_id: str
) -> Optional[Dict[str, Optional[Decimal]]]:
    """Find prices for a model ID: exact match, then region-stripped, then the us. variant."""
    if model_id in prices:
        return prices[model_id]
    stripped = _strip_region_prefix(model_id)
    if stripped in prices:
        return prices[stripped]
    return prices.get(f"us.{stripped}")


def _list_existing(pricing_manager) -> Dict[str, Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    last_key = None
    while True:
        page = pricing_manager.list_all_pricing(limit=1000, last_key=last_key)
        items.extend(page.get("items", []))
        last_key = page.get("last_key")
        if not last_key:
            break
    return {item["model_id"]: item for item in items}


def _price_changes(
    existing: Dict[str, Any], target: Dict[str, Optional[Decimal]]
) -> Dict[str, Decimal]:
    """Fields whose source price differs from the stored one. Never nulls out a stored price."""
    changes = {}
    for field in _PRICE_FIELDS:
        new_value = target.get(field)
        if new_value is None:
            continue
        old_value = existing.get(field)
        if old_value is None or Decimal(str(old_value)) != new_value:
            changes[field] = new_value
    return changes


def sync_pricing(
    pricing_manager,
    source_prices: Dict[str, Dict[str, Optional[Decimal]]],
    create_missing: bool = True,
    overwrite_manual: bool = False,
    dry_run: bool = False,
    extra_model_ids: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """
    Reconcile the pricing table against extracted source prices.

    Args:
        pricing_manager: ModelPricingManager instance
        source_prices: Output of extract_bedrock_pricing()
        create_missing: Create rows for source models not yet in the table
        overwrite_manual: Also update rows without pricing_source="litellm"
        dry_run: Compute the summary without writing anything
        extra_model_ids: Model IDs that must have pricing (e.g. model-mapping
            targets); created via region-prefix fallback lookup if missing

    Returns:
        Summary dict: created/updated/skipped_manual (model ID lists),
        unchanged (count), not_found (extra IDs with no source price)
    """
    existing = _list_existing(pricing_manager)
    created: List[str] = []
    updated: List[str] = []
    skipped_manual: List[str] = []
    not_found: List[str] = []
    unchanged = 0

    def apply_update(
        model_id: str, row: Dict[str, Any], target: Dict[str, Optional[Decimal]]
    ):
        nonlocal unchanged
        if row.get("pricing_source") != "litellm" and not overwrite_manual:
            skipped_manual.append(model_id)
            return
        changes = _price_changes(row, target)
        if not changes:
            unchanged += 1
            return
        if not dry_run:
            pricing_manager.update_pricing(model_id, **changes)
        updated.append(model_id)

    def create(model_id: str, target: Dict[str, Optional[Decimal]]):
        if not dry_run:
            pricing_manager.create_pricing(
                model_id=model_id,
                provider=_vendor_label(model_id),
                input_price=target["input_price"],
                output_price=target["output_price"],
                cache_read_price=target.get("cache_read_price"),
                cache_write_price=target.get("cache_write_price"),
                pricing_source="litellm",
            )
        created.append(model_id)

    for model_id, target in source_prices.items():
        row = existing.get(model_id)
        if row is not None:
            apply_update(model_id, row, target)
        elif create_missing:
            create(model_id, target)

    # Existing rows not directly keyed in the source (region-prefixed variants)
    for model_id, row in existing.items():
        if model_id in source_prices:
            continue
        fallback = _lookup_price(source_prices, model_id)
        if fallback is not None:
            apply_update(model_id, row, fallback)

    seen = set(existing) | set(created)
    for model_id in extra_model_ids or []:
        if not model_id or model_id in seen or model_id.startswith("arn:"):
            continue
        seen.add(model_id)
        fallback = _lookup_price(source_prices, model_id)
        if fallback is None:
            not_found.append(model_id)
        else:
            create(model_id, fallback)

    return {
        "created": created,
        "updated": updated,
        "skipped_manual": skipped_manual,
        "unchanged": unchanged,
        "not_found": not_found,
        "dry_run": dry_run,
    }


def run_sync(
    url: Optional[str] = None,
    providers: Optional[Iterable[str]] = None,
    create_missing: Optional[bool] = None,
    overwrite_manual: Optional[bool] = None,
    dry_run: bool = False,
    include_mapped_models: bool = True,
) -> Dict[str, Any]:
    """
    Fetch the LiteLLM price table and sync it into DynamoDB.

    Blocking (httpx + boto3); call from async code via run_in_executor.
    None-valued options fall back to the PRICING_SYNC_* settings.
    """
    from app.db.dynamodb import DynamoDBClient, ModelMappingManager, ModelPricingManager

    source_url = url or settings.pricing_sync_url
    data = fetch_litellm_pricing(source_url)
    source_prices = extract_bedrock_pricing(data, providers)

    client = DynamoDBClient()
    pricing_manager = ModelPricingManager(client)

    extra_model_ids: List[str] = []
    if include_mapped_models:
        extra_model_ids.extend(settings.default_model_mapping.values())
        try:
            mappings = ModelMappingManager(client).list_mappings()
            extra_model_ids.extend(
                m["bedrock_model_id"] for m in mappings if m.get("bedrock_model_id")
            )
        except Exception as e:
            logger.warning(f"Pricing sync: failed to list custom model mappings: {e}")

    summary = sync_pricing(
        pricing_manager,
        source_prices,
        create_missing=(
            settings.pricing_sync_create_missing
            if create_missing is None
            else create_missing
        ),
        overwrite_manual=(
            settings.pricing_sync_overwrite_manual
            if overwrite_manual is None
            else overwrite_manual
        ),
        dry_run=dry_run,
        extra_model_ids=extra_model_ids,
    )
    summary["source_url"] = source_url
    summary["source_models"] = len(source_prices)
    logger.info(
        "Pricing sync%s: %d source models, %d created, %d updated, %d unchanged, "
        "%d manual rows skipped, %d mapped models without source pricing",
        " (dry run)" if dry_run else "",
        summary["source_models"],
        len(summary["created"]),
        len(summary["updated"]),
        summary["unchanged"],
        len(summary["skipped_manual"]),
        len(summary["not_found"]),
    )
    return summary
