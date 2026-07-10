#!/usr/bin/env python3
"""
Sync model pricing from the LiteLLM price table into DynamoDB.

Usage:
    python scripts/sync_model_pricing.py                     # sync with settings defaults
    python scripts/sync_model_pricing.py --dry-run           # preview changes without writing
    python scripts/sync_model_pricing.py --overwrite-manual  # also update manually managed rows
    python scripts/sync_model_pricing.py --url <json_url>    # sync from a different source URL

Rows created by the sync are marked pricing_source="litellm" and refreshed on
later runs; rows without that marker (created manually or edited in the admin
portal) are skipped unless --overwrite-manual is given. Rows are never deleted.
"""

import argparse
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.pricing_sync_service import run_sync


def main():
    parser = argparse.ArgumentParser(
        description="Sync model pricing from the LiteLLM price table"
    )
    parser.add_argument("--url", help="Source URL (default: PRICING_SYNC_URL setting)")
    parser.add_argument(
        "--providers",
        help="Comma-separated litellm_provider values (default: PRICING_SYNC_PROVIDERS setting)",
    )
    parser.add_argument(
        "--no-create-missing",
        action="store_true",
        help="Only update existing rows; don't create rows for new models",
    )
    parser.add_argument(
        "--overwrite-manual",
        action="store_true",
        help="Also update rows not created by the sync (manually managed pricing)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing",
    )
    args = parser.parse_args()

    summary = run_sync(
        url=args.url,
        providers=(
            [p.strip() for p in args.providers.split(",")] if args.providers else None
        ),
        create_missing=False if args.no_create_missing else None,
        overwrite_manual=True if args.overwrite_manual else None,
        dry_run=args.dry_run,
    )

    prefix = "[dry run] " if summary["dry_run"] else ""
    print(f"Source: {summary['source_url']} ({summary['source_models']} usable models)")
    print(f"{prefix}Created:   {len(summary['created'])}")
    print(f"{prefix}Updated:   {len(summary['updated'])}")
    print(f"Unchanged: {summary['unchanged']}")
    if summary["skipped_manual"]:
        print(f"Skipped (manually managed): {len(summary['skipped_manual'])}")
        for model_id in summary["skipped_manual"]:
            print(f"  - {model_id}")
    if summary["not_found"]:
        print(f"Mapped models without source pricing: {len(summary['not_found'])}")
        for model_id in summary["not_found"]:
            print(f"  - {model_id}")
    if summary["dry_run"] and (summary["created"] or summary["updated"]):
        print("\nWould create:" if summary["created"] else "", end="")
        for model_id in summary["created"]:
            print(f"\n  + {model_id}", end="")
        print("\nWould update:" if summary["updated"] else "", end="")
        for model_id in summary["updated"]:
            print(f"\n  ~ {model_id}", end="")
        print()


if __name__ == "__main__":
    main()
