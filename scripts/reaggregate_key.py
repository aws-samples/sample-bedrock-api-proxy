#!/usr/bin/env python3
"""
Recompute the stored usage-stats row for one or more API keys from scratch.

Why this exists
---------------
The admin portal shows per-key token totals from the ``anthropic-proxy-usage-stats``
table, which is a *running total* maintained incrementally: each aggregation pass
only reads usage records newer than ``last_aggregated_timestamp`` and *adds* the
deltas (see ``UsageStatsManager.aggregate_all_usage``). That means a correctness
fix to the aggregation logic does **not** retroactively heal a row that already
accumulated wrong values — the historical portion stays baked in.

This was the case for the OpenAI passthrough cached-token fix: OpenAI-format APIs
report ``input_tokens`` inclusive of cached tokens, so before the fix the stored
``total_input_tokens`` was inflated by the cached amount (counted once inside
input and again under ``cached``). New records aggregate correctly now, but
already-stored totals need a one-time recompute.

What it does
------------
For each key:
  1. Print the current stored stat (before).
  2. Delete the usage-stats row so the next aggregation takes the first-run
     (full, from-scratch) branch instead of the incremental branch.
  3. Reset ``budget_used`` / ``budget_used_mtd`` to 0 (re-aggregation re-adds
     cost from scratch; skip with --no-budget-reset).
  4. Run ``aggregate_all_usage([key])`` to recompute from the raw usage table
     with the current (fixed) code.
  5. Print the recomputed stat (after).

IMPORTANT: the deployed service must already be running the fixed code, otherwise
this recomputes with the old (inflated) logic. Run with --dry-run first.

Usage:
    python scripts/reaggregate_key.py --region us-west-2 --api-key sk-xxxx
    python scripts/reaggregate_key.py --region us-west-2 --api-key sk-a --api-key sk-b
    python scripts/reaggregate_key.py --region us-west-2 --api-key sk-xxxx --dry-run
    python scripts/reaggregate_key.py --region us-west-2 --all          # every key (careful)
"""

import argparse
import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def _fmt(stats):
    """Render the token fields of a stats row (or '<none>')."""
    if not stats:
        return "<no stored stats row>"
    return (
        f"input={int(stats.get('total_input_tokens', 0)):,} "
        f"output={int(stats.get('total_output_tokens', 0)):,} "
        f"cached={int(stats.get('total_cached_tokens', 0)):,} "
        f"cache_write={int(stats.get('total_cache_write_tokens', 0)):,} "
        f"requests={int(stats.get('total_requests', 0)):,}"
    )


def main():
    parser = argparse.ArgumentParser(
        description="Recompute stored usage-stats for API key(s) from scratch.",
    )
    parser.add_argument(
        "--api-key",
        action="append",
        default=[],
        dest="api_keys",
        help="API key to recompute (repeatable). Required unless --all.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Recompute every API key in the table (use with care).",
    )
    parser.add_argument(
        "--region",
        default=None,
        help="AWS region (sets AWS_REGION before clients are built). "
        "Defaults to AWS_REGION / .env.",
    )
    parser.add_argument(
        "--no-budget-reset",
        action="store_true",
        help="Do not reset budget_used/budget_used_mtd before recompute. "
        "Leave set only if you will not let cost re-aggregate.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the current stored stats and exit without modifying anything.",
    )
    args = parser.parse_args()

    if not args.api_keys and not args.all:
        parser.error("provide at least one --api-key, or --all")

    # Region must be set before app modules build their boto3 clients.
    if args.region:
        os.environ["AWS_REGION"] = args.region

    from app.db.dynamodb import (
        APIKeyManager,
        DynamoDBClient,
        ModelPricingManager,
        UsageStatsManager,
    )

    db = DynamoDBClient()
    api_key_manager = APIKeyManager(db)
    stats_manager = UsageStatsManager(db)
    pricing_manager = ModelPricingManager(db)

    region = os.environ.get("AWS_REGION", "<default>")

    # Resolve the target key list.
    if args.all:
        keys = []
        last_key = None
        while True:
            result = api_key_manager.list_all_api_keys(limit=1000, last_key=last_key)
            keys.extend(item["api_key"] for item in result.get("items", []))
            last_key = result.get("last_key")
            if not last_key:
                break
    else:
        keys = list(dict.fromkeys(args.api_keys))  # de-dupe, preserve order

    print(f"Region: {region}")
    print(f"Keys to process: {len(keys)}")
    if args.dry_run:
        print("Mode: DRY RUN (no changes)\n")
    print()

    processed = 0
    for key in keys:
        masked = key[:12] + "..." if len(key) > 12 else key
        before = stats_manager.get_stats(key)
        print(f"[{masked}] before: {_fmt(before)}")

        if args.dry_run:
            continue

        # 1. Delete stored row -> next aggregation recomputes from scratch.
        try:
            stats_manager.table.delete_item(Key={"api_key": key})
        except Exception as exc:  # pragma: no cover - operational tool
            print(f"[{masked}] ERROR deleting stats row: {exc}")
            continue

        # 2. Reset budget so re-aggregation's cost is not double-counted.
        if not args.no_budget_reset:
            api_key_manager.update_api_key(
                api_key=key, budget_used=0.0, budget_used_mtd=0.0
            )

        # 3. Recompute from the raw usage table with current code.
        stats_manager.aggregate_all_usage(
            [key],
            pricing_manager=pricing_manager,
            api_key_manager=api_key_manager,
        )

        after = stats_manager.get_stats(key)
        print(f"[{masked}] after:  {_fmt(after)}")
        processed += 1

    print()
    if args.dry_run:
        print("Dry run complete — no changes made.")
    else:
        print(f"Recomputed {processed}/{len(keys)} key(s).")


if __name__ == "__main__":
    main()
