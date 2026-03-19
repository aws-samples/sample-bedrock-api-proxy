#!/usr/bin/env python3
"""
Script to create API keys in DynamoDB.

Usage:
    python scripts/create_api_key.py --user-id <user_id> --name <key_name>
    python scripts/create_api_key.py --user-id <user_id> --name <key_name> --service-tier flex

Examples:
    # Create a basic API key with default service tier
    python scripts/create_api_key.py --user-id dev-user --name "Development Key"

    # Create an API key with flex tier (for non-Claude models like Qwen, DeepSeek)
    python scripts/create_api_key.py --user-id dev-user --name "Flex Tier Key" --service-tier flex

    # Create an API key with custom rate limit
    python scripts/create_api_key.py --user-id dev-user --name "Limited Key" --rate-limit 100

Service Tier Options:
    - default:  Standard service tier (works with all models)
    - flex:     Lower cost, higher latency (NOT supported by Claude models)
    - priority: Lower latency, higher cost
    - reserved: Reserved capacity tier

Note: Claude models only support 'default' and 'reserved' tiers.
      If 'flex' is used with Claude, it will automatically fallback to 'default'.
"""
import argparse
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db.dynamodb import APIKeyManager, DynamoDBClient


VALID_SERVICE_TIERS = ["default", "flex", "priority", "reserved"]


def main():
    """Create API key."""
    parser = argparse.ArgumentParser(
        description="Create API key for the Anthropic-Bedrock proxy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Service Tier Options:
  default   - Standard service tier (works with all models)
  flex      - Lower cost, higher latency (NOT supported by Claude models)
  priority  - Lower latency, higher cost
  reserved  - Reserved capacity tier

Examples:
  # Create a basic API key
  python scripts/create_api_key.py --user-id dev-user --name "Dev Key"

  # Create with flex tier for Qwen/DeepSeek models
  python scripts/create_api_key.py --user-id dev-user --name "Flex Key" --service-tier flex

  # Create with custom rate limit
  python scripts/create_api_key.py --user-id dev-user --name "Limited Key" --rate-limit 100
        """
    )
    parser.add_argument(
        "--user-id",
        required=True,
        help="User ID for the API key",
    )
    parser.add_argument(
        "--name",
        required=True,
        help="Name/description for the API key",
    )
    parser.add_argument(
        "--rate-limit",
        type=int,
        default=None,
        help="Custom rate limit (requests per window)",
    )
    parser.add_argument(
        "--service-tier",
        type=str,
        default=None,
        choices=VALID_SERVICE_TIERS,
        help="Bedrock service tier: default, flex, priority, reserved (default: default). "
             "Note: Claude models only support 'default' and 'reserved'.",
    )

    args = parser.parse_args()

    # Initialize DynamoDB client
    print("Initializing DynamoDB client...")
    dynamodb_client = DynamoDBClient()

    # Create API key manager
    api_key_manager = APIKeyManager(dynamodb_client)

    # Create API key
    print(f"Creating API key for user: {args.user_id}")
    api_key = api_key_manager.create_api_key(
        user_id=args.user_id,
        name=args.name,
        rate_limit=args.rate_limit,
        service_tier=args.service_tier,
    )

    print(f"\n{'='*50}")
    print(f"API Key created successfully!")
    print(f"{'='*50}")
    print(f"API Key:      {api_key}")
    print(f"User ID:      {args.user_id}")
    print(f"Name:         {args.name}")
    if args.rate_limit:
        print(f"Rate Limit:   {args.rate_limit} requests per window")
    print(f"Service Tier: {args.service_tier or 'default'}")
    print(f"{'='*50}")

    if args.service_tier == "flex":
        print(f"\n⚠️  Warning: 'flex' tier is NOT supported by Claude models.")
        print(f"   If used with Claude, requests will fallback to 'default' tier.")

    print(f"\nUse this key in requests:")
    print(f"  x-api-key: {api_key}")


if __name__ == "__main__":
    main()
