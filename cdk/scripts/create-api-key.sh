#!/bin/bash
set -e

# Script to create API keys in DynamoDB for the Anthropic Proxy

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Default values
ENVIRONMENT="prod"
REGION="${AWS_REGION:-us-west-2}"
USER_ID=""
KEY_NAME=""
RATE_LIMIT="1000"
SERVICE_TIER="default"

# Usage
usage() {
    cat <<EOF
Usage: $0 [OPTIONS]

Create an API key for the Anthropic Proxy service

OPTIONS:
    -e, --environment ENV    Environment (dev|prod) [default: prod]
    -r, --region REGION      AWS region [default: us-west-2]
    -u, --user-id ID         User ID (required)
    -n, --name NAME          Key name/description (required)
    -l, --rate-limit LIMIT   Rate limit requests per minute [optional]
    -t, --service-tier TIER  Bedrock service tier [default: default]
                             Options: default, flex, priority, reserved
    -h, --help               Show this help message

SERVICE TIERS:
    default   - Standard service tier (works with all models)
    flex      - Lower cost, higher latency (NOT supported by Claude models)
    priority  - Lower latency, higher cost
    reserved  - Reserved capacity tier

    Note: Claude models only support 'default' and 'reserved' tiers.
          If 'flex' is used with Claude, requests will fallback to 'default'.

EXAMPLES:
    # Create a key for a user with default service tier
    ./scripts/create-api-key.sh -e dev -u user@example.com -n "Production Key"

    # Create a key with flex tier (for Qwen, DeepSeek, etc.)
    ./scripts/create-api-key.sh -e prod -u user@example.com -n "Flex Key" -t flex

    # Create a key with rate limit
    ./scripts/create-api-key.sh -e prod -u user@example.com -n "API Key" -l 1000

    # Create a key with flex tier and rate limit
    ./scripts/create-api-key.sh -e prod -u user@example.com -n "Budget Key" -t flex -l 500

EOF
    exit 1
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -e|--environment)
            ENVIRONMENT="$2"
            shift 2
            ;;
        -r|--region)
            REGION="$2"
            shift 2
            ;;
        -u|--user-id)
            USER_ID="$2"
            shift 2
            ;;
        -n|--name)
            KEY_NAME="$2"
            shift 2
            ;;
        -l|--rate-limit)
            RATE_LIMIT="$2"
            shift 2
            ;;
        -t|--service-tier)
            SERVICE_TIER="$2"
            shift 2
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            usage
            ;;
    esac
done

# Validate inputs
if [[ -z "$USER_ID" ]]; then
    echo -e "${RED}Error: User ID is required${NC}"
    usage
fi

if [[ -z "$KEY_NAME" ]]; then
    echo -e "${RED}Error: Key name is required${NC}"
    usage
fi

if [[ ! "$ENVIRONMENT" =~ ^(dev|prod)$ ]]; then
    echo -e "${RED}Error: Environment must be 'dev' or 'prod'${NC}"
    exit 1
fi

if [[ ! "$SERVICE_TIER" =~ ^(default|flex|priority|reserved)$ ]]; then
    echo -e "${RED}Error: Service tier must be 'default', 'flex', 'priority', or 'reserved'${NC}"
    exit 1
fi

# Check AWS credentials
if ! aws sts get-caller-identity &> /dev/null; then
    echo -e "${RED}Error: AWS credentials not configured properly.${NC}"
    exit 1
fi

# Generate API key
API_KEY="sk-$(openssl rand -hex 16)"

# Get table name from CloudFormation stack outputs
STACK_NAME="AnthropicProxy-${ENVIRONMENT}-DynamoDB"
TABLE_NAME=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='APIKeysTableName'].OutputValue" \
    --output text 2>/dev/null)

if [[ -z "$TABLE_NAME" || "$TABLE_NAME" == "None" ]]; then
    # Fallback: try to find table by pattern
    TABLE_NAME=$(aws dynamodb list-tables \
        --region "$REGION" \
        --query "TableNames[?contains(@, 'AnthropicProxy-${ENVIRONMENT}') && contains(@, 'APIKeys')] | [0]" \
        --output text 2>/dev/null)
fi

if [[ -z "$TABLE_NAME" || "$TABLE_NAME" == "None" ]]; then
    echo -e "${RED}Error: Could not find API Keys table for environment '${ENVIRONMENT}'.${NC}"
    echo -e "${RED}Make sure the CDK stack 'AnthropicProxy-${ENVIRONMENT}-DynamoDB' is deployed.${NC}"
    exit 1
fi

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Creating API Key${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "Environment:  ${YELLOW}${ENVIRONMENT}${NC}"
echo -e "Region:       ${YELLOW}${REGION}${NC}"
echo -e "Table:        ${YELLOW}${TABLE_NAME}${NC}"
echo -e "User ID:      ${YELLOW}${USER_ID}${NC}"
echo -e "Key Name:     ${YELLOW}${KEY_NAME}${NC}"
echo -e "Service Tier: ${YELLOW}${SERVICE_TIER}${NC}"
[ -n "$RATE_LIMIT" ] && echo -e "Rate Limit:   ${YELLOW}${RATE_LIMIT} req/min${NC}"
echo -e "${GREEN}========================================${NC}"
echo

# Show warning for flex tier
if [[ "$SERVICE_TIER" == "flex" ]]; then
    echo -e "${YELLOW}⚠️  Warning: 'flex' tier is NOT supported by Claude models.${NC}"
    echo -e "${YELLOW}   If used with Claude, requests will fallback to 'default'.${NC}"
    echo
fi

# Prepare item
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

ITEM="{
  \"api_key\": {\"S\": \"$API_KEY\"},
  \"user_id\": {\"S\": \"$USER_ID\"},
  \"name\": {\"S\": \"$KEY_NAME\"},
  \"is_active\": {\"BOOL\": true},
  \"created_at\": {\"S\": \"$TIMESTAMP\"},
  \"service_tier\": {\"S\": \"$SERVICE_TIER\"},
  \"metadata\": {\"M\": {
    \"created_via\": {\"S\": \"cli\"},
    \"environment\": {\"S\": \"$ENVIRONMENT\"}
  }}
}"

# Add rate limit if specified
if [ -n "$RATE_LIMIT" ]; then
    ITEM=$(echo "$ITEM" | jq --arg limit "$RATE_LIMIT" \
        '.rate_limit = {"N": $limit}')
fi

# Create item in DynamoDB
echo -e "${YELLOW}Creating API key in DynamoDB...${NC}"

if aws dynamodb put-item \
    --table-name "$TABLE_NAME" \
    --item "$ITEM" \
    --region "$REGION" \
    --condition-expression "attribute_not_exists(api_key)" \
    2>/dev/null; then

    echo -e "${GREEN}✓ API key created successfully!${NC}"
    echo
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}API Key Details${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo -e "${YELLOW}API Key:${NC}      $API_KEY"
    echo -e "${YELLOW}Service Tier:${NC} $SERVICE_TIER"
    echo
    echo -e "${YELLOW}IMPORTANT: Save this API key securely!${NC}"
    echo -e "${YELLOW}It will not be shown again.${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo
    echo -e "${YELLOW}Test the API key:${NC}"
    echo -e "  curl -H 'x-api-key: $API_KEY' https://your-endpoint.com/health"
else
    echo -e "${RED}Error: Failed to create API key${NC}"
    exit 1
fi
