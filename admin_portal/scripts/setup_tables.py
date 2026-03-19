#!/usr/bin/env python3
"""
Setup script for Admin Portal DynamoDB tables.

This script creates the model pricing table and can seed initial data.
"""
import sys
from pathlib import Path

# Add parent directory to path to import from app
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from decimal import Decimal
from app.db.dynamodb import DynamoDBClient, ModelPricingManager


# Default model pricing data (prices per 1M tokens in USD)
# Note: cache_write_price is the 5m TTL rate (1.25x input_price).
# The 1h TTL rate (2x input_price) is derived during aggregation in
# UsageStatsManager.aggregate_usage_for_key() based on the cache_ttl field.
DEFAULT_PRICING = [
    {
        "model_id": "anthropic.claude-3-5-haiku-20241022-v1:0",
        "provider": "Anthropic",
        "display_name": "Claude 3.5 Haiku",
        "input_price": Decimal("0.80"),
        "output_price": Decimal("4.00"),
        "cache_read_price": Decimal("0.08"),
        "cache_write_price": Decimal("1.00"),
        "status": "active",
    },
    {
        "model_id": "global.anthropic.claude-haiku-4-5-20251001-v1:0",
        "provider": "Anthropic",
        "display_name": "Claude Haiku 4.5",
        "input_price": Decimal("1.00"),
        "output_price": Decimal("5.00"),
        "cache_read_price": Decimal("0.10"),
        "cache_write_price": Decimal("1.25"),
        "status": "active",
    },
    {
        "model_id": "global.anthropic.claude-opus-4-5-20251101-v1:0",
        "provider": "Anthropic",
        "display_name": "Claude Opus 4.5",
        "input_price": Decimal("5.00"),
        "output_price": Decimal("25.00"),
        "cache_read_price": Decimal("0.5"),
        "cache_write_price": Decimal("6.25"),
        "status": "active",
    },
    {
        "model_id": "global.anthropic.claude-opus-4-6-v1",
        "provider": "Anthropic",
        "display_name": "Claude Opus 4.6",
        "input_price": Decimal("5.00"),
        "output_price": Decimal("25.00"),
        "cache_read_price": Decimal("0.5"),
        "cache_write_price": Decimal("6.25"),
        "status": "active",
    },
    {
        "model_id": "global.anthropic.claude-sonnet-4-6",
        "provider": "Anthropic",
        "display_name": "Claude Sonnet 4.6",
        "input_price": Decimal("3.00"),
        "output_price": Decimal("15.00"),
        "cache_read_price": Decimal("0.30"),
        "cache_write_price": Decimal("3.75"),
        "status": "active",
    },
    {
        "model_id": "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "provider": "Anthropic",
        "display_name": "Claude Sonnet 4.5",
        "input_price": Decimal("3.00"),
        "output_price": Decimal("15.00"),
        "cache_read_price": Decimal("0.30"),
        "cache_write_price": Decimal("3.75"),
        "status": "active",
    },
    {
        "model_id": "minimax.minimax-m2",
        "provider": "MiniMax",
        "display_name": "minimax.minimax-m2",
        "input_price": Decimal("0.15"),
        "output_price": Decimal("0.6"),
        "cache_read_price": Decimal("0.00"),
        "cache_write_price": Decimal("0.00"),
        "status": "active",
    }
]


def create_tables():
    """Create all required DynamoDB tables."""
    print("Creating DynamoDB tables...")
    client = DynamoDBClient()
    client.create_tables()
    print("Tables created successfully!")


def seed_pricing_data(force: bool = False):
    """Seed default pricing data into the model pricing table."""
    print("\nSeeding model pricing data...")
    client = DynamoDBClient()
    pricing_manager = ModelPricingManager(client)

    for pricing in DEFAULT_PRICING:
        # Check if already exists
        existing = pricing_manager.get_pricing(pricing["model_id"])
        if existing and not force:
            print(f"  Skipping {pricing['model_id']} (already exists)")
            continue

        pricing_manager.create_pricing(
            model_id=pricing["model_id"],
            provider=pricing["provider"],
            display_name=pricing["display_name"],
            input_price=float(pricing["input_price"]),
            output_price=float(pricing["output_price"]),
            cache_read_price=float(pricing["cache_read_price"]) if pricing["cache_read_price"] else None,
            cache_write_price=float(pricing["cache_write_price"]) if pricing["cache_write_price"] else None,
            status=pricing["status"],
        )
        print(f"  Added {pricing['model_id']}")

    print("Pricing data seeded successfully!")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Setup Admin Portal DynamoDB tables")
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Seed default pricing data",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force overwrite existing pricing data",
    )
    parser.add_argument(
        "--tables-only",
        action="store_true",
        help="Only create tables, don't seed data",
    )

    args = parser.parse_args()

    # Always create tables
    create_tables()

    # Seed data if requested or by default
    if not args.tables_only:
        seed_pricing_data(force=args.force)


if __name__ == "__main__":
    main()
