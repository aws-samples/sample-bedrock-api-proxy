#!/bin/bash
set -e

# Anthropic Proxy CDK Deployment Script
# This script deploys the Anthropic-Bedrock API proxy to AWS

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
ENVIRONMENT="prod"
REGION="${AWS_REGION:-us-west-2}"
PLATFORM="arm64"
LAUNCH_TYPE="fargate"
SKIP_BUILD=false
DESTROY=false
DEPLOY_ALL=false

# Usage
usage() {
    cat <<EOF
Usage: $0 [OPTIONS]

Deploy Anthropic-Bedrock API Proxy to AWS using CDK

OPTIONS:
    -e, --environment ENV      Environment to deploy (dev|prod) [default: prod]
    -r, --region REGION        AWS region [default: us-west-2]
    -p, --platform PLATFORM    Platform architecture (arm64|amd64) [default:arm64]
    -l, --launch-type TYPE     ECS launch type (fargate|ec2) [default: fargate]
                               - fargate: Serverless, no Docker access, lower cost
                               - ec2: EC2 instances, supports PTC (Docker socket)
    -s, --skip-build           Skip npm install and build
    -a, --all                  Deploy ALL stacks including Network/DynamoDB/Cognito
                               [default: deploy only the -ECS application stack]
                               Only needed for first-time bootstrap or a
                               deliberate infrastructure change. Run 'cdk diff'
                               first — on an environment whose VPC has drifted
                               from this code, deploying the Network stack can
                               orphan live NAT gateways and VPC endpoints.
    -d, --destroy              Destroy the stack instead of deploying
    -h, --help                 Show this help message

EXAMPLES:
    # Deploy to dev environment with Fargate (default, no PTC support)
    ./scripts/deploy.sh -e dev -p arm64

    # Deploy to dev with EC2 launch type (enables PTC support)
    ./scripts/deploy.sh -e dev -p arm64 -l ec2

    # Deploy to prod with AMD64 and EC2 for PTC
    ./scripts/deploy.sh -e prod -r us-east-1 -p amd64 -l ec2

    # Destroy dev environment
    ./scripts/deploy.sh -e dev -p arm64 -d

LAUNCH TYPE COMPARISON:
    +----------+------------+-----------+----------+-------------+
    | Type     | PTC Support| Cost      | Scaling  | Management  |
    +----------+------------+-----------+----------+-------------+
    | fargate  | No         | Pay/use   | Fast     | Zero        |
    | ec2      | Yes        | Instance  | Slower   | Some        |
    +----------+------------+-----------+----------+-------------+

    * Use 'fargate' (default) for most deployments
    * Use 'ec2' only if you need Programmatic Tool Calling (PTC) feature

WEB SEARCH (via environment variables):
    Enable web search by setting env vars before running this script:

    ENABLE_WEB_SEARCH=true \\
    WEB_SEARCH_PROVIDER=tavily \\
    WEB_SEARCH_API_KEY=tvly-your-api-key \\
    ./scripts/deploy.sh -e prod -p arm64

CACHE TTL (via environment variables):
    Set proxy-level default cache TTL:

    DEFAULT_CACHE_TTL=1h \\
    ./scripts/deploy.sh -e prod -p arm64

OPENAI COMPAT / PASSTHROUGH (via environment variables):
    Enable Bedrock Mantle-backed OpenAI compatibility:

    ENABLE_OPENAI_COMPAT=true \\
    ENABLE_OPENAI_PASSTHROUGH=true \\
    BEDROCK_API_KEY=your-bedrock-api-key \\
    MANTLE_ENDPOINT_URL=https://bedrock-mantle.us-west-2.api.aws/v1 \\
    ./scripts/deploy.sh -e prod -p arm64

OTEL TRACING (via environment variables):
    Enable OpenTelemetry tracing by setting env vars before running this script:

    ENABLE_TRACING=true \\
    OTEL_EXPORTER_OTLP_ENDPOINT=https://us.cloud.langfuse.com/api/public/otel \\
    OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf \\
    OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic <base64>" \\
    OTEL_SERVICE_NAME=anthropic-bedrock-proxy \\
    OTEL_TRACE_CONTENT=true \\
    OTEL_TRACE_SAMPLING_RATIO=1.0 \\
    ./scripts/deploy.sh -e prod -p arm64

PREREQUISITES:
    - AWS CLI configured with appropriate credentials
    - Node.js and npm installed
    - Docker installed (for building container images)
    - CDK bootstrapped in target account/region

EOF
    exit 1
}

# Load deploy-time secrets from cdk/.env.local (gitignored) if present.
#
# Only secrets belong there; shared configuration lives in config/config.ts so
# it cannot differ silently between machines.
#
# Values already present in the environment take precedence, so a one-off
# `BEDROCK_API_KEY=... ./deploy.sh` still overrides the file. That is why this
# assigns each key individually instead of `set -a; . file` — sourcing would
# clobber an explicit override with the file's value.
# CDK_ENV_LOCAL_FILE overrides the path (tests point it at /dev/null so they do
# not depend on whether the developer happens to have a .env.local).
ENV_LOCAL="${CDK_ENV_LOCAL_FILE:-$(dirname "$0")/../.env.local}"
if [ -f "$ENV_LOCAL" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            ''|'#'*) continue ;;          # blank or comment
            *=*) ;;
            *) continue ;;                # not an assignment
        esac
        key="${line%%=*}"
        value="${line#*=}"
        # Trim with parameter expansion only — this must work before PATH is
        # usable, so no sed/tr/awk here.
        while case "$key" in [[:space:]]*) true ;; *) false ;; esac; do key="${key# }"; key="${key#	}"; done
        while case "$key" in *[[:space:]]) true ;; *) false ;; esac; do key="${key% }"; key="${key%	}"; done
        while case "$value" in [[:space:]]*) true ;; *) false ;; esac; do value="${value# }"; value="${value#	}"; done
        while case "$value" in *[[:space:]]) true ;; *) false ;; esac; do value="${value% }"; value="${value%	}"; done
        case "$value" in
            \"*\") value="${value#\"}"; value="${value%\"}" ;;
            \'*\') value="${value#\'}"; value="${value%\'}" ;;
        esac
        [ -n "$key" ] || continue
        [ -n "$value" ] || continue       # skip placeholders like `KEY=`
        # Only set it if not already provided in the environment, so an explicit
        # `KEY=... ./deploy.sh` still wins.
        if [ -z "$(eval "printf '%s' \"\${$key:-}\"")" ]; then
            export "$key=$value"
        fi
    done < "$ENV_LOCAL"
    echo -e "${GREEN}✓ Loaded deploy secrets from ${ENV_LOCAL##*/}${NC}"
fi

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
        -p|--platform)
            PLATFORM="$2"
            shift 2
            ;;
        -l|--launch-type)
            LAUNCH_TYPE="$2"
            shift 2
            ;;
        -s|--skip-build)
            SKIP_BUILD=true
            shift
            ;;
        -a|--all)
            DEPLOY_ALL=true
            shift
            ;;
        -d|--destroy)
            DESTROY=true
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

# Validate platform (required)
if [[ -z "$PLATFORM" ]]; then
    echo -e "${RED}Error: Platform is required. Use -p arm64 or -p amd64${NC}"
    usage
fi

if [[ ! "$PLATFORM" =~ ^(arm64|amd64)$ ]]; then
    echo -e "${RED}Error: Platform must be 'arm64' or 'amd64'${NC}"
    exit 1
fi

# Validate environment
if [[ ! "$ENVIRONMENT" =~ ^(dev|prod)$ ]]; then
    echo -e "${RED}Error: Environment must be 'dev' or 'prod'${NC}"
    exit 1
fi

# Validate launch type
if [[ ! "$LAUNCH_TYPE" =~ ^(fargate|ec2)$ ]]; then
    echo -e "${RED}Error: Launch type must be 'fargate' or 'ec2'${NC}"
    exit 1
fi

# Validate Bedrock Mantle credentials for new deployments.
#
# Implementation notes — this guard must work before PATH is usable:
#   - `${VAR,,}` (lowercase expansion) is bash 4+ only and aborts with "bad
#     substitution" on the bash 3.2 that ships with macOS.
#   - External tools (tr, awk, ...) are unavailable if PATH is not yet set up.
# So match case-insensitively with a bash 3.2 builtin pattern instead.
#
# The authoritative checks live in getConfig() in cdk/config/config.ts, which
# runs for every synth/diff/deploy; this is an early, friendlier duplicate.
is_true() {
    case "$1" in
        [Tt][Rr][Uu][Ee]) return 0 ;;
        *) return 1 ;;
    esac
}

if [[ "$DESTROY" != true && -z "${BEDROCK_API_KEY:-}" ]] \
   && { is_true "${ENABLE_OPENAI_COMPAT:-}" || is_true "${ENABLE_OPENAI_PASSTHROUGH:-}"; }; then
    if is_true "${ENABLE_OPENAI_COMPAT:-}"; then
        echo -e "${RED}Error: BEDROCK_API_KEY is required when ENABLE_OPENAI_COMPAT=true.${NC}"
    else
        echo -e "${RED}Error: BEDROCK_API_KEY is required when ENABLE_OPENAI_PASSTHROUGH=true.${NC}"
    fi
    if [[ -n "${OPENAI_API_KEY:-}" ]]; then
        echo -e "${YELLOW}OPENAI_API_KEY is deprecated for Bedrock Mantle. Use BEDROCK_API_KEY instead.${NC}"
    fi
    exit 1
fi

# Determine PTC status
if [[ "$LAUNCH_TYPE" == "ec2" ]]; then
    PTC_STATUS="${GREEN}Enabled${NC}"
else
    PTC_STATUS="${YELLOW}Disabled (requires EC2 launch type)${NC}"
fi

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Anthropic Proxy Deployment${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "Environment: ${YELLOW}${ENVIRONMENT}${NC}"
echo -e "Region: ${YELLOW}${REGION}${NC}"
echo -e "Platform: ${YELLOW}${PLATFORM}${NC}"
echo -e "Launch Type: ${YELLOW}${LAUNCH_TYPE}${NC}"
echo -e "PTC Support: ${PTC_STATUS}"
echo -e "Action: ${YELLOW}$([ "$DESTROY" = true ] && echo "DESTROY" || echo "DEPLOY")${NC}"
echo -e "${GREEN}========================================${NC}"
echo

# Show EC2 info if using EC2 launch type
if [[ "$LAUNCH_TYPE" == "ec2" ]]; then
    echo -e "${BLUE}EC2 Launch Type Configuration:${NC}"
    if [[ "$ENVIRONMENT" == "dev" ]]; then
        if [[ "$PLATFORM" == "arm64" ]]; then
            echo -e "  Instance Type: ${YELLOW}t4g.medium (ARM64 Graviton)${NC}"
        else
            echo -e "  Instance Type: ${YELLOW}t3.medium (x86_64)${NC}"
        fi
        echo -e "  Spot Instances: ${YELLOW}Yes (cost savings)${NC}"
    else
        if [[ "$PLATFORM" == "arm64" ]]; then
            echo -e "  Instance Type: ${YELLOW}t4g.large (ARM64 Graviton)${NC}"
        else
            echo -e "  Instance Type: ${YELLOW}t3.large (x86_64)${NC}"
        fi
        echo -e "  Spot Instances: ${YELLOW}No (production stability)${NC}"
    fi
    echo -e "  Docker Socket: ${YELLOW}Mounted (for PTC)${NC}"
    echo
fi

# Check prerequisites
echo -e "${YELLOW}Checking prerequisites...${NC}"

if ! command -v aws &> /dev/null; then
    echo -e "${RED}Error: AWS CLI not found. Please install AWS CLI.${NC}"
    exit 1
fi

if ! command -v node &> /dev/null; then
    echo -e "${RED}Error: Node.js not found. Please install Node.js.${NC}"
    exit 1
fi

if ! command -v docker &> /dev/null; then
    echo -e "${RED}Error: Docker not found. Please install Docker.${NC}"
    exit 1
fi

# Check AWS credentials
if ! aws sts get-caller-identity &> /dev/null; then
    echo -e "${RED}Error: AWS credentials not configured properly.${NC}"
    exit 1
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo -e "${GREEN}✓ AWS Account: ${ACCOUNT_ID}${NC}"

# Check if CDK is bootstrapped
echo -e "${YELLOW}Checking CDK bootstrap status...${NC}"
if ! aws cloudformation describe-stacks --stack-name CDKToolkit --region "$REGION" &> /dev/null; then
    echo -e "${YELLOW}Warning: CDK not bootstrapped in ${REGION}.${NC}"
    echo -e "${YELLOW}Run: cdk bootstrap aws://${ACCOUNT_ID}/${REGION}${NC}"
    read -p "Do you want to bootstrap now? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        cd "$(dirname "$0")/.." || exit 1
        CDK_PLATFORM="$PLATFORM" CDK_LAUNCH_TYPE="$LAUNCH_TYPE" CDK_ENVIRONMENT="$ENVIRONMENT" \
            npx cdk bootstrap "aws://${ACCOUNT_ID}/${REGION}"
    else
        echo -e "${RED}Cannot proceed without CDK bootstrap.${NC}"
        exit 1
    fi
fi
echo -e "${GREEN}✓ CDK bootstrapped${NC}"

# Navigate to CDK directory
cd "$(dirname "$0")/.." || exit 1

# Install dependencies and build
if [ "$SKIP_BUILD" = false ]; then
    echo -e "${YELLOW}Installing dependencies...${NC}"
    npm install
    echo -e "${GREEN}✓ Dependencies installed${NC}"

    echo -e "${YELLOW}Building CDK project...${NC}"
    npm run build
    echo -e "${GREEN}✓ Build complete${NC}"
fi

# Export environment variables
export AWS_REGION="$REGION"
export CDK_DEFAULT_REGION="$REGION"
export CDK_DEFAULT_ACCOUNT="$ACCOUNT_ID"
export CDK_ENVIRONMENT="$ENVIRONMENT"
export CDK_PLATFORM="$PLATFORM"
export CDK_LAUNCH_TYPE="$LAUNCH_TYPE"

# Must match stackPrefix in bin/app.ts
STACK_PREFIX="AnthropicProxy-${ENVIRONMENT}"

# Clean cdk.out directory to prevent ENAMETOOLONG errors
echo -e "${YELLOW}Cleaning previous CDK output...${NC}"
rm -rf cdk.out
echo -e "${GREEN}✓ Cleanup complete${NC}"

# Perform action
if [ "$DESTROY" = true ]; then
    echo -e "${RED}========================================${NC}"
    echo -e "${RED}WARNING: DESTROYING INFRASTRUCTURE${NC}"
    echo -e "${RED}========================================${NC}"
    echo -e "${RED}This will delete all resources in the ${ENVIRONMENT} environment.${NC}"
    read -p "Are you sure? Type 'yes' to confirm: " CONFIRM

    if [ "$CONFIRM" != "yes" ]; then
        echo -e "${YELLOW}Destroy cancelled.${NC}"
        exit 0
    fi

    echo -e "${YELLOW}Destroying stacks...${NC}"
    npx cdk destroy --all -c environment="$ENVIRONMENT" --force
    echo -e "${GREEN}✓ Infrastructure destroyed${NC}"
else
    echo -e "${YELLOW}Synthesizing CloudFormation templates...${NC}"
    npx cdk synth -c environment="$ENVIRONMENT"
    echo -e "${GREEN}✓ Synthesis complete${NC}"

    if [ "$DEPLOY_ALL" = true ]; then
        echo -e "${YELLOW}Deploying ALL stacks (--all)...${NC}"
        npx cdk deploy --all -c environment="$ENVIRONMENT" --require-approval never
    else
        # Default: deploy only the application stack.
        #
        # `--all` would also deploy the Network stack. That is unsafe against a
        # long-lived environment whose VPC has drifted from this code (manually
        # created NAT gateways, VPC endpoints, or a subnet layout that no longer
        # matches maxAzs): CDK would provision parallel resources and orphan the
        # live ones, taking egress down with it.
        #
        # The application stack is what a code deploy needs. Use --all only for
        # a first-time bootstrap or a deliberate, reviewed network change, and
        # run `cdk diff` first.
        echo -e "${YELLOW}Deploying ${STACK_PREFIX}-ECS only (application stack)...${NC}"
        echo -e "${BLUE}  Network/DynamoDB/Cognito stacks are NOT touched.${NC}"
        echo -e "${BLUE}  Use --all if you intend to deploy infrastructure too.${NC}"
        npx cdk deploy "${STACK_PREFIX}-ECS" --exclusively \
            -c environment="$ENVIRONMENT" --require-approval never
    fi

    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}Deployment Complete!${NC}"
    echo -e "${GREEN}========================================${NC}"

    # Get outputs
    echo -e "${YELLOW}Retrieving stack outputs...${NC}"

    ALB_DNS=$(aws cloudformation describe-stacks \
        --stack-name "AnthropicProxy-${ENVIRONMENT}-ECS" \
        --region "$REGION" \
        --query 'Stacks[0].Outputs[?OutputKey==`ALBDNSName`].OutputValue' \
        --output text 2>/dev/null || echo "N/A")

    LAUNCH_TYPE_OUTPUT=$(aws cloudformation describe-stacks \
        --stack-name "AnthropicProxy-${ENVIRONMENT}-ECS" \
        --region "$REGION" \
        --query 'Stacks[0].Outputs[?OutputKey==`LaunchType`].OutputValue' \
        --output text 2>/dev/null || echo "N/A")

    PTC_ENABLED=$(aws cloudformation describe-stacks \
        --stack-name "AnthropicProxy-${ENVIRONMENT}-ECS" \
        --region "$REGION" \
        --query 'Stacks[0].Outputs[?OutputKey==`PTCEnabled`].OutputValue' \
        --output text 2>/dev/null || echo "N/A")

    SECRET_NAME=$(aws cloudformation describe-stacks \
        --stack-name "AnthropicProxy-${ENVIRONMENT}-ECS" \
        --region "$REGION" \
        --query 'Stacks[0].Outputs[?OutputKey==`MasterAPIKeySecretName`].OutputValue' \
        --output text 2>/dev/null || echo "N/A")

    ADMIN_PORTAL_URL=$(aws cloudformation describe-stacks \
        --stack-name "AnthropicProxy-${ENVIRONMENT}-ECS" \
        --region "$REGION" \
        --query 'Stacks[0].Outputs[?OutputKey==`AdminPortalURL`].OutputValue' \
        --output text 2>/dev/null || echo "N/A")

    CLOUDFRONT_URL=$(aws cloudformation describe-stacks \
        --stack-name "AnthropicProxy-${ENVIRONMENT}-ECS" \
        --region "$REGION" \
        --query 'Stacks[0].Outputs[?OutputKey==`ProxyURL`].OutputValue' \
        --output text 2>/dev/null || echo "")

    CLOUDFRONT_DOMAIN=$(aws cloudformation describe-stacks \
        --stack-name "AnthropicProxy-${ENVIRONMENT}-ECS" \
        --region "$REGION" \
        --query 'Stacks[0].Outputs[?OutputKey==`CloudFrontDomainName`].OutputValue' \
        --output text 2>/dev/null || echo "")

    # Get Cognito outputs (if Cognito stack exists)
    COGNITO_USER_POOL_ID=$(aws cloudformation describe-stacks \
        --stack-name "AnthropicProxy-${ENVIRONMENT}-Cognito" \
        --region "$REGION" \
        --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' \
        --output text 2>/dev/null || echo "N/A")

    COGNITO_CLIENT_ID=$(aws cloudformation describe-stacks \
        --stack-name "AnthropicProxy-${ENVIRONMENT}-Cognito" \
        --region "$REGION" \
        --query 'Stacks[0].Outputs[?OutputKey==`UserPoolClientId`].OutputValue' \
        --output text 2>/dev/null || echo "N/A")

    echo
    echo -e "${GREEN}Access URLs:${NC}"
    if [[ -n "$CLOUDFRONT_URL" && "$CLOUDFRONT_URL" != "None" ]]; then
        echo -e "  API Proxy (HTTPS): ${CLOUDFRONT_URL}"
        ADMIN_HTTPS_URL=$(aws cloudformation describe-stacks \
            --stack-name "AnthropicProxy-${ENVIRONMENT}-ECS" \
            --region "$REGION" \
            --query 'Stacks[0].Outputs[?OutputKey==`AdminPortalHTTPSURL`].OutputValue' \
            --output text 2>/dev/null || echo "")
        if [[ -n "$ADMIN_HTTPS_URL" && "$ADMIN_HTTPS_URL" != "None" ]]; then
            echo -e "  Admin Portal (HTTPS): ${ADMIN_HTTPS_URL}"
        fi
        echo -e "  ALB (internal HTTP): http://${ALB_DNS}"
    else
        echo -e "  API Proxy: http://${ALB_DNS}"
        if [[ "$ADMIN_PORTAL_URL" != "N/A" ]]; then
            echo -e "  Admin Portal: ${ADMIN_PORTAL_URL}"
        fi
    fi
    echo
    echo -e "${GREEN}Deployment Configuration:${NC}"
    echo -e "  Launch Type: ${LAUNCH_TYPE_OUTPUT}"
    echo -e "  PTC Enabled: ${PTC_ENABLED}"
    echo
    echo -e "${GREEN}Master API Key Secret:${NC}"
    echo -e "  Secret Name: ${SECRET_NAME}"
    echo -e "  Retrieve with: aws secretsmanager get-secret-value --secret-id ${SECRET_NAME} --region ${REGION}"

    # Display Cognito info if available
    if [[ "$COGNITO_USER_POOL_ID" != "N/A" ]]; then
        echo
        echo -e "${GREEN}Cognito (Admin Portal Authentication):${NC}"
        echo -e "  User Pool ID: ${COGNITO_USER_POOL_ID}"
        echo -e "  Client ID: ${COGNITO_CLIENT_ID}"
        echo -e "  Region: ${REGION}"
    fi

    echo
    echo -e "${YELLOW}Next Steps:${NC}"
    echo -e "  1. Create API keys using: ./scripts/create-api-key.sh"
    if [[ -n "$CLOUDFRONT_URL" && "$CLOUDFRONT_URL" != "None" ]]; then
        echo -e "  2. Test the health endpoint: curl ${CLOUDFRONT_URL}/health"
    else
        echo -e "  2. Test the health endpoint: curl http://${ALB_DNS}/health"
    fi
    if [[ "$PTC_ENABLED" == "true" ]]; then
        if [[ -n "$CLOUDFRONT_URL" && "$CLOUDFRONT_URL" != "None" ]]; then
            echo -e "  3. Test PTC health: curl ${CLOUDFRONT_URL}/health/ptc"
        else
            echo -e "  3. Test PTC health: curl http://${ALB_DNS}/health/ptc"
        fi
    fi
    if [[ "$COGNITO_USER_POOL_ID" != "N/A" ]]; then
        echo -e "  4. Create admin user: ./scripts/create-admin-user.sh -e ${ENVIRONMENT} -r ${REGION} --email <admin@example.com>"
    fi
    echo -e "  5. Review CloudWatch logs in the AWS Console"
fi

echo -e "${GREEN}========================================${NC}"
