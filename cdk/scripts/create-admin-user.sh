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
TEMP_PASSWORD=""
SUPPRESS_INVITE=false

# Usage
usage() {
    cat <<EOF
Usage: $0 [OPTIONS]

Create an admin user for the Anthropic Proxy Admin Portal (Cognito)

OPTIONS:
    -e, --environment ENV      Environment (dev|prod) [default: dev]
    -r, --region REGION        AWS region [default: us-west-2]
    --email EMAIL              User email address (required)
    --password PASSWORD        Temporary password (optional, will be generated if not provided)
    --suppress-invite          Don't send invitation email (user must be told password)
    -h, --help                 Show this help message

EXAMPLES:
    # Create admin user with auto-generated temporary password
    ./scripts/create-admin-user.sh -e dev --email admin@example.com

    # Create admin user with specific temporary password
    ./scripts/create-admin-user.sh -e prod -r us-east-1 --email admin@example.com --password 'TempPass123!'

    # Create user without sending invitation email
    ./scripts/create-admin-user.sh -e dev --email admin@example.com --suppress-invite

NOTES:
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

# Generate a random password that meets Cognito requirements
generate_password() {
    # Generate a password with: uppercase, lowercase, numbers, special chars
    local password=""
    # 4 uppercase
    password+=$(cat /dev/urandom | tr -dc 'A-Z' | head -c 4)
    # 4 lowercase
    password+=$(cat /dev/urandom | tr -dc 'a-z' | head -c 4)
    # 3 numbers
    password+=$(cat /dev/urandom | tr -dc '0-9' | head -c 3)
    # 2 special chars
    password+=$(cat /dev/urandom | tr -dc '@#$%&*!' | head -c 2)
    # Shuffle the password
    echo "$password" | fold -w1 | shuf | tr -d '\n'
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
            TEMP_PASSWORD="$2"
            shift 2
            ;;
        --suppress-invite)
            SUPPRESS_INVITE=true
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

# Generate password if not provided
if [[ -z "$TEMP_PASSWORD" ]]; then
    TEMP_PASSWORD=$(generate_password)
    echo -e "${YELLOW}Generated temporary password${NC}"
fi

# Check if user already exists
echo -e "${YELLOW}Checking if user already exists...${NC}"
if aws cognito-idp admin-get-user \
    --user-pool-id "$USER_POOL_ID" \
    --username "$EMAIL" \
    --region "$REGION" &> /dev/null; then
    echo -e "${YELLOW}User already exists. Do you want to reset their password? (y/n)${NC}"
    read -p "" -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}Resetting password...${NC}"
        aws cognito-idp admin-set-user-password \
            --user-pool-id "$USER_POOL_ID" \
            --username "$EMAIL" \
            --password "$TEMP_PASSWORD" \
            --no-permanent \
            --region "$REGION"
        echo -e "${GREEN}Password reset successfully!${NC}"
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
        "--temporary-password" "$TEMP_PASSWORD"
        "--user-attributes" "Name=email,Value=$EMAIL" "Name=email_verified,Value=true"
        "--region" "$REGION"
    )

    if [[ "$SUPPRESS_INVITE" == true ]]; then
        CREATE_ARGS+=("--message-action" "SUPPRESS")
    fi

    aws cognito-idp admin-create-user "${CREATE_ARGS[@]}"

    echo -e "${GREEN}User created successfully!${NC}"
fi

# Display credentials
echo
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Admin User Created${NC}"
echo -e "${GREEN}========================================${NC}"
echo
echo -e "${BLUE}Login Credentials:${NC}"
echo -e "  Email: ${YELLOW}${EMAIL}${NC}"
echo -e "  Temporary Password: ${YELLOW}${TEMP_PASSWORD}${NC}"
echo
echo -e "${BLUE}Admin Portal URL:${NC}"
ALB_DNS=$(aws cloudformation describe-stacks \
    --stack-name "AnthropicProxy-${ENVIRONMENT}-ECS" \
    --region "$REGION" \
    --query 'Stacks[0].Outputs[?OutputKey==`ALBDNSName`].OutputValue' \
    --output text 2>/dev/null || echo "N/A")

if [[ "$ALB_DNS" != "N/A" ]]; then
    echo -e "  http://${ALB_DNS}/admin/"
else
    echo -e "  ${YELLOW}(ALB not found - check ECS stack deployment)${NC}"
fi

echo
echo -e "${YELLOW}IMPORTANT:${NC}"
echo -e "  - The user must change their password on first login"
echo -e "  - Password requirements: 12+ chars, uppercase, lowercase, number, special char"
echo -e "  - Save these credentials securely - the temporary password won't be shown again"
echo
echo -e "${GREEN}========================================${NC}"
