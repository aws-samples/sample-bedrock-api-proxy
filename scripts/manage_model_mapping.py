#!/usr/bin/env python3
"""
Script to manage model ID mappings between Anthropic and Bedrock formats.

This script allows you to:
- Add new model mappings
- List existing mappings
- Delete mappings
- Test model ID resolution

Usage:
    # Add a mapping
    python scripts/manage_model_mapping.py add \
        --anthropic-id "claude-3-5-sonnet-20241022" \
        --bedrock-id "anthropic.claude-3-5-sonnet-20241022-v2:0"

    # List all mappings
    python scripts/manage_model_mapping.py list

    # Delete a mapping
    python scripts/manage_model_mapping.py delete \
        --anthropic-id "claude-3-5-sonnet-20241022"

    # Test model resolution
    python scripts/manage_model_mapping.py test \
        --anthropic-id "claude-3-5-sonnet-20241022"
"""
import argparse
import sys
from pathlib import Path

# Add parent directory to path to import app modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db.dynamodb import DynamoDBClient, ModelMappingManager
from app.core.config import settings
from app.services.model_mapping_sync_service import get_sync_status, run_sync


def refresh_default_mapping():
    """Best-effort pull of the remote default mapping so listings match a running proxy."""
    if not settings.model_mapping_sync_enabled:
        return
    try:
        run_sync()
    except Exception as e:
        print(f"⚠ Could not fetch remote model mappings ({e}); using local snapshot.")


def add_mapping(anthropic_id: str, bedrock_id: str):
    """Add a new model mapping."""
    dynamodb_client = DynamoDBClient()
    mapping_manager = ModelMappingManager(dynamodb_client)

    print(f"Adding model mapping:")
    print(f"  Anthropic ID: {anthropic_id}")
    print(f"  Bedrock ID:   {bedrock_id}")

    mapping_manager.set_mapping(anthropic_id, bedrock_id)
    print("✓ Mapping added successfully!")


def list_mappings():
    """List all model mappings."""
    dynamodb_client = DynamoDBClient()
    mapping_manager = ModelMappingManager(dynamodb_client)

    print("\n" + "="*80)
    print("MODEL MAPPINGS")
    print("="*80)

    # Show default mappings (remote model_mappings.json, or the local snapshot)
    refresh_default_mapping()
    status = get_sync_status()
    origin = {
        "remote": f"remote: {status['source_url']}",
        "env": "DEFAULT_MODEL_MAPPING env + local snapshot",
        "bundled": "local snapshot: model-mappings/model_mappings.json",
    }[status["source"]]
    print(f"\n📋 Default Mappings ({origin}):")
    print("-" * 80)
    for anthropic_id, bedrock_id in settings.default_model_mapping.items():
        print(f"  {anthropic_id:<40} → {bedrock_id}")

    # Show custom mappings from DynamoDB
    print("\n📝 Custom Mappings (from DynamoDB):")
    print("-" * 80)
    custom_mappings = mapping_manager.list_mappings()

    if custom_mappings:
        for mapping in custom_mappings:
            anthropic_id = mapping.get("anthropic_model_id", "unknown")
            bedrock_id = mapping.get("bedrock_model_id", "unknown")
            print(f"  {anthropic_id:<40} → {bedrock_id}")
        print(f"\nTotal custom mappings: {len(custom_mappings)}")
    else:
        print("  (no custom mappings)")

    print("\n" + "="*80 + "\n")


def delete_mapping(anthropic_id: str):
    """Delete a model mapping."""
    dynamodb_client = DynamoDBClient()
    mapping_manager = ModelMappingManager(dynamodb_client)

    print(f"Deleting mapping for: {anthropic_id}")

    # Check if it exists first
    existing = mapping_manager.get_mapping(anthropic_id)
    if existing:
        print(f"  Current mapping: {anthropic_id} → {existing}")
        mapping_manager.delete_mapping(anthropic_id)
        print("✓ Mapping deleted successfully!")
    else:
        print(f"⚠ No custom mapping found for '{anthropic_id}'")
        print("  (Note: Default mappings cannot be deleted)")


def test_resolution(anthropic_id: str):
    """Test how a model ID would be resolved."""
    from app.converters.anthropic_to_bedrock import AnthropicToBedrockConverter

    dynamodb_client = DynamoDBClient()
    mapping_manager = ModelMappingManager(dynamodb_client)
    converter = AnthropicToBedrockConverter(dynamodb_client)

    print(f"\n🔍 Testing model ID resolution for: {anthropic_id}")
    print("="*80)

    # Check DynamoDB custom mapping
    custom_mapping = mapping_manager.get_mapping(anthropic_id)
    if custom_mapping:
        print(f"\n✓ Found in DynamoDB (custom mapping):")
        print(f"  {anthropic_id} → {custom_mapping}")
    else:
        print(f"\n  No custom mapping in DynamoDB")

    # Check default mapping
    default_mapping = settings.default_model_mapping.get(anthropic_id)
    if default_mapping:
        print(f"\n✓ Found in default config:")
        print(f"  {anthropic_id} → {default_mapping}")
    else:
        print(f"\n  No default mapping in config")

    # Show what the converter will use
    resolved = converter._convert_model_id(anthropic_id)
    print(f"\n🎯 Final resolved ID (what will be used):")
    print(f"  {resolved}")

    if resolved == anthropic_id:
        print(f"\n⚠ Note: Using input ID as-is (pass-through mode)")
        print(f"  This assumes '{anthropic_id}' is a valid Bedrock model ARN")

    print("\n" + "="*80 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Manage model ID mappings between Anthropic and Bedrock formats",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")
    subparsers.required = True

    # Add command
    add_parser = subparsers.add_parser("add", help="Add a new model mapping")
    add_parser.add_argument(
        "--anthropic-id",
        required=True,
        help="Anthropic model ID (e.g., claude-3-5-sonnet-20241022)"
    )
    add_parser.add_argument(
        "--bedrock-id",
        required=True,
        help="Bedrock model ARN (e.g., anthropic.claude-3-5-sonnet-20241022-v2:0)"
    )

    # List command
    subparsers.add_parser("list", help="List all model mappings")

    # Delete command
    delete_parser = subparsers.add_parser("delete", help="Delete a model mapping")
    delete_parser.add_argument(
        "--anthropic-id",
        required=True,
        help="Anthropic model ID to delete"
    )

    # Test command
    test_parser = subparsers.add_parser("test", help="Test model ID resolution")
    test_parser.add_argument(
        "--anthropic-id",
        required=True,
        help="Anthropic model ID to test"
    )

    args = parser.parse_args()

    try:
        if args.command == "add":
            add_mapping(args.anthropic_id, args.bedrock_id)
        elif args.command == "list":
            list_mappings()
        elif args.command == "delete":
            delete_mapping(args.anthropic_id)
        elif args.command == "test":
            test_resolution(args.anthropic_id)
    except Exception as e:
        print(f"\n❌ Error: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
