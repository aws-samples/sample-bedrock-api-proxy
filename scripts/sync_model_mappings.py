#!/usr/bin/env python3
"""
Fetch the remote default model mapping file and show what a running proxy
would load from it.

Usage:
    python scripts/sync_model_mappings.py                 # fetch + diff against the local snapshot
    python scripts/sync_model_mappings.py --url <json>    # fetch from a different URL
    python scripts/sync_model_mappings.py --validate model-mappings/model_mappings.json
                                                          # validate a local file before pushing

The proxy and admin portal pull this file themselves (MODEL_MAPPING_SYNC_URL,
every MODEL_MAPPING_SYNC_INTERVAL_SECONDS); this script is for checking the
remote file / a local edit, not for pushing state into a running service.
"""

import argparse
import json
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.config import settings
from app.services.model_mapping_sync_service import (
    get_local_overrides,
    parse_model_mappings,
    run_sync,
)


def _print_list(label: str, items, current=None, new=None):
    if not items:
        return
    print(f"\n{label} ({len(items)}):")
    for key in items:
        if current is not None and new is not None and key in current and key in new:
            print(f"  {key:<40} {current[key]}  ->  {new[key]}")
        elif new is not None and key in new:
            print(f"  {key:<40} -> {new[key]}")
        else:
            print(f"  {key}")


def validate_file(path: str) -> int:
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        mappings = parse_model_mappings(payload)
    except (OSError, ValueError) as e:
        print(f"✗ {path}: {e}")
        return 1
    print(f"✓ {path}: {len(mappings)} mappings OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch/validate the remote default model mapping file"
    )
    parser.add_argument(
        "--url", help="Source URL (default: MODEL_MAPPING_SYNC_URL setting)"
    )
    parser.add_argument(
        "--validate",
        metavar="FILE",
        help="Validate a local model_mappings.json instead of fetching the remote one",
    )
    args = parser.parse_args()

    if args.validate:
        return validate_file(args.validate)

    before = dict(settings.default_model_mapping)
    try:
        summary = run_sync(url=args.url)
    except Exception as e:
        print(f"✗ Sync failed: {e}")
        return 1

    after = settings.default_model_mapping
    print(f"Source:            {summary['source_url']}")
    print(f"Remote mappings:   {summary['remote_models']}")
    print(
        f"Local overrides:   {summary['local_overrides']} (DEFAULT_MODEL_MAPPING env)"
    )
    print(f"Active mappings:   {summary['mapping_count']}")
    if not (summary["added"] or summary["removed"] or summary["changed"]):
        print("\nNo differences against the local snapshot / env mapping.")
    _print_list("Added vs local snapshot", summary["added"], new=after)
    _print_list("Removed vs local snapshot", summary["removed"])
    _print_list(
        "Changed vs local snapshot", summary["changed"], current=before, new=after
    )
    overrides = get_local_overrides()
    if overrides:
        print("\nDEFAULT_MODEL_MAPPING env entries layered on top:")
        for k, v in overrides.items():
            print(f"  {k:<40} -> {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
