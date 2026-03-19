"""
DynamoDB client and table management.

Provides interfaces for interacting with DynamoDB tables for API keys,
usage tracking, and model mapping.
"""
import json
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Union
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError

from app.core.config import settings


class DynamoDBClient:
    """DynamoDB client for managing tables and operations."""

    def __init__(self):
        """Initialize DynamoDB client."""
        self.dynamodb = boto3.resource(
            "dynamodb",
            region_name=settings.aws_region,
            endpoint_url=settings.dynamodb_endpoint_url,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            aws_session_token=settings.aws_session_token,
        )

        self.api_keys_table_name = settings.dynamodb_api_keys_table
        self.usage_table_name = settings.dynamodb_usage_table
        self.model_mapping_table_name = settings.dynamodb_model_mapping_table
        self.model_pricing_table_name = settings.dynamodb_model_pricing_table
        self.usage_stats_table_name = settings.dynamodb_usage_stats_table
        self.provider_keys_table_name = settings.dynamodb_provider_keys_table
        self.routing_rules_table_name = settings.dynamodb_routing_rules_table
        self.failover_chains_table_name = settings.dynamodb_failover_chains_table
        self.smart_routing_config_table_name = settings.dynamodb_smart_routing_config_table

    def create_tables(self):
        """Create all required DynamoDB tables if they don't exist."""
        self._create_api_keys_table()
        self._create_usage_table()
        self._create_model_mapping_table()
        self._create_model_pricing_table()
        self._create_usage_stats_table()
        self._create_provider_keys_table()
        self._create_routing_rules_table()
        self._create_failover_chains_table()
        self._create_smart_routing_config_table()

    def _create_api_keys_table(self):
        """Create API keys table."""
        try:
            table = self.dynamodb.create_table(
                TableName=self.api_keys_table_name,
                KeySchema=[
                    {"AttributeName": "api_key", "KeyType": "HASH"},  # Partition key
                ],
                AttributeDefinitions=[
                    {"AttributeName": "api_key", "AttributeType": "S"},
                    {"AttributeName": "user_id", "AttributeType": "S"},
                ],
                GlobalSecondaryIndexes=[
                    {
                        "IndexName": "user_id-index",
                        "KeySchema": [
                            {"AttributeName": "user_id", "KeyType": "HASH"},
                        ],
                        "Projection": {"ProjectionType": "ALL"},
                    }
                ],
                BillingMode="PAY_PER_REQUEST",
            )
            table.wait_until_exists()
            print(f"Created table: {self.api_keys_table_name}")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceInUseException":
                print(f"Table already exists: {self.api_keys_table_name}")
            else:
                raise

    def _create_usage_table(self):
        """Create usage tracking table."""
        try:
            table = self.dynamodb.create_table(
                TableName=self.usage_table_name,
                KeySchema=[
                    {"AttributeName": "api_key", "KeyType": "HASH"},  # Partition key
                    {"AttributeName": "timestamp", "KeyType": "RANGE"},  # Sort key
                ],
                AttributeDefinitions=[
                    {"AttributeName": "api_key", "AttributeType": "S"},
                    {"AttributeName": "timestamp", "AttributeType": "S"},  # Changed to S to match CDK
                    {"AttributeName": "request_id", "AttributeType": "S"},
                ],
                GlobalSecondaryIndexes=[
                    {
                        "IndexName": "request_id-index",
                        "KeySchema": [
                            {"AttributeName": "request_id", "KeyType": "HASH"},
                        ],
                        "Projection": {"ProjectionType": "ALL"},
                    }
                ],
                BillingMode="PAY_PER_REQUEST",
            )
            table.wait_until_exists()
            print(f"Created table: {self.usage_table_name}")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceInUseException":
                print(f"Table already exists: {self.usage_table_name}")
            else:
                raise

    def _create_model_mapping_table(self):
        """Create model mapping table."""
        try:
            table = self.dynamodb.create_table(
                TableName=self.model_mapping_table_name,
                KeySchema=[
                    {
                        "AttributeName": "anthropic_model_id",
                        "KeyType": "HASH",
                    },  # Partition key
                ],
                AttributeDefinitions=[
                    {"AttributeName": "anthropic_model_id", "AttributeType": "S"},
                ],
                BillingMode="PAY_PER_REQUEST",
            )
            table.wait_until_exists()
            print(f"Created table: {self.model_mapping_table_name}")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceInUseException":
                print(f"Table already exists: {self.model_mapping_table_name}")
            else:
                raise

    def _create_model_pricing_table(self):
        """Create model pricing table for admin portal."""
        try:
            table = self.dynamodb.create_table(
                TableName=self.model_pricing_table_name,
                KeySchema=[
                    {"AttributeName": "model_id", "KeyType": "HASH"},  # Partition key
                ],
                AttributeDefinitions=[
                    {"AttributeName": "model_id", "AttributeType": "S"},
                    {"AttributeName": "provider", "AttributeType": "S"},
                ],
                GlobalSecondaryIndexes=[
                    {
                        "IndexName": "provider-index",
                        "KeySchema": [
                            {"AttributeName": "provider", "KeyType": "HASH"},
                        ],
                        "Projection": {"ProjectionType": "ALL"},
                    }
                ],
                BillingMode="PAY_PER_REQUEST",
            )
            table.wait_until_exists()
            print(f"Created table: {self.model_pricing_table_name}")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceInUseException":
                print(f"Table already exists: {self.model_pricing_table_name}")
            else:
                raise

    def _create_usage_stats_table(self):
        """Create usage stats table for aggregated token usage."""
        try:
            table = self.dynamodb.create_table(
                TableName=self.usage_stats_table_name,
                KeySchema=[
                    {"AttributeName": "api_key", "KeyType": "HASH"},  # Partition key
                ],
                AttributeDefinitions=[
                    {"AttributeName": "api_key", "AttributeType": "S"},
                ],
                BillingMode="PAY_PER_REQUEST",
            )
            table.wait_until_exists()
            print(f"Created table: {self.usage_stats_table_name}")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceInUseException":
                print(f"Table already exists: {self.usage_stats_table_name}")
            else:
                raise

    def _create_provider_keys_table(self):
        """Create provider keys table for multi-provider key management."""
        try:
            table = self.dynamodb.create_table(
                TableName=self.provider_keys_table_name,
                KeySchema=[
                    {"AttributeName": "key_id", "KeyType": "HASH"},
                ],
                AttributeDefinitions=[
                    {"AttributeName": "key_id", "AttributeType": "S"},
                    {"AttributeName": "provider", "AttributeType": "S"},
                ],
                GlobalSecondaryIndexes=[
                    {
                        "IndexName": "provider-index",
                        "KeySchema": [
                            {"AttributeName": "provider", "KeyType": "HASH"},
                        ],
                        "Projection": {"ProjectionType": "ALL"},
                    }
                ],
                BillingMode="PAY_PER_REQUEST",
            )
            table.wait_until_exists()
            print(f"Created table: {self.provider_keys_table_name}")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceInUseException":
                print(f"Table already exists: {self.provider_keys_table_name}")
            else:
                raise

    def _create_routing_rules_table(self):
        """Create routing rules table."""
        try:
            table = self.dynamodb.create_table(
                TableName=self.routing_rules_table_name,
                KeySchema=[
                    {"AttributeName": "rule_id", "KeyType": "HASH"},
                ],
                AttributeDefinitions=[
                    {"AttributeName": "rule_id", "AttributeType": "S"},
                ],
                BillingMode="PAY_PER_REQUEST",
            )
            table.wait_until_exists()
            print(f"Created table: {self.routing_rules_table_name}")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceInUseException":
                print(f"Table already exists: {self.routing_rules_table_name}")
            else:
                raise

    def _create_failover_chains_table(self):
        """Create failover chains table."""
        try:
            table = self.dynamodb.create_table(
                TableName=self.failover_chains_table_name,
                KeySchema=[
                    {"AttributeName": "source_model", "KeyType": "HASH"},
                ],
                AttributeDefinitions=[
                    {"AttributeName": "source_model", "AttributeType": "S"},
                ],
                BillingMode="PAY_PER_REQUEST",
            )
            table.wait_until_exists()
            print(f"Created table: {self.failover_chains_table_name}")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceInUseException":
                print(f"Table already exists: {self.failover_chains_table_name}")
            else:
                raise

    def _create_smart_routing_config_table(self):
        """Create smart routing config table."""
        try:
            table = self.dynamodb.create_table(
                TableName=self.smart_routing_config_table_name,
                KeySchema=[
                    {"AttributeName": "config_id", "KeyType": "HASH"},
                ],
                AttributeDefinitions=[
                    {"AttributeName": "config_id", "AttributeType": "S"},
                ],
                BillingMode="PAY_PER_REQUEST",
            )
            table.wait_until_exists()
            print(f"Created table: {self.smart_routing_config_table_name}")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceInUseException":
                print(f"Table already exists: {self.smart_routing_config_table_name}")
            else:
                raise


class APIKeyManager:
    """Manager for API key operations."""

    def __init__(self, dynamodb_client: DynamoDBClient):
        """Initialize API key manager."""
        self.dynamodb = dynamodb_client.dynamodb
        self.table = self.dynamodb.Table(dynamodb_client.api_keys_table_name)

    def create_api_key(
        self,
        user_id: str,
        name: str,
        rate_limit: Optional[int] = None,
        service_tier: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        owner_name: Optional[str] = None,
        role: Optional[str] = None,
        monthly_budget: Optional[float] = None,
        tpm_limit: Optional[int] = None,
        cache_ttl: Optional[str] = None,
        routing_strategy: Optional[str] = None,
        compression_strategy: Optional[str] = None,
    ) -> str:
        """
        Create a new API key.

        Args:
            user_id: User identifier
            name: Human-readable name for the key
            rate_limit: Optional custom rate limit
            service_tier: Optional Bedrock service tier ('default', 'flex', 'priority', 'reserved')
                         Note: Claude models only support 'default' and 'reserved'
            metadata: Optional metadata dictionary
            owner_name: Display name for the owner (e.g., "Eng. Team")
            role: Role type (e.g., "Admin", "Write Only", "Read Only", "Full Access")
            monthly_budget: Monthly budget limit in USD
            tpm_limit: Tokens per minute limit
            cache_ttl: Optional prompt cache TTL duration (e.g., '5m', '1h')

        Returns:
            Generated API key
        """
        api_key = f"sk-{uuid4().hex}"
        timestamp = int(time.time())

        # Get current month in YYYY-MM format
        current_month = datetime.now(timezone.utc).strftime("%Y-%m")

        item = {
            "api_key": api_key,
            "user_id": user_id,
            "name": name,
            "created_at": timestamp,
            "is_active": True,
            "rate_limit": rate_limit or settings.rate_limit_requests,
            "service_tier": service_tier or settings.default_service_tier,
            "metadata": metadata or {},
            # New fields for admin portal
            "owner_name": owner_name or user_id,
            "role": role or "Full Access",
            "monthly_budget": Decimal(str(monthly_budget)) if monthly_budget else Decimal("0"),
            "budget_used": Decimal("0"),  # Total cumulative budget used (never resets)
            "budget_used_mtd": Decimal("0"),  # Month-to-date budget used (resets monthly)
            "budget_mtd_month": current_month,  # Month for MTD tracking (YYYY-MM)
            "budget_history": "{}",  # Monthly budget history as JSON string (e.g., {"2025-11": 32.11})
            "deactivated_reason": None,  # Reason for deactivation (e.g., "budget_exceeded")
            "tpm_limit": tpm_limit or 100000,
            "cache_ttl": cache_ttl,
            "routing_strategy": routing_strategy or "off",
            "compression_strategy": compression_strategy or "off",
        }

        self.table.put_item(Item=item)
        return api_key

    def validate_api_key(self, api_key: str) -> Optional[Dict[str, Any]]:
        """
        Validate an API key and return its details.

        Auto-reactivates keys that were deactivated due to budget_exceeded
        if a new month has started.

        Args:
            api_key: API key to validate

        Returns:
            API key details if valid, None otherwise
        """
        try:
            response = self.table.get_item(Key={"api_key": api_key})
            item = response.get("Item")

            if not item:
                return None

            # If key is active, return it
            if item.get("is_active", False):
                return item

            # Check if key was deactivated due to budget exceeded
            # and if a new month has started - auto-reactivate it
            deactivated_reason = item.get("deactivated_reason")
            if deactivated_reason == "budget_exceeded":
                budget_mtd_month = item.get("budget_mtd_month", "")
                current_month = datetime.now(timezone.utc).strftime("%Y-%m")

                if budget_mtd_month != current_month:
                    # New month has started - reactivate and reset MTD
                    self._reactivate_for_new_month(api_key, current_month)
                    # Fetch updated item
                    response = self.table.get_item(Key={"api_key": api_key})
                    return response.get("Item")

            return None
        except ClientError:
            return None

    def _reactivate_for_new_month(self, api_key: str, current_month: str) -> bool:
        """
        Reactivate an API key for a new month, resetting MTD budget.

        Archives the previous month's budget to budget_history before resetting.

        Args:
            api_key: API key to reactivate
            current_month: Current month in YYYY-MM format

        Returns:
            True if reactivated successfully
        """
        try:
            # First get the current item to archive budget history
            response = self.table.get_item(Key={"api_key": api_key})
            item = response.get("Item", {})

            previous_month = item.get("budget_mtd_month", "")
            previous_mtd = float(item.get("budget_used_mtd", 0))

            # Update budget history
            budget_history_str = item.get("budget_history", "{}")
            try:
                budget_history = json.loads(budget_history_str) if budget_history_str else {}
            except (json.JSONDecodeError, TypeError):
                budget_history = {}

            if previous_month and previous_mtd > 0:
                budget_history[previous_month] = round(previous_mtd, 2)

            new_budget_history_str = json.dumps(budget_history)

            self.table.update_item(
                Key={"api_key": api_key},
                UpdateExpression="SET is_active = :active, budget_used_mtd = :zero, "
                "budget_mtd_month = :month, deactivated_reason = :null, "
                "budget_history = :history, updated_at = :updated_at",
                ExpressionAttributeValues={
                    ":active": True,
                    ":zero": Decimal("0"),
                    ":month": current_month,
                    ":null": None,
                    ":history": new_budget_history_str,
                    ":updated_at": int(time.time()),
                },
            )
            print(f"[APIKeyManager] Auto-reactivated key {api_key[:20]}... for new month {current_month}")
            return True
        except ClientError as e:
            print(f"[APIKeyManager] Error reactivating key: {e}")
            return False

    def deactivate_api_key(self, api_key: str, reason: Optional[str] = None):
        """
        Deactivate an API key.

        Args:
            api_key: API key to deactivate
            reason: Optional reason for deactivation (e.g., "budget_exceeded", "manual")
        """
        update_expr = "SET is_active = :val, updated_at = :updated_at"
        expr_values: Dict[str, Any] = {
            ":val": False,
            ":updated_at": int(time.time()),
        }

        if reason:
            update_expr += ", deactivated_reason = :reason"
            expr_values[":reason"] = reason

        self.table.update_item(
            Key={"api_key": api_key},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=expr_values,
        )

    def deactivate_for_budget_exceeded(self, api_key: str) -> bool:
        """
        Deactivate an API key due to budget exceeded.

        Args:
            api_key: API key to deactivate

        Returns:
            True if deactivated successfully
        """
        try:
            self.deactivate_api_key(api_key, reason="budget_exceeded")
            print(f"[APIKeyManager] Deactivated key {api_key[:20]}... due to budget exceeded")
            return True
        except ClientError as e:
            print(f"[APIKeyManager] Error deactivating key: {e}")
            return False

    def list_api_keys_for_user(self, user_id: str) -> List[Dict[str, Any]]:
        """
        List all API keys for a user.

        Args:
            user_id: User identifier

        Returns:
            List of API key details
        """
        response = self.table.query(
            IndexName="user_id-index",
            KeyConditionExpression="user_id = :user_id",
            ExpressionAttributeValues={":user_id": user_id},
        )
        return response.get("Items", [])

    def list_all_api_keys(
        self,
        limit: int = 100,
        last_key: Optional[Dict[str, Any]] = None,
        status_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        List all API keys with pagination support.

        Args:
            limit: Maximum number of items to return
            last_key: Last evaluated key for pagination
            status_filter: Optional filter by status ('active', 'revoked', or None for all)

        Returns:
            Dict with 'items' and 'last_key' for pagination
        """
        scan_kwargs: Dict[str, Any] = {"Limit": limit}

        if last_key:
            scan_kwargs["ExclusiveStartKey"] = last_key

        if status_filter == "active":
            scan_kwargs["FilterExpression"] = "is_active = :active"
            scan_kwargs["ExpressionAttributeValues"] = {":active": True}
        elif status_filter == "revoked":
            scan_kwargs["FilterExpression"] = "is_active = :active"
            scan_kwargs["ExpressionAttributeValues"] = {":active": False}

        response = self.table.scan(**scan_kwargs)

        return {
            "items": response.get("Items", []),
            "last_key": response.get("LastEvaluatedKey"),
            "count": response.get("Count", 0),
        }

    def get_api_key(self, api_key: str) -> Optional[Dict[str, Any]]:
        """
        Get API key details by key.

        Args:
            api_key: API key to retrieve

        Returns:
            API key details or None if not found
        """
        try:
            response = self.table.get_item(Key={"api_key": api_key})
            return response.get("Item")
        except ClientError:
            return None

    def update_api_key(
        self,
        api_key: str,
        name: Optional[str] = None,
        owner_name: Optional[str] = None,
        role: Optional[str] = None,
        monthly_budget: Optional[float] = None,
        budget_used: Optional[float] = None,
        budget_used_mtd: Optional[float] = None,
        budget_mtd_month: Optional[str] = None,
        tpm_limit: Optional[int] = None,
        rate_limit: Optional[int] = None,
        service_tier: Optional[str] = None,
        is_active: Optional[bool] = None,
        deactivated_reason: Optional[str] = None,
        cache_ttl: Optional[str] = None,
        routing_strategy: Optional[str] = None,
        compression_strategy: Optional[str] = None,
    ) -> bool:
        """
        Update API key fields.

        Args:
            api_key: API key to update
            name: New name
            owner_name: New owner name
            role: New role
            monthly_budget: New monthly budget
            budget_used: New total budget used amount
            budget_used_mtd: New month-to-date budget used amount
            budget_mtd_month: Month for MTD tracking (YYYY-MM format)
            tpm_limit: New TPM limit
            rate_limit: New rate limit
            service_tier: New service tier
            is_active: New active status
            deactivated_reason: Reason for deactivation (e.g., "budget_exceeded")

        Returns:
            True if updated successfully
        """
        update_parts = []
        expression_values = {}
        expression_names = {}

        if name is not None:
            update_parts.append("#n = :name")
            expression_values[":name"] = name
            expression_names["#n"] = "name"

        if owner_name is not None:
            update_parts.append("owner_name = :owner_name")
            expression_values[":owner_name"] = owner_name

        if role is not None:
            update_parts.append("#r = :role")
            expression_values[":role"] = role
            expression_names["#r"] = "role"

        if monthly_budget is not None:
            update_parts.append("monthly_budget = :monthly_budget")
            expression_values[":monthly_budget"] = Decimal(str(monthly_budget))

        if budget_used is not None:
            update_parts.append("budget_used = :budget_used")
            expression_values[":budget_used"] = Decimal(str(budget_used))

        if budget_used_mtd is not None:
            update_parts.append("budget_used_mtd = :budget_used_mtd")
            expression_values[":budget_used_mtd"] = Decimal(str(budget_used_mtd))

        if budget_mtd_month is not None:
            update_parts.append("budget_mtd_month = :budget_mtd_month")
            expression_values[":budget_mtd_month"] = budget_mtd_month

        if tpm_limit is not None:
            update_parts.append("tpm_limit = :tpm_limit")
            expression_values[":tpm_limit"] = tpm_limit

        if rate_limit is not None:
            update_parts.append("rate_limit = :rate_limit")
            expression_values[":rate_limit"] = rate_limit

        if service_tier is not None:
            update_parts.append("service_tier = :service_tier")
            expression_values[":service_tier"] = service_tier

        if is_active is not None:
            update_parts.append("is_active = :is_active")
            expression_values[":is_active"] = is_active
            # Clear deactivated_reason when reactivating manually
            if is_active:
                update_parts.append("deactivated_reason = :null_reason")
                expression_values[":null_reason"] = None

        if deactivated_reason is not None:
            update_parts.append("deactivated_reason = :deactivated_reason")
            expression_values[":deactivated_reason"] = deactivated_reason

        if cache_ttl is not None:
            if cache_ttl == "none":
                update_parts.append("cache_ttl = :cache_ttl")
                expression_values[":cache_ttl"] = None
            else:
                update_parts.append("cache_ttl = :cache_ttl")
                expression_values[":cache_ttl"] = cache_ttl

        if routing_strategy is not None:
            update_parts.append("routing_strategy = :routing_strategy")
            expression_values[":routing_strategy"] = routing_strategy

        if compression_strategy is not None:
            update_parts.append("compression_strategy = :compression_strategy")
            expression_values[":compression_strategy"] = compression_strategy

        if not update_parts:
            return False

        # Add updated_at timestamp
        update_parts.append("updated_at = :updated_at")
        expression_values[":updated_at"] = int(time.time())

        update_expression = "SET " + ", ".join(update_parts)

        try:
            update_kwargs = {
                "Key": {"api_key": api_key},
                "UpdateExpression": update_expression,
                "ExpressionAttributeValues": expression_values,
            }
            if expression_names:
                update_kwargs["ExpressionAttributeNames"] = expression_names

            self.table.update_item(**update_kwargs)
            return True
        except ClientError:
            return False

    def reactivate_api_key(self, api_key: str) -> bool:
        """
        Reactivate a deactivated API key.

        Args:
            api_key: API key to reactivate

        Returns:
            True if reactivated successfully
        """
        return self.update_api_key(api_key, is_active=True)

    def delete_api_key(self, api_key: str) -> bool:
        """
        Permanently delete an API key.

        Args:
            api_key: API key to delete

        Returns:
            True if deleted successfully
        """
        try:
            self.table.delete_item(Key={"api_key": api_key})
            return True
        except ClientError:
            return False

    def increment_budget_used(
        self,
        api_key: str,
        amount: float,
        check_budget_limit: bool = True,
    ) -> Dict[str, Any]:
        """
        Increment both budget_used (total) and budget_used_mtd (month-to-date).

        Handles month rollover by resetting MTD when the month changes.
        Optionally checks if MTD exceeds monthly_budget and deactivates the key.

        Args:
            api_key: API key to update
            amount: Amount to add to budget
            check_budget_limit: If True, check and deactivate if MTD exceeds monthly_budget

        Returns:
            Dict with 'success', 'budget_exceeded', and optionally 'new_mtd'
        """
        current_month = datetime.now(timezone.utc).strftime("%Y-%m")

        try:
            # First, get current key info to check the month
            response = self.table.get_item(Key={"api_key": api_key})
            item = response.get("Item")
            if not item:
                return {"success": False, "budget_exceeded": False}

            existing_month = item.get("budget_mtd_month", "")
            monthly_budget = float(item.get("monthly_budget", 0))

            if existing_month != current_month:
                # Month has changed - archive previous month's budget to history and reset MTD
                previous_mtd = float(item.get("budget_used_mtd", 0))

                # Update budget history with the previous month's final value
                budget_history_str = item.get("budget_history", "{}")
                try:
                    budget_history = json.loads(budget_history_str) if budget_history_str else {}
                except (json.JSONDecodeError, TypeError):
                    budget_history = {}

                # Only archive if there was actual budget used in the previous month
                if existing_month and previous_mtd > 0:
                    # Round to 2 decimal places for readability
                    budget_history[existing_month] = round(previous_mtd, 2)

                new_budget_history_str = json.dumps(budget_history)

                self.table.update_item(
                    Key={"api_key": api_key},
                    UpdateExpression="SET budget_used = if_not_exists(budget_used, :zero) + :amount, "
                    "budget_used_mtd = :amount, budget_mtd_month = :month, "
                    "budget_history = :history, updated_at = :updated_at",
                    ExpressionAttributeValues={
                        ":amount": Decimal(str(amount)),
                        ":zero": Decimal("0"),
                        ":month": current_month,
                        ":history": new_budget_history_str,
                        ":updated_at": int(time.time()),
                    },
                )
                new_mtd = amount
            else:
                # Same month - increment both
                self.table.update_item(
                    Key={"api_key": api_key},
                    UpdateExpression="SET budget_used = if_not_exists(budget_used, :zero) + :amount, "
                    "budget_used_mtd = if_not_exists(budget_used_mtd, :zero) + :amount, "
                    "updated_at = :updated_at",
                    ExpressionAttributeValues={
                        ":amount": Decimal(str(amount)),
                        ":zero": Decimal("0"),
                        ":updated_at": int(time.time()),
                    },
                )
                current_mtd = float(item.get("budget_used_mtd", 0))
                new_mtd = current_mtd + amount

            # Check if MTD exceeds monthly budget
            budget_exceeded = False
            if check_budget_limit and monthly_budget > 0 and new_mtd >= monthly_budget:
                # Deactivate the key due to budget exceeded
                self.deactivate_for_budget_exceeded(api_key)
                budget_exceeded = True

            return {"success": True, "budget_exceeded": budget_exceeded, "new_mtd": new_mtd}

        except ClientError as e:
            print(f"[APIKeyManager] Error incrementing budget: {e}")
            return {"success": False, "budget_exceeded": False}


class UsageTracker:
    """Tracker for API usage and analytics."""

    def __init__(self, dynamodb_client: DynamoDBClient):
        """Initialize usage tracker."""
        self.dynamodb = dynamodb_client.dynamodb
        self.table = self.dynamodb.Table(dynamodb_client.usage_table_name)

    def record_usage(
        self,
        api_key: str,
        request_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int = 0,
        cache_write_input_tokens: int = 0,
        success: bool = True,
        error_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        cache_ttl: Optional[str] = None,
    ):
        """
        Record API usage.

        Args:
            api_key: API key used
            request_id: Request identifier
            model: Model used
            input_tokens: Input token count
            output_tokens: Output token count
            cached_tokens: Cached tokens read (cache_read_input_tokens)
            cache_write_input_tokens: Tokens written to cache (cache_creation_input_tokens)
            success: Whether request was successful
            error_message: Error message if failed
            metadata: Optional metadata
            cache_ttl: Effective cache TTL used ("5m" or "1h"), for billing differentiation
        """
        # Use string timestamp to match CDK table schema (STRING type)
        current_time = int(time.time())
        timestamp = str(current_time * 1000)  # milliseconds as string

        item = {
            "api_key": api_key,
            "timestamp": timestamp,
            "request_id": request_id,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": cached_tokens,
            "cache_write_input_tokens": cache_write_input_tokens,
            "total_tokens": input_tokens + output_tokens,
            "success": success,
            "error_message": error_message,
            "metadata": metadata or {},
        }

        if cache_ttl:
            item["cache_ttl"] = cache_ttl

        # Add TTL if enabled (usage_ttl_days > 0)
        if settings.usage_ttl_days > 0:
            ttl_seconds = settings.usage_ttl_days * 24 * 60 * 60  # Convert days to seconds
            item["ttl"] = current_time + ttl_seconds

        self.table.put_item(Item=item)

    def get_usage_stats(
        self, api_key: str, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Get usage statistics for an API key.

        Args:
            api_key: API key to query
            start_time: Start of time range
            end_time: End of time range

        Returns:
            Usage statistics dictionary
        """
        if not start_time:
            start_time = datetime.now() - timedelta(days=30)
        if not end_time:
            end_time = datetime.now()

        # Use string timestamps to match CDK table schema (STRING type)
        start_timestamp = str(int(start_time.timestamp() * 1000))
        end_timestamp = str(int(end_time.timestamp() * 1000))

        response = self.table.query(
            KeyConditionExpression="api_key = :api_key AND #ts BETWEEN :start AND :end",
            ExpressionAttributeNames={"#ts": "timestamp"},
            ExpressionAttributeValues={
                ":api_key": api_key,
                ":start": start_timestamp,
                ":end": end_timestamp,
            },
        )

        items = response.get("Items", [])

        # Aggregate statistics
        total_requests = len(items)
        total_input_tokens = sum(item.get("input_tokens", 0) for item in items)
        total_output_tokens = sum(item.get("output_tokens", 0) for item in items)
        total_cached_tokens = sum(item.get("cached_tokens", 0) for item in items)
        total_cache_write_input_tokens = sum(item.get("cache_write_input_tokens", 0) for item in items)
        successful_requests = sum(1 for item in items if item.get("success", False))

        return {
            "total_requests": total_requests,
            "successful_requests": successful_requests,
            "failed_requests": total_requests - successful_requests,
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_cached_tokens": total_cached_tokens,
            "total_cache_write_input_tokens": total_cache_write_input_tokens,
            "total_tokens": total_input_tokens + total_output_tokens,
        }


class ModelMappingManager:
    """Manager for custom model mappings."""

    def __init__(self, dynamodb_client: DynamoDBClient):
        """Initialize model mapping manager."""
        self.dynamodb = dynamodb_client.dynamodb
        self.table = self.dynamodb.Table(dynamodb_client.model_mapping_table_name)

    def get_mapping(self, anthropic_model_id: str) -> Optional[str]:
        """
        Get Bedrock model ID for an Anthropic model ID.

        Args:
            anthropic_model_id: Anthropic model identifier

        Returns:
            Bedrock model ARN or None
        """
        try:
            response = self.table.get_item(
                Key={"anthropic_model_id": anthropic_model_id}
            )
            item = response.get("Item")
            return item.get("bedrock_model_id") if item else None
        except ClientError:
            return None

    def set_mapping(self, anthropic_model_id: str, bedrock_model_id: str):
        """
        Set custom model mapping.

        Args:
            anthropic_model_id: Anthropic model identifier
            bedrock_model_id: Bedrock model ARN
        """
        item = {
            "anthropic_model_id": anthropic_model_id,
            "bedrock_model_id": bedrock_model_id,
            "updated_at": int(time.time()),
        }
        self.table.put_item(Item=item)

    def delete_mapping(self, anthropic_model_id: str):
        """
        Delete custom model mapping.

        Args:
            anthropic_model_id: Anthropic model identifier
        """
        self.table.delete_item(Key={"anthropic_model_id": anthropic_model_id})

    def list_mappings(self) -> List[Dict[str, str]]:
        """
        List all custom model mappings.

        Returns:
            List of model mappings
        """
        response = self.table.scan()
        return response.get("Items", [])


class ModelPricingManager:
    """Manager for model pricing configuration."""

    def __init__(self, dynamodb_client: DynamoDBClient):
        """Initialize model pricing manager."""
        self.dynamodb = dynamodb_client.dynamodb
        self.table = self.dynamodb.Table(dynamodb_client.model_pricing_table_name)

    def create_pricing(
        self,
        model_id: str,
        provider: str,
        input_price: Union[float, Decimal],
        output_price: Union[float, Decimal],
        cache_read_price: Optional[Union[float, Decimal]] = None,
        cache_write_price: Optional[Union[float, Decimal]] = None,
        display_name: Optional[str] = None,
        status: str = "active",
    ) -> Dict[str, Any]:
        """
        Create a new model pricing entry.

        Args:
            model_id: Bedrock model ID (e.g., "anthropic.claude-3-5-sonnet-20241022-v2:0")
            provider: Provider name (e.g., "Anthropic", "Cohere")
            input_price: Input price per 1M tokens in USD
            output_price: Output price per 1M tokens in USD
            cache_read_price: Cache read price per 1M tokens in USD
            cache_write_price: Cache write price per 1M tokens in USD
            display_name: Human-readable model name
            status: Model status ("active", "deprecated", "disabled")

        Returns:
            Created pricing item
        """
        timestamp = int(time.time())

        # Convert floats to Decimal for DynamoDB compatibility
        def to_decimal(value: Optional[Union[float, Decimal]]) -> Optional[Decimal]:
            if value is None:
                return None
            return Decimal(str(value)) if not isinstance(value, Decimal) else value

        item = {
            "model_id": model_id,
            "provider": provider,
            "display_name": display_name or model_id,
            "input_price": to_decimal(input_price),
            "output_price": to_decimal(output_price),
            "cache_read_price": to_decimal(cache_read_price),
            "cache_write_price": to_decimal(cache_write_price),
            "status": status,
            "created_at": timestamp,
            "updated_at": timestamp,
        }

        self.table.put_item(Item=item)
        return item

    def get_pricing(self, model_id: str) -> Optional[Dict[str, Any]]:
        """
        Get pricing for a specific model.

        Args:
            model_id: Bedrock model ID

        Returns:
            Pricing details or None if not found
        """
        try:
            response = self.table.get_item(Key={"model_id": model_id})
            return response.get("Item")
        except ClientError:
            return None

    def update_pricing(
        self,
        model_id: str,
        input_price: Optional[Union[float, Decimal]] = None,
        output_price: Optional[Union[float, Decimal]] = None,
        cache_read_price: Optional[Union[float, Decimal]] = None,
        cache_write_price: Optional[Union[float, Decimal]] = None,
        display_name: Optional[str] = None,
        status: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> bool:
        """
        Update model pricing.

        Args:
            model_id: Bedrock model ID
            input_price: New input price per 1M tokens
            output_price: New output price per 1M tokens
            cache_read_price: New cache read price per 1M tokens
            cache_write_price: New cache write price per 1M tokens
            display_name: New display name
            status: New status
            provider: New provider name

        Returns:
            True if updated successfully
        """
        # Helper to convert floats to Decimal for DynamoDB
        def to_decimal(value: Optional[Union[float, Decimal]]) -> Optional[Decimal]:
            if value is None:
                return None
            return Decimal(str(value)) if not isinstance(value, Decimal) else value

        update_parts = []
        expression_values = {}

        if input_price is not None:
            update_parts.append("input_price = :input_price")
            expression_values[":input_price"] = to_decimal(input_price)

        if output_price is not None:
            update_parts.append("output_price = :output_price")
            expression_values[":output_price"] = to_decimal(output_price)

        if cache_read_price is not None:
            update_parts.append("cache_read_price = :cache_read_price")
            expression_values[":cache_read_price"] = to_decimal(cache_read_price)

        if cache_write_price is not None:
            update_parts.append("cache_write_price = :cache_write_price")
            expression_values[":cache_write_price"] = to_decimal(cache_write_price)

        if display_name is not None:
            update_parts.append("display_name = :display_name")
            expression_values[":display_name"] = display_name

        if status is not None:
            update_parts.append("#s = :status")
            expression_values[":status"] = status

        if provider is not None:
            update_parts.append("provider = :provider")
            expression_values[":provider"] = provider

        if not update_parts:
            return False

        # Add updated_at timestamp
        update_parts.append("updated_at = :updated_at")
        expression_values[":updated_at"] = int(time.time())

        update_expression = "SET " + ", ".join(update_parts)

        try:
            update_kwargs = {
                "Key": {"model_id": model_id},
                "UpdateExpression": update_expression,
                "ExpressionAttributeValues": expression_values,
            }
            # status is a reserved word in DynamoDB
            if status is not None:
                update_kwargs["ExpressionAttributeNames"] = {"#s": "status"}

            self.table.update_item(**update_kwargs)
            return True
        except ClientError:
            return False

    def delete_pricing(self, model_id: str) -> bool:
        """
        Delete model pricing.

        Args:
            model_id: Bedrock model ID

        Returns:
            True if deleted successfully
        """
        try:
            self.table.delete_item(Key={"model_id": model_id})
            return True
        except ClientError:
            return False

    def list_all_pricing(
        self,
        limit: int = 100,
        last_key: Optional[Dict[str, Any]] = None,
        provider_filter: Optional[str] = None,
        status_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        List all model pricing with pagination.

        Args:
            limit: Maximum number of items to return
            last_key: Last evaluated key for pagination
            provider_filter: Optional filter by provider
            status_filter: Optional filter by status

        Returns:
            Dict with 'items' and 'last_key' for pagination
        """
        # Use GSI if filtering by provider
        if provider_filter:
            query_kwargs = {
                "IndexName": "provider-index",
                "KeyConditionExpression": "provider = :provider",
                "ExpressionAttributeValues": {":provider": provider_filter},
                "Limit": limit,
            }
            if last_key:
                query_kwargs["ExclusiveStartKey"] = last_key
            if status_filter:
                query_kwargs["FilterExpression"] = "#s = :status"
                query_kwargs["ExpressionAttributeNames"] = {"#s": "status"}
                query_kwargs["ExpressionAttributeValues"][":status"] = status_filter

            response = self.table.query(**query_kwargs)
        else:
            scan_kwargs: Dict[str, Any] = {"Limit": limit}
            if last_key:
                scan_kwargs["ExclusiveStartKey"] = last_key
            if status_filter:
                scan_kwargs["FilterExpression"] = "#s = :status"
                scan_kwargs["ExpressionAttributeNames"] = {"#s": "status"}
                scan_kwargs["ExpressionAttributeValues"] = {":status": status_filter}

            response = self.table.scan(**scan_kwargs)

        return {
            "items": response.get("Items", []),
            "last_key": response.get("LastEvaluatedKey"),
            "count": response.get("Count", 0),
        }

    def get_pricing_by_provider(self, provider: str) -> List[Dict[str, Any]]:
        """
        Get all pricing for a specific provider.

        Args:
            provider: Provider name

        Returns:
            List of pricing items for the provider
        """
        response = self.table.query(
            IndexName="provider-index",
            KeyConditionExpression="provider = :provider",
            ExpressionAttributeValues={":provider": provider},
        )
        return response.get("Items", [])


class UsageStatsManager:
    """Manager for aggregated usage statistics."""

    def __init__(self, dynamodb_client: DynamoDBClient):
        """Initialize usage stats manager."""
        self.dynamodb = dynamodb_client.dynamodb
        self.dynamodb_client = dynamodb_client
        self.table = self.dynamodb.Table(dynamodb_client.usage_stats_table_name)
        self.usage_table = self.dynamodb.Table(dynamodb_client.usage_table_name)

    def _resolve_model_id(
        self,
        model_id: str,
        model_mapping_cache: Optional[Dict[str, str]] = None
    ) -> str:
        """
        Resolve an Anthropic model ID to a Bedrock model ID.

        Args:
            model_id: The model ID (could be Anthropic or Bedrock format)
            model_mapping_cache: Optional cache of model mappings

        Returns:
            The resolved Bedrock model ID
        """
        if not model_id:
            return model_id

        # Check cache first
        if model_mapping_cache and model_id in model_mapping_cache:
            return model_mapping_cache[model_id]

        # Check default config mapping
        bedrock_id = settings.default_model_mapping.get(model_id)
        if bedrock_id:
            return bedrock_id

        # If no mapping found, assume it's already a Bedrock model ID
        return model_id

    def get_stats(self, api_key: str) -> Optional[Dict[str, Any]]:
        """
        Get aggregated usage stats for an API key.

        Args:
            api_key: API key to query

        Returns:
            Usage stats dictionary or None if not found
        """
        try:
            response = self.table.get_item(Key={"api_key": api_key})
            return response.get("Item")
        except ClientError:
            return None

    def update_stats(
        self,
        api_key: str,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int,
        cache_write_tokens: int,
        request_count: int,
        last_aggregated_timestamp: Optional[int] = None,
    ) -> bool:
        """
        Update or create aggregated usage stats for an API key.

        Args:
            api_key: API key to update
            input_tokens: Total input tokens
            output_tokens: Total output tokens
            cached_tokens: Total cached tokens
            cache_write_tokens: Total cache write tokens
            request_count: Total request count
            last_aggregated_timestamp: Timestamp of the last processed record (for incremental aggregation)

        Returns:
            True if updated successfully
        """
        try:
            item = {
                "api_key": api_key,
                "total_input_tokens": input_tokens,
                "total_output_tokens": output_tokens,
                "total_cached_tokens": cached_tokens,
                "total_cache_write_tokens": cache_write_tokens,
                "total_requests": request_count,
                "last_updated": int(time.time()),
            }
            if last_aggregated_timestamp is not None:
                item["last_aggregated_timestamp"] = last_aggregated_timestamp
            self.table.put_item(Item=item)
            return True
        except ClientError:
            return False

    def increment_stats(
        self,
        api_key: str,
        delta_input_tokens: int,
        delta_output_tokens: int,
        delta_cached_tokens: int,
        delta_cache_write_tokens: int,
        delta_request_count: int,
        last_aggregated_timestamp: int,
    ) -> bool:
        """
        Incrementally update usage stats for an API key.

        Args:
            api_key: API key to update
            delta_input_tokens: Input tokens to add
            delta_output_tokens: Output tokens to add
            delta_cached_tokens: Cached tokens to add
            delta_cache_write_tokens: Cache write tokens to add
            delta_request_count: Request count to add
            last_aggregated_timestamp: New timestamp of the last processed record

        Returns:
            True if updated successfully
        """
        try:
            self.table.update_item(
                Key={"api_key": api_key},
                UpdateExpression="""
                    SET total_input_tokens = if_not_exists(total_input_tokens, :zero) + :input_tokens,
                        total_output_tokens = if_not_exists(total_output_tokens, :zero) + :output_tokens,
                        total_cached_tokens = if_not_exists(total_cached_tokens, :zero) + :cached_tokens,
                        total_cache_write_tokens = if_not_exists(total_cache_write_tokens, :zero) + :cache_write_tokens,
                        total_requests = if_not_exists(total_requests, :zero) + :request_count,
                        last_aggregated_timestamp = :last_timestamp,
                        last_updated = :now
                """,
                ExpressionAttributeValues={
                    ":input_tokens": delta_input_tokens,
                    ":output_tokens": delta_output_tokens,
                    ":cached_tokens": delta_cached_tokens,
                    ":cache_write_tokens": delta_cache_write_tokens,
                    ":request_count": delta_request_count,
                    ":last_timestamp": last_aggregated_timestamp,
                    ":now": int(time.time()),
                    ":zero": 0,
                },
            )
            return True
        except ClientError:
            return False

    def get_all_stats(self) -> List[Dict[str, Any]]:
        """
        Get all usage stats.

        Returns:
            List of all usage stats
        """
        try:
            response = self.table.scan()
            return response.get("Items", [])
        except ClientError:
            return []

    def aggregate_usage_for_key(
        self,
        api_key: str,
        pricing_cache: Optional[Dict[str, Dict[str, Any]]] = None,
        model_mapping_cache: Optional[Dict[str, str]] = None,
        since_timestamp: Optional[int] = None,
    ) -> Dict[str, Union[int, float]]:
        """
        Aggregate usage data for an API key from the usage table.

        Supports incremental aggregation by specifying since_timestamp to only
        process records newer than that timestamp.

        Args:
            api_key: API key to aggregate
            pricing_cache: Optional cache of model pricing (keyed by Bedrock model ID)
            model_mapping_cache: Optional cache of model ID mappings (Anthropic → Bedrock)
            since_timestamp: Optional timestamp to filter records (only process records > this timestamp)

        Returns:
            Dictionary with aggregated stats including total_cost and max_timestamp
        """
        total_input_tokens = 0
        total_output_tokens = 0
        total_cached_tokens = 0
        total_cache_write_tokens = 0
        total_requests = 0
        total_cost = 0.0
        max_timestamp = since_timestamp or 0

        try:
            # Build query parameters
            if since_timestamp:
                # Incremental query: only fetch records after the last processed timestamp
                # Note: timestamp is stored as STRING in DynamoDB (milliseconds as string)
                paginator_params: Dict[str, Any] = {
                    "KeyConditionExpression": "api_key = :api_key AND #ts > :since_ts",
                    "ExpressionAttributeValues": {
                        ":api_key": api_key,
                        ":since_ts": str(since_timestamp),  # Must be string to match schema
                    },
                    "ExpressionAttributeNames": {"#ts": "timestamp"},
                }
            else:
                # Full query: fetch all records for this API key
                paginator_params = {
                    "KeyConditionExpression": "api_key = :api_key",
                    "ExpressionAttributeValues": {":api_key": api_key},
                }

            last_key = None
            while True:
                if last_key:
                    paginator_params["ExclusiveStartKey"] = last_key

                response = self.usage_table.query(**paginator_params)

                for item in response.get("Items", []):
                    input_tokens = int(item.get("input_tokens", 0))
                    output_tokens = int(item.get("output_tokens", 0))
                    cached_tokens = int(item.get("cached_tokens", 0))
                    cache_write_tokens = int(item.get("cache_write_input_tokens", 0))
                    record_timestamp = int(item.get("timestamp", 0))

                    total_input_tokens += input_tokens
                    total_output_tokens += output_tokens
                    total_cached_tokens += cached_tokens
                    total_cache_write_tokens += cache_write_tokens
                    total_requests += 1

                    # Track the maximum timestamp processed
                    if record_timestamp > max_timestamp:
                        max_timestamp = record_timestamp

                    # Calculate cost for this request if pricing is available
                    model = item.get("model", "")
                    if pricing_cache and model:
                        # Resolve model ID to Bedrock format for pricing lookup
                        bedrock_model_id = self._resolve_model_id(model, model_mapping_cache)
                        pricing = pricing_cache.get(bedrock_model_id)
                        if pricing:
                            input_price = float(pricing.get("input_price", 0))
                            output_price = float(pricing.get("output_price", 0))
                            cache_read_price = float(pricing.get("cache_read_price", 0) or 0)
                            cache_write_price = float(pricing.get("cache_write_price", 0) or 0)

                            # Cache write pricing depends on TTL duration:
                            #   5m (default): 1.25x input price (stored as cache_write_price)
                            #   1h: 2.0x input price (derived from input_price)
                            record_cache_ttl = item.get("cache_ttl")
                            if record_cache_ttl == "1h":
                                effective_cache_write_price = input_price * 2.0
                            else:
                                effective_cache_write_price = cache_write_price

                            # Prices are per 1M tokens
                            cost = (
                                (input_tokens * input_price / 1_000_000)
                                + (output_tokens * output_price / 1_000_000)
                                + (cached_tokens * cache_read_price / 1_000_000)
                                + (cache_write_tokens * effective_cache_write_price / 1_000_000)
                            )
                            total_cost += cost

                last_key = response.get("LastEvaluatedKey")
                if not last_key:
                    break

        except ClientError as e:
            print(f"Error aggregating usage for {api_key}: {e}")

        return {
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_cached_tokens": total_cached_tokens,
            "total_cache_write_tokens": total_cache_write_tokens,
            "total_requests": total_requests,
            "total_cost": total_cost,
            "max_timestamp": max_timestamp,
        }

    @staticmethod
    def get_service_tier_multiplier(service_tier: Optional[str]) -> float:
        """
        Get the cost multiplier based on service tier.

        Service tier pricing:
        - default: 1.0 (standard pricing)
        - flex: 0.5 (50% discount)
        - priority: 1.75 (75% markup)

        Args:
            service_tier: The service tier ('default', 'flex', 'priority')

        Returns:
            Cost multiplier to apply
        """
        multipliers = {
            "default": 1.0,
            "flex": 0.5,
            "priority": 1.75,
        }
        return multipliers.get(service_tier or "default", 1.0)

    def aggregate_all_usage(
        self,
        api_keys: List[str],
        pricing_manager: Optional["ModelPricingManager"] = None,
        api_key_manager: Optional["APIKeyManager"] = None,
    ) -> int:
        """
        Aggregate usage for all provided API keys and store in stats table.

        Uses incremental aggregation: only processes new records since the last
        aggregation timestamp, and increments the existing totals rather than
        recalculating from scratch.

        Args:
            api_keys: List of API keys to aggregate
            pricing_manager: Optional pricing manager to calculate costs
            api_key_manager: Optional API key manager to update budget_used

        Returns:
            Number of keys successfully aggregated
        """
        # Build pricing cache if pricing manager is available
        pricing_cache: Dict[str, Dict[str, Any]] = {}
        if pricing_manager:
            result = pricing_manager.list_all_pricing(limit=1000)
            for item in result.get("items", []):
                model_id = item.get("model_id", "")
                if model_id:
                    pricing_cache[model_id] = item

        # Build model mapping cache from DynamoDB custom mappings
        model_mapping_cache: Dict[str, str] = {}
        try:
            model_mapping_manager = ModelMappingManager(self.dynamodb_client)
            custom_mappings = model_mapping_manager.list_mappings()
            for mapping in custom_mappings:
                anthropic_id = mapping.get("anthropic_model_id", "")
                bedrock_id = mapping.get("bedrock_model_id", "")
                if anthropic_id and bedrock_id:
                    model_mapping_cache[anthropic_id] = bedrock_id
        except Exception as e:
            print(f"[UsageStatsManager] Error loading model mappings: {e}")

        count = 0
        for api_key in api_keys:
            # Get existing stats to find the last aggregated timestamp
            existing_stats = self.get_stats(api_key)
            last_aggregated_timestamp = None
            if existing_stats:
                last_aggregated_timestamp = existing_stats.get("last_aggregated_timestamp")
                # Convert Decimal to int if needed
                if last_aggregated_timestamp is not None:
                    last_aggregated_timestamp = int(last_aggregated_timestamp)

            # Aggregate usage (incrementally if we have a timestamp)
            stats = self.aggregate_usage_for_key(
                api_key,
                pricing_cache,
                model_mapping_cache,
                since_timestamp=last_aggregated_timestamp,
            )

            # Skip if no new records were processed
            if stats["total_requests"] == 0:
                continue

            max_timestamp = int(stats["max_timestamp"])

            # Get service tier multiplier for cost adjustment
            service_tier = "default"
            if api_key_manager:
                api_key_info = api_key_manager.get_api_key(api_key)
                if api_key_info:
                    service_tier = api_key_info.get("service_tier", "default")
            multiplier = self.get_service_tier_multiplier(service_tier)
            adjusted_cost = float(stats["total_cost"]) * multiplier

            if last_aggregated_timestamp:
                # Incremental update: add delta values to existing stats
                if self.increment_stats(
                    api_key=api_key,
                    delta_input_tokens=int(stats["total_input_tokens"]),
                    delta_output_tokens=int(stats["total_output_tokens"]),
                    delta_cached_tokens=int(stats["total_cached_tokens"]),
                    delta_cache_write_tokens=int(stats["total_cache_write_tokens"]),
                    delta_request_count=int(stats["total_requests"]),
                    last_aggregated_timestamp=max_timestamp,
                ):
                    count += 1

                    # Increment budget_used and budget_used_mtd on the API key
                    # This also handles month rollover and budget limit checks
                    if api_key_manager and adjusted_cost > 0:
                        result = api_key_manager.increment_budget_used(api_key, adjusted_cost)
                        if result.get("budget_exceeded"):
                            print(f"[UsageStatsManager] API key {api_key[:20]}... exceeded budget")
            else:
                # First run: set initial values
                if self.update_stats(
                    api_key=api_key,
                    input_tokens=int(stats["total_input_tokens"]),
                    output_tokens=int(stats["total_output_tokens"]),
                    cached_tokens=int(stats["total_cached_tokens"]),
                    cache_write_tokens=int(stats["total_cache_write_tokens"]),
                    request_count=int(stats["total_requests"]),
                    last_aggregated_timestamp=max_timestamp,
                ):
                    count += 1

                    # Set budget_used and budget_used_mtd on the API key
                    # This also handles month rollover and budget limit checks
                    if api_key_manager and adjusted_cost > 0:
                        result = api_key_manager.increment_budget_used(api_key, adjusted_cost)
                        if result.get("budget_exceeded"):
                            print(f"[UsageStatsManager] API key {api_key[:20]}... exceeded budget")

        return count


class ProviderKeyManager:
    """Manager for provider API key operations (multi-provider key pool)."""

    def __init__(self, dynamodb_client: DynamoDBClient):
        self.dynamodb = dynamodb_client.dynamodb
        self.table = self.dynamodb.Table(dynamodb_client.provider_keys_table_name)

    def create_key(
        self,
        provider: str,
        encrypted_api_key: str,
        models: List[str],
        is_enabled: bool = True,
    ) -> Dict[str, Any]:
        """Create a new provider key."""
        key_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()
        item = {
            "key_id": key_id,
            "provider": provider,
            "encrypted_api_key": encrypted_api_key,
            "models": models,
            "is_enabled": is_enabled,
            "status": "available",
            "created_at": now,
            "updated_at": now,
        }
        self.table.put_item(Item=item)
        return item

    def get_key(self, key_id: str) -> Optional[Dict[str, Any]]:
        """Get a provider key by ID."""
        try:
            response = self.table.get_item(Key={"key_id": key_id})
            return response.get("Item")
        except ClientError:
            return None

    def list_keys(self) -> List[Dict[str, Any]]:
        """List all provider keys."""
        response = self.table.scan()
        return response.get("Items", [])

    def list_keys_by_provider(self, provider: str) -> List[Dict[str, Any]]:
        """List provider keys filtered by provider using GSI."""
        response = self.table.query(
            IndexName="provider-index",
            KeyConditionExpression="provider = :provider",
            ExpressionAttributeValues={":provider": provider},
        )
        return response.get("Items", [])

    def update_key(
        self,
        key_id: str,
        models: Optional[List[str]] = None,
        is_enabled: Optional[bool] = None,
        status: Optional[str] = None,
    ) -> bool:
        """Update a provider key."""
        update_parts = []
        expression_values = {}

        if models is not None:
            update_parts.append("models = :models")
            expression_values[":models"] = models
        if is_enabled is not None:
            update_parts.append("is_enabled = :is_enabled")
            expression_values[":is_enabled"] = is_enabled
        if status is not None:
            update_parts.append("#s = :status")
            expression_values[":status"] = status

        if not update_parts:
            return False

        update_parts.append("updated_at = :updated_at")
        expression_values[":updated_at"] = datetime.now(timezone.utc).isoformat()

        update_expression = "SET " + ", ".join(update_parts)
        try:
            update_kwargs = {
                "Key": {"key_id": key_id},
                "UpdateExpression": update_expression,
                "ExpressionAttributeValues": expression_values,
            }
            if "#s" in update_expression:
                update_kwargs["ExpressionAttributeNames"] = {"#s": "status"}
            self.table.update_item(**update_kwargs)
            return True
        except ClientError:
            return False

    def delete_key(self, key_id: str) -> bool:
        """Delete a provider key."""
        try:
            self.table.delete_item(Key={"key_id": key_id})
            return True
        except ClientError:
            return False


class RoutingConfigManager:
    """Manager for routing rule operations."""

    def __init__(self, dynamodb_client: DynamoDBClient):
        self.dynamodb = dynamodb_client.dynamodb
        self.table = self.dynamodb.Table(dynamodb_client.routing_rules_table_name)

    def create_rule(
        self,
        rule_name: str,
        rule_type: str,
        pattern: str,
        target_model: str,
        target_provider: str = "bedrock",
        priority: Optional[int] = None,
        is_enabled: bool = True,
    ) -> Dict[str, Any]:
        """Create a new routing rule."""
        rule_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()

        if priority is None:
            # Auto-assign priority: max existing + 1
            existing = self.list_rules()
            priority = max((r.get("priority", 0) for r in existing), default=-1) + 1

        item = {
            "rule_id": rule_id,
            "rule_name": rule_name,
            "rule_type": rule_type,
            "pattern": pattern,
            "target_model": target_model,
            "target_provider": target_provider,
            "priority": priority,
            "is_enabled": is_enabled,
            "created_at": now,
            "updated_at": now,
        }
        self.table.put_item(Item=item)
        return item

    def get_rule(self, rule_id: str) -> Optional[Dict[str, Any]]:
        """Get a routing rule by ID."""
        try:
            response = self.table.get_item(Key={"rule_id": rule_id})
            return response.get("Item")
        except ClientError:
            return None

    def list_rules(self) -> List[Dict[str, Any]]:
        """List all routing rules sorted by priority."""
        response = self.table.scan()
        items = response.get("Items", [])
        return sorted(items, key=lambda r: r.get("priority", 0))

    def update_rule(
        self,
        rule_id: str,
        rule_name: Optional[str] = None,
        rule_type: Optional[str] = None,
        pattern: Optional[str] = None,
        target_model: Optional[str] = None,
        target_provider: Optional[str] = None,
        is_enabled: Optional[bool] = None,
    ) -> bool:
        """Update a routing rule."""
        update_parts = []
        expression_values = {}
        expression_names = {}

        if rule_name is not None:
            update_parts.append("rule_name = :rule_name")
            expression_values[":rule_name"] = rule_name
        if rule_type is not None:
            update_parts.append("rule_type = :rule_type")
            expression_values[":rule_type"] = rule_type
        if pattern is not None:
            update_parts.append("pattern = :pattern")
            expression_values[":pattern"] = pattern
        if target_model is not None:
            update_parts.append("target_model = :target_model")
            expression_values[":target_model"] = target_model
        if target_provider is not None:
            update_parts.append("target_provider = :target_provider")
            expression_values[":target_provider"] = target_provider
        if is_enabled is not None:
            update_parts.append("is_enabled = :is_enabled")
            expression_values[":is_enabled"] = is_enabled

        if not update_parts:
            return False

        update_parts.append("updated_at = :updated_at")
        expression_values[":updated_at"] = datetime.now(timezone.utc).isoformat()

        update_expression = "SET " + ", ".join(update_parts)
        try:
            update_kwargs = {
                "Key": {"rule_id": rule_id},
                "UpdateExpression": update_expression,
                "ExpressionAttributeValues": expression_values,
            }
            if expression_names:
                update_kwargs["ExpressionAttributeNames"] = expression_names
            self.table.update_item(**update_kwargs)
            return True
        except ClientError:
            return False

    def reorder_rules(self, rule_ids: List[str]) -> bool:
        """Batch update rule priorities based on ordered list of rule_ids."""
        try:
            for priority, rule_id in enumerate(rule_ids):
                self.table.update_item(
                    Key={"rule_id": rule_id},
                    UpdateExpression="SET priority = :p, updated_at = :u",
                    ExpressionAttributeValues={
                        ":p": priority,
                        ":u": datetime.now(timezone.utc).isoformat(),
                    },
                )
            return True
        except ClientError:
            return False

    def delete_rule(self, rule_id: str) -> bool:
        """Delete a routing rule."""
        try:
            self.table.delete_item(Key={"rule_id": rule_id})
            return True
        except ClientError:
            return False


class FailoverConfigManager:
    """Manager for failover chain operations."""

    def __init__(self, dynamodb_client: DynamoDBClient):
        self.dynamodb = dynamodb_client.dynamodb
        self.table = self.dynamodb.Table(dynamodb_client.failover_chains_table_name)

    def create_chain(
        self,
        source_model: str,
        targets: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        """Create a new failover chain."""
        now = datetime.now(timezone.utc).isoformat()
        item = {
            "source_model": source_model,
            "targets": targets,
            "created_at": now,
            "updated_at": now,
        }
        self.table.put_item(Item=item)
        return item

    def get_chain(self, source_model: str) -> Optional[Dict[str, Any]]:
        """Get a failover chain by source model."""
        try:
            response = self.table.get_item(Key={"source_model": source_model})
            return response.get("Item")
        except ClientError:
            return None

    def list_chains(self) -> List[Dict[str, Any]]:
        """List all failover chains."""
        response = self.table.scan()
        return response.get("Items", [])

    def update_chain(
        self,
        source_model: str,
        targets: List[Dict[str, str]],
    ) -> bool:
        """Update a failover chain's targets."""
        try:
            self.table.update_item(
                Key={"source_model": source_model},
                UpdateExpression="SET targets = :targets, updated_at = :updated_at",
                ExpressionAttributeValues={
                    ":targets": targets,
                    ":updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            return True
        except ClientError:
            return False

    def delete_chain(self, source_model: str) -> bool:
        """Delete a failover chain."""
        try:
            self.table.delete_item(Key={"source_model": source_model})
            return True
        except ClientError:
            return False


class SmartRoutingConfigManager:
    """Manager for smart routing global configuration."""

    def __init__(self, dynamodb_client: DynamoDBClient):
        self.dynamodb = dynamodb_client.dynamodb
        self.table = self.dynamodb.Table(dynamodb_client.smart_routing_config_table_name)

    def get_config(self) -> Optional[Dict[str, Any]]:
        """Get the global smart routing config."""
        try:
            response = self.table.get_item(Key={"config_id": "global"})
            return response.get("Item")
        except ClientError:
            return None

    def put_config(
        self,
        strong_model: str,
        weak_model: str,
        threshold: float = 0.5,
    ) -> Dict[str, Any]:
        """Create or update the global smart routing config."""
        now = datetime.now(timezone.utc).isoformat()
        item = {
            "config_id": "global",
            "strong_model": strong_model,
            "weak_model": weak_model,
            "threshold": Decimal(str(threshold)),
            "updated_at": now,
        }
        self.table.put_item(Item=item)
        return item
