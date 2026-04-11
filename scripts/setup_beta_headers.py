#!/usr/bin/env python3
"""Seed beta headers table with default data from config.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db.dynamodb import DynamoDBClient, BetaHeaderManager
from app.core.config import settings


def main():
    """Seed beta headers table."""
    print("Setting up beta headers table...")

    db_client = DynamoDBClient()
    manager = BetaHeaderManager(db_client)

    # Seed blocklist entries from config defaults
    for header_name in settings.beta_headers_blocklist:
        existing = manager.get(header_name)
        if existing:
            print(f"  Already exists: {header_name}")
            continue
        manager.create(
            header_name=header_name,
            header_type="blocklist",
            description="Default blocklist entry from config",
        )
        print(f"  Created blocklist: {header_name}")

    # Seed mapping entries from config defaults
    for header_name, mapped_to in settings.beta_header_mapping.items():
        existing = manager.get(header_name)
        if existing:
            print(f"  Already exists: {header_name}")
            continue
        manager.create(
            header_name=header_name,
            header_type="mapping",
            mapped_to=mapped_to,
            description="Default mapping entry from config",
        )
        print(f"  Created mapping: {header_name} -> {mapped_to}")

    print("\nDone! Beta headers seeded.")


if __name__ == "__main__":
    main()
