"""Routing configuration management routes."""
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from fastapi import APIRouter, HTTPException, status

from app.db.dynamodb import DynamoDBClient, RoutingConfigManager, SmartRoutingConfigManager
from admin_portal.backend.schemas.routing import (
    RoutingRuleCreate,
    RoutingRuleUpdate,
    RoutingRuleResponse,
    RuleReorderRequest,
    SmartRoutingConfig,
)

router = APIRouter()


def _get_rule_manager():
    return RoutingConfigManager(DynamoDBClient())


def _get_smart_config_manager():
    return SmartRoutingConfigManager(DynamoDBClient())


@router.get("/rules", response_model=List[RoutingRuleResponse])
async def list_routing_rules():
    """List all routing rules sorted by priority."""
    mgr = _get_rule_manager()
    items = mgr.list_rules()
    return [
        RoutingRuleResponse(
            rule_id=r["rule_id"],
            rule_name=r["rule_name"],
            rule_type=r["rule_type"],
            pattern=r["pattern"],
            target_model=r["target_model"],
            target_provider=r.get("target_provider", "bedrock"),
            priority=int(r.get("priority", 0)),
            is_enabled=r.get("is_enabled", True),
            created_at=r.get("created_at", ""),
            updated_at=r.get("updated_at"),
        )
        for r in items
    ]


@router.post("/rules", response_model=RoutingRuleResponse, status_code=status.HTTP_201_CREATED)
async def create_routing_rule(body: RoutingRuleCreate):
    """Create a new routing rule."""
    mgr = _get_rule_manager()
    item = mgr.create_rule(
        rule_name=body.rule_name,
        rule_type=body.rule_type,
        pattern=body.pattern,
        target_model=body.target_model,
        target_provider=body.target_provider,
    )
    return RoutingRuleResponse(
        rule_id=item["rule_id"],
        rule_name=item["rule_name"],
        rule_type=item["rule_type"],
        pattern=item["pattern"],
        target_model=item["target_model"],
        target_provider=item.get("target_provider", "bedrock"),
        priority=int(item.get("priority", 0)),
        is_enabled=item.get("is_enabled", True),
        created_at=item.get("created_at", ""),
        updated_at=item.get("updated_at"),
    )


@router.put("/rules/reorder", response_model=dict)
async def reorder_routing_rules(body: RuleReorderRequest):
    """Batch update rule priorities based on ordered list."""
    mgr = _get_rule_manager()
    success = mgr.reorder_rules(body.rule_ids)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to reorder rules")
    return {"success": True}


@router.put("/rules/{rule_id}", response_model=dict)
async def update_routing_rule(rule_id: str, body: RoutingRuleUpdate):
    """Update a routing rule."""
    mgr = _get_rule_manager()
    success = mgr.update_rule(
        rule_id=rule_id,
        rule_name=body.rule_name,
        rule_type=body.rule_type,
        pattern=body.pattern,
        target_model=body.target_model,
        target_provider=body.target_provider,
        is_enabled=body.is_enabled,
    )
    if not success:
        raise HTTPException(status_code=404, detail="Routing rule not found")
    return {"success": True}


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_routing_rule(rule_id: str):
    """Delete a routing rule."""
    mgr = _get_rule_manager()
    mgr.delete_rule(rule_id)


@router.get("/smart-config", response_model=SmartRoutingConfig)
async def get_smart_routing_config():
    """Get the global smart routing configuration."""
    mgr = _get_smart_config_manager()
    config = mgr.get_config()
    if not config:
        return SmartRoutingConfig(
            strong_model="claude-sonnet-4-5-20250929",
            weak_model="claude-haiku-4-5-20251001",
            threshold=0.5,
        )
    return SmartRoutingConfig(
        strong_model=config["strong_model"],
        weak_model=config["weak_model"],
        threshold=float(config.get("threshold", 0.5)),
    )


@router.put("/smart-config", response_model=SmartRoutingConfig)
async def update_smart_routing_config(body: SmartRoutingConfig):
    """Update the global smart routing configuration."""
    mgr = _get_smart_config_manager()
    mgr.put_config(
        strong_model=body.strong_model,
        weak_model=body.weak_model,
        threshold=body.threshold,
    )
    return body
