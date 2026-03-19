#!/usr/bin/env python3
"""
Script to create DynamoDB tables.

Usage:
    python scripts/setup_tables.py
"""
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db.dynamodb import DynamoDBClient


def main():
    """Create DynamoDB tables."""
    print("Setting up DynamoDB tables...")

    # Initialize DynamoDB client
    dynamodb_client = DynamoDBClient()

    # Create tables
    print("\nCreating tables...")
    dynamodb_client.create_tables()

    print("\n✓ All tables created successfully!")
    print("\nTables:")
    print(f"  - {dynamodb_client.api_keys_table_name}")
    print(f"  - {dynamodb_client.usage_table_name}")
    print(f"  - {dynamodb_client.usage_stats_table_name}")
    print(f"  - {dynamodb_client.model_mapping_table_name}")
    print(f"  - {dynamodb_client.model_pricing_table_name}")


if __name__ == "__main__":
    main()
