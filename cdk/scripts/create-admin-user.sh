#!/bin/bash
set -e

# Create Admin User for Anthropic Proxy Admin Portal
# This script creates a Cognito user for the admin portal

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
ENVIRONMENT="dev"
REGION="${AWS_REGION:-us-west-2}"
EMAIL=""
PROMPT_PASSWORD=false

# Usage
usage() {
    cat <<EOF
Usage: $0 [OPTIONS]

Create an admin user for the Anthropic Proxy Admin Portal (Cognito)

OPTIONS:
    -e, --environment ENV      Environment (dev|prod) [default: dev]
    -r, --region REGION        AWS region [default: us-west-2]
    --email EMAIL              User email address (required)
    --prompt-password          Enter the temporary password at a hidden prompt instead of
                               having Cognito email one (no invitation email is sent)
    -h, --help                 Show this help message

EXAMPLES:
    # Create admin user; Cognito emails a one-time temporary password
    ./scripts/create-admin-user.sh -e dev --email admin@example.com

    # No email delivery available: type the temporary password at a hidden prompt
    ./scripts/create-admin-user.sh -e prod -r us-east-1 --email admin@example.com --prompt-password

NOTES:
    - The temporary password is never printed, logged or accepted as a
      command-line argument (it would land in shell history / process lists)
    - The user will be required to change their password on first login
    - Password must meet Cognito requirements:
      * Minimum 12 characters
      * At least one uppercase letter
      * At least one lowercase letter
      * At least one number
      * At least one special character

EOF
    exit 1
}

# Read a temporary password from a hidden prompt (never echoed or stored)
read_temp_password() {
    local confirm
    read -r -s -p "Temporary password: " TEMP_PASSWORD; echo
    read -r -s -p "Confirm temporary password: " confirm; echo
    if [[ -z "$TEMP_PASSWORD" || "$TEMP_PASSWORD" != "$confirm" ]]; then
        echo -e "${RED}Error: passwords are empty or do not match${NC}"
        exit 1
    fi
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
        --email)
            EMAIL="$2"
            shift 2
            ;;
        --password)
            echo -e "${RED}Error: --password was removed: passwords on the command line leak into shell history and process listings.${NC}"
            echo -e "${RED}Omit it (Cognito emails a temporary password) or use --prompt-password.${NC}"
            exit 1
            ;;
        --prompt-password|--suppress-invite)
            PROMPT_PASSWORD=true
            shift
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

# Validate required parameters
if [[ -z "$EMAIL" ]]; then
    echo -e "${RED}Error: Email is required. Use --email <email>${NC}"
    usage
fi

# Validate environment
if [[ ! "$ENVIRONMENT" =~ ^(dev|prod)$ ]]; then
    echo -e "${RED}Error: Environment must be 'dev' or 'prod'${NC}"
    exit 1
fi

# Validate email format (basic check)
if [[ ! "$EMAIL" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]]; then
    echo -e "${RED}Error: Invalid email format${NC}"
    exit 1
fi

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Create Admin Portal User${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "Environment: ${YELLOW}${ENVIRONMENT}${NC}"
echo -e "Region: ${YELLOW}${REGION}${NC}"
echo -e "Email: ${YELLOW}${EMAIL}${NC}"
echo -e "${GREEN}========================================${NC}"
echo

# Check AWS credentials
if ! aws sts get-caller-identity &> /dev/null; then
    echo -e "${RED}Error: AWS credentials not configured properly.${NC}"
    exit 1
fi

# Get Cognito User Pool ID from CloudFormation
echo -e "${YELLOW}Retrieving Cognito User Pool ID...${NC}"
USER_POOL_ID=$(aws cloudformation describe-stacks \
    --stack-name "AnthropicProxy-${ENVIRONMENT}-Cognito" \
    --region "$REGION" \
    --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' \
    --output text 2>/dev/null)

if [[ -z "$USER_POOL_ID" || "$USER_POOL_ID" == "None" ]]; then
    echo -e "${RED}Error: Could not find Cognito User Pool for environment '${ENVIRONMENT}'${NC}"
    echo -e "${RED}Make sure the Cognito stack is deployed: ./scripts/deploy.sh -e ${ENVIRONMENT}${NC}"
    exit 1
fi

echo -e "${GREEN}Found User Pool: ${USER_POOL_ID}${NC}"

# Check if user already exists
echo -e "${YELLOW}Checking if user already exists...${NC}"
if aws cognito-idp admin-get-user \
    --user-pool-id "$USER_POOL_ID" \
    --username "$EMAIL" \
    --region "$REGION" &> /dev/null; then
    echo -e "${YELLOW}User already exists. Do you want to set a new temporary password? (y/n)${NC}"
    read -p "" -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        read_temp_password
        echo -e "${YELLOW}Setting temporary password...${NC}"
        aws cognito-idp admin-set-user-password \
            --user-pool-id "$USER_POOL_ID" \
            --username "$EMAIL" \
            --password "$TEMP_PASSWORD" \
            --no-permanent \
            --region "$REGION"
        unset TEMP_PASSWORD
        echo -e "${GREEN}Temporary password set. Share it with the user out of band.${NC}"
    else
        echo -e "${YELLOW}Aborted.${NC}"
        exit 0
    fi
else
    # Create new user
    echo -e "${YELLOW}Creating new user...${NC}"

    CREATE_ARGS=(
        "--user-pool-id" "$USER_POOL_ID"
        "--username" "$EMAIL"
        "--user-attributes" "Name=email,Value=$EMAIL" "Name=email_verified,Value=true"
        "--region" "$REGION"
    )

    if [[ "$PROMPT_PASSWORD" == true ]]; then
        read_temp_password
        CREATE_ARGS+=("--temporary-password" "$TEMP_PASSWORD" "--message-action" "SUPPRESS")
    else
        # Cognito generates the one-time password and emails it to the user
        CREATE_ARGS+=("--desired-delivery-mediums" "EMAIL")
    fi

    aws cognito-idp admin-create-user "${CREATE_ARGS[@]}" > /dev/null
    unset TEMP_PASSWORD

    echo -e "${GREEN}User created successfully!${NC}"
fi

# Display credentials
echo
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Admin User Created${NC}"
echo -e "${GREEN}========================================${NC}"
echo
echo -e "${BLUE}Login:${NC}"
echo -e "  Email: ${YELLOW}${EMAIL}${NC}"
if [[ "$PROMPT_PASSWORD" == true ]]; then
    echo -e "  Temporary password: the one you entered (share it with the user out of band)"
else
    echo -e "  Temporary password: emailed to ${EMAIL} by Cognito"
fi
echo
echo -e "${BLUE}Admin Portal URL:${NC}"
CF_DOMAIN=$(aws cloudformation describe-stacks \
    --stack-name "AnthropicProxy-${ENVIRONMENT}-ECS" \
    --region "$REGION" \
    --query 'Stacks[0].Outputs[?OutputKey==`CloudFrontDomainName`].OutputValue' \
    --output text 2>/dev/null || echo "N/A")

if [[ "$CF_DOMAIN" != "N/A" && -n "$CF_DOMAIN" ]]; then
    echo -e "  https://${CF_DOMAIN}/admin/"
else
    ALB_DNS=$(aws cloudformation describe-stacks \
        --stack-name "AnthropicProxy-${ENVIRONMENT}-ECS" \
        --region "$REGION" \
        --query 'Stacks[0].Outputs[?OutputKey==`ALBDNSName`].OutputValue' \
        --output text 2>/dev/null || echo "N/A")

    if [[ "$ALB_DNS" != "N/A" && -n "$ALB_DNS" ]]; then
        echo -e "  http://${ALB_DNS}/admin/"
        echo -e "  ${YELLOW}(HTTP only - CloudFront not enabled)${NC}"
    else
        echo -e "  ${YELLOW}(URL not found - check ECS stack deployment)${NC}"
    fi
fi

echo
echo -e "${YELLOW}IMPORTANT:${NC}"
echo -e "  - The user must change their password on first login"
echo -e "  - Password requirements: 12+ chars, uppercase, lowercase, number, special char"
echo -e "  - The temporary password is one-time: it expires and must be changed at first login"
echo
echo -e "${GREEN}========================================${NC}"
