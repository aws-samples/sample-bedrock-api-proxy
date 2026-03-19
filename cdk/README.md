# Anthropic-Bedrock API Proxy - CDK Infrastructure

This directory contains AWS CDK infrastructure code for deploying the Anthropic-Bedrock API Proxy to AWS ECS Fargate.

## Architecture

```
Internet
    │
    ▼
Application Load Balancer (Regional)
    │
    ├── /admin/*  ──────►  Admin Portal Service (Fargate)
    │                       └── FastAPI + React (port 8005)
    │                            └── Cognito Authentication
    │
    ├── /api/*  ───────►  Admin Portal Service (Fargate)
    │                       └── API endpoints for admin portal
    │                            (auth, dashboard, API keys, pricing)
    │
    └── /*  ────────────►  API Proxy Service (Fargate/EC2)
                            └── FastAPI (port 8000)
                                 └── AWS Bedrock (/v1/messages, /health)
    │
    ▼
DynamoDB (API keys, usage, pricing, model mapping)
```

### Key Features

- **Application Load Balancer**: HTTP endpoint with path-based routing
- **ECS Fargate**: Serverless container orchestration with auto-scaling
- **Admin Portal**: Web-based management UI with Cognito authentication
- **DynamoDB**: Five tables for API keys, usage tracking, usage stats, pricing, and model mapping
- **VPC**: Multi-AZ deployment with public/private subnets
- **Security**: WAF rules, security groups, IAM roles with least privilege
- **Monitoring**: CloudWatch logs, Container Insights, metrics

## Prerequisites

### Required Tools

1. **AWS CLI** (v2.x or later)
   ```bash
   aws --version
   ```

2. **Node.js** (v18.x or later) and npm
   ```bash
   node --version
   npm --version
   ```

3. **Docker** (for building container images)
   ```bash
   docker --version
   ```

4. **AWS CDK** (installed automatically via npm)

### AWS Account Setup

1. **Configure AWS Credentials**
   ```bash
   aws configure
   # Or use environment variables:
   export AWS_ACCESS_KEY_ID=your-access-key
   export AWS_SECRET_ACCESS_KEY=your-secret-key
   export AWS_REGION=us-west-2
   ```

2. **Bootstrap CDK** (one-time per account/region)
   ```bash
   cd cdk
   npm install
   npx cdk bootstrap aws://ACCOUNT-ID/REGION
   ```

3. **Required IAM Permissions**
   Your AWS user/role needs permissions for:
   - CloudFormation (full)
   - ECS, ECR (full)
   - EC2, VPC (full)
   - DynamoDB (full)
   - IAM (role creation)
   - Secrets Manager (create/read secrets)
   - CloudWatch Logs (create/write)

## Quick Start

### 1. Install Dependencies

```bash
cd cdk
npm install
```

**Note:** For production, you should add HTTPS by configuring an SSL certificate on the ALB.

## Configuration

### Environment Configuration

Edit `config/config.ts` to customize environments:

```typescript
export const environments = {
  dev: {
    region: 'us-west-2',
    ecsDesiredCount: 1,
    ecsMinCapacity: 1,
    ecsMaxCapacity: 2,
    // ... more settings
  },
  prod: {
    region: 'us-west-2',
    ecsDesiredCount: 2,
    ecsMinCapacity: 2,
    ecsMaxCapacity: 10,
    // ... more settings
  },
};
```

### Key Configuration Options

| Setting | Dev Default | Prod Default | Description |
|---------|-------------|--------------|-------------|
| `ecsDesiredCount` | 1 | 2 | Initial number of tasks |
| `ecsCpu` | 512 | 1024 | CPU units per task |
| `ecsMemory` | 1024 | 2048 | Memory (MB) per task |
| `ecsMinCapacity` | 1 | 2 | Min auto-scaling tasks |
| `ecsMaxCapacity` | 2 | 10 | Max auto-scaling tasks |
| `maxAzs` | 2 | 3 | Availability zones |
| `dynamodbBillingMode` | PAY_PER_REQUEST | PAY_PER_REQUEST | DynamoDB billing |

## Deployment Options

### Deploy to Production

```bash
./scripts/deploy.sh -e prod -r us-west-2 -p arm64
```

**Production differences:**
- More ECS tasks (2-10 auto-scaling)
- 3 availability zones
- Container Insights enabled
- 30-day log retention
- VPC endpoints for cost optimization



Useful for quick redeployments when dependencies haven't changed.

### Destroy Infrastructure

```bash
./scripts/deploy.sh -d
```

**Warning:** This deletes all resources. DynamoDB tables with `RETAIN` policy must be deleted manually.

## Stack Details

### 1. DynamoDB Stack

Creates five tables:

#### API Keys Table
- **Table Name:** `anthropic-proxy-{env}-api-keys`
- **Primary Key:** `api_key` (String)
- **GSI:** `user_id-index` for querying by user
- **Attributes:** `api_key`, `user_id`, `name`, `is_active`, `rate_limit`, `monthly_budget`, `budget_used`
- **Encryption:** AWS-managed
- **Backup:** Point-in-time recovery (prod only)

#### Usage Table
- **Table Name:** `anthropic-proxy-{env}-usage`
- **Primary Key:** `api_key` (String), `timestamp` (String)
- **GSI:** `request_id-index` for request lookups
- **TTL:** Automatic expiration via `ttl` attribute
- **Purpose:** Track individual API requests

#### Usage Stats Table
- **Table Name:** `anthropic-proxy-{env}-usage-stats`
- **Primary Key:** `api_key` (String)
- **Purpose:** Aggregated usage statistics (total tokens, requests)
- **Updated:** Every 5 minutes by admin portal background task

#### Model Mapping Table
- **Table Name:** `anthropic-proxy-{env}-model-mapping`
- **Primary Key:** `anthropic_model_id` (String)
- **Purpose:** Map Anthropic model IDs to Bedrock ARNs

#### Model Pricing Table
- **Table Name:** `anthropic-proxy-{env}-model-pricing`
- **Primary Key:** `model_id` (String)
- **Purpose:** Store model pricing for cost calculations
- **Attributes:** `input_price`, `output_price`, `cache_read_price`, `cache_write_price`

### 2. Network Stack

Creates VPC infrastructure:

- **VPC CIDR:** `10.0.0.0/16` (dev), `10.1.0.0/16` (prod)
- **Subnets:**
  - Public subnets (for ALB)
  - Private subnets with NAT (for ECS tasks)
- **NAT Gateways:** 1 (dev), 3 (prod)
- **VPC Endpoints (prod only):**
  - S3 Gateway Endpoint (free)
  - DynamoDB Gateway Endpoint (free)
  - ECR, CloudWatch Logs, Bedrock Runtime (interface endpoints)

**Security Groups:**
- ALB SG: Allows 80, 443 from internet
- ECS SG: Allows traffic from ALB only

### 3. ECS Stack

Creates container orchestration:

**ECS Cluster:**
- **Name:** `anthropic-proxy-{env}`
- **Container Insights:** Enabled in prod
- **Capacity Provider:** Fargate Spot (optional)

**Task Definition:**
- **CPU:** 512 (dev), 1024 (prod)
- **Memory:** 1024 MB (dev), 2048 MB (prod)
- **Container Image:** Built from Dockerfile in project root
- **Health Check:** `/health` endpoint every 30s
- **Environment Variables:**
  - AWS region, DynamoDB table names
  - Feature flags, rate limits
  - Secrets Manager integration for master API key

**Service:**
- **Desired Count:** 1 (dev), 2 (prod)
- **Auto-scaling:**
  - CPU-based: 70% target
  - Memory-based: 70% target
  - Request count: 1000 per target
- **Deployment:** Rolling updates with circuit breaker
- **Execute Command:** Enabled in dev for debugging

**Application Load Balancer:**
- **Scheme:** Internet-facing
- **Health Check:** `/health` every 30s
- **Deregistration Delay:** 30 seconds
- **Deletion Protection:** Disabled
- **Path-based Routing:**
  - `/admin/*` → Admin Portal Target Group (priority 10)
  - `/api/*` → Admin Portal Target Group (priority 20)
  - `/*` → API Proxy Target Group (default)

### 4. Cognito Stack

Creates authentication resources for the admin portal:

**User Pool:**
- **Name:** `anthropic-proxy-admin-{env}`
- **Sign-in:** Email-based (no username)
- **Self-signup:** Disabled (admin creates users)
- **Password Policy:** 12+ chars, upper/lower/digit/symbol required
- **MFA:** Optional in prod, disabled in dev

**App Client:**
- **Name:** `admin-portal-{env}`
- **Client Secret:** None (SPA cannot keep secrets)
- **Auth Flows:** USER_PASSWORD_AUTH, USER_SRP_AUTH
- **Token Validity:** Access/ID 1 hour, Refresh 30 days

### 5. Admin Portal Service

Creates the admin portal as a separate Fargate service:

**Task Definition:**
- **CPU:** 1024 (1 vCPU)
- **Memory:** 1024 MB
- **Container Image:** Built from `admin_portal/Dockerfile`
- **Port:** 8005

**Service:**
- **Desired Count:** 1 (min)
- **Auto-scaling:** 1-2 tasks (dev), 1-4 tasks (prod)
- **Health Check:** `/health`

**Features:**
- React frontend bundled with FastAPI backend
- Cognito JWT authentication
- API key management (CRUD)
- Usage monitoring and dashboard
- Model pricing configuration

**Access:**
- URL: `http://<ALB-DNS>/admin/`
- First login: Create user in Cognito console

## Admin Portal

The admin portal provides a web-based management interface for the API proxy.

### Accessing the Admin Portal

After deployment, access the admin portal at:
```
http://<ALB-DNS>/admin/
```

Get the ALB DNS from deployment output:
```bash
aws cloudformation describe-stacks \
  --stack-name AnthropicProxy-dev-ECS \
  --query 'Stacks[0].Outputs[?OutputKey==`ALBDNSName`].OutputValue' \
  --output text
```

### Creating Admin Users

Admin users must be created in the Cognito User Pool:

```bash
# Get User Pool ID
USER_POOL_ID=$(aws cloudformation describe-stacks \
  --stack-name AnthropicProxy-dev-Cognito \
  --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' \
  --output text)

# Create admin user
aws cognito-idp admin-create-user \
  --user-pool-id $USER_POOL_ID \
  --username admin@example.com \
  --user-attributes Name=email,Value=admin@example.com Name=email_verified,Value=true \
  --temporary-password "TempPass123!" \
  --message-action SUPPRESS

# Set permanent password (optional)
aws cognito-idp admin-set-user-password \
  --user-pool-id $USER_POOL_ID \
  --username admin@example.com \
  --password "YourSecurePassword123!" \
  --permanent
```

### Admin Portal Features

| Feature | Description |
|---------|-------------|
| **Dashboard** | Overview of API usage, active keys, budget status |
| **API Keys** | Create, update, deactivate, delete API keys |
| **Usage Stats** | View token usage, request counts per key |
| **Pricing** | Configure model pricing for cost calculations |
| **Budget Management** | Set monthly budgets, view MTD usage |

### Admin Portal Configuration

Configure admin portal settings in `config/config.ts`:

```typescript
// Admin Portal
adminPortalEnabled: true,
adminPortalCpu: 1024,          // 1 vCPU (Fargate: 1024 CPU requires 2048-8192 MB memory)
adminPortalMemory: 2048,       // 2 GB
adminPortalMinCapacity: 1,
adminPortalMaxCapacity: 2,
adminPortalContainerPort: 8005,
adminPortalHealthCheckPath: '/health',
```

### Disabling Admin Portal

To deploy without the admin portal:

```typescript
// In config/config.ts
adminPortalEnabled: false,
```

This will skip creating:
- Admin Portal Fargate service
- Cognito User Pool
- ALB routing rules for `/admin/*`

## IAM Roles and Permissions

### Task Execution Role

Used by ECS to start containers:
- Pull images from ECR
- Write logs to CloudWatch
- Retrieve secrets from Secrets Manager

### Task Role

Used by application containers:
- **DynamoDB:** Read/write to all four tables
- **Bedrock:** Invoke models (`bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream`)
- **CloudWatch Logs:** Create log streams and write logs

## Secrets Management

### Master API Key

The master API key is stored in AWS Secrets Manager:

```bash
# Get secret name from stack output
SECRET_NAME="anthropic-proxy-dev-master-api-key"

# Retrieve the key
aws secretsmanager get-secret-value \
  --secret-id "$SECRET_NAME" \
  --region us-west-2 \
  --query 'SecretString' \
  --output text | jq -r '.password'
```

The master key:
- Bypasses DynamoDB authentication
- Has no rate limits
- Should only be used for admin operations

## Monitoring and Logs

### CloudWatch Logs

Log Group: `/ecs/anthropic-proxy-{env}`

```bash
# View recent logs
aws logs tail /ecs/anthropic-proxy-dev --follow

# Filter for errors
aws logs filter-log-events \
  --log-group-name /ecs/anthropic-proxy-dev \
  --filter-pattern "ERROR"
```

### Container Insights (prod only)

View metrics in CloudWatch Console:
- ECS → Clusters → anthropic-proxy-prod → Metrics
- CPU, Memory, Network utilization
- Task count, running tasks

## Cost Optimization

### Development Environment

**Estimated monthly cost:** ~$50-100

- ECS Fargate: 1 task × 0.5 vCPU × 1GB RAM
- ALB: 1 load balancer + LCU usage
- NAT Gateway: 1 gateway + data transfer
- DynamoDB: Pay-per-request (minimal)
- CloudWatch: Logs and metrics

### Production Environment

**Estimated monthly cost:** ~$200-500 (base) + usage

- ECS Fargate: 2-10 tasks (auto-scaling)
- ALB: Higher LCU usage
- NAT Gateways: 3 (multi-AZ)
- VPC Endpoints: ~$7 each
- DynamoDB: Pay-per-request (scales with usage)
- Container Insights: ~$0.30 per GB ingested

**Cost Optimization Tips:**
1. Use VPC endpoints to avoid NAT gateway charges for AWS API calls
2. Enable DynamoDB auto-scaling for predictable traffic
3. Review CloudWatch log retention (7 days dev, 30 days prod)
4. Use Fargate Spot for non-critical workloads (70% discount)
5. Consider using ARM64 (Graviton2) for 20% cost savings

## Troubleshooting

### Deployment Fails

**Issue:** CDK bootstrap not found
```bash
npx cdk bootstrap aws://ACCOUNT-ID/REGION
```

**Issue:** Docker image build fails
```bash
# Build locally first
docker build -t anthropic-proxy:test .
```

**Issue:** Insufficient IAM permissions
- Check your AWS user/role has required permissions
- Review CloudFormation stack events for specific errors

### Container Fails to Start

**Check logs:**
```bash
aws logs tail /ecs/anthropic-proxy-dev --follow
```

**Common issues:**
- Missing environment variables
- DynamoDB tables not accessible
- Bedrock permissions missing
- Container health check failing

**Debug with ECS Exec (dev only):**
```bash
aws ecs execute-command \
  --cluster anthropic-proxy-dev \
  --task TASK-ID \
  --container anthropic-proxy \
  --interactive \
  --command "/bin/bash"
```

### Health Check Failures

ALB health checks fail if:
- Container port mismatch (should be 8000)
- `/health` endpoint not responding
- Security group blocking traffic
- Container taking too long to start (increase grace period)

### ALB 504 Errors

ALB returns 504 if:
- ECS tasks take too long to respond
- Tasks are unhealthy
- Network connectivity issues

**Solutions:**
- Check ECS task health and logs
- Verify security groups allow traffic between ALB and ECS
- Review container health check configuration
- Increase ALB timeout if needed for long-running requests

## Advanced Configuration

### Custom Domain with HTTPS

1. **Create ACM certificate in your region:**
   ```bash
   aws acm request-certificate \
     --domain-name api.yourdomain.com \
     --validation-method DNS \
     --region us-west-2
   ```

2. **Add HTTPS listener to ALB:**
   ```typescript
   const httpsListener = alb.addListener('HttpsListener', {
     port: 443,
     protocol: ApplicationProtocol.HTTPS,
     certificates: [certificate],
     defaultTargetGroups: [targetGroup],
   });
   ```

3. **Create Route53 record:**
   ```typescript
   new route53.ARecord(this, 'AliasRecord', {
     zone: hostedZone,
     target: route53.RecordTarget.fromAlias(
       new targets.LoadBalancerTarget(alb)
     ),
   });
   ```

### Using Fargate Spot

Save 70% on compute costs:

```typescript
const capacityProviderStrategy = [
  {
    capacityProvider: 'FARGATE_SPOT',
    weight: 1,
    base: 0,
  },
  {
    capacityProvider: 'FARGATE',
    weight: 0,
    base: 1, // Keep at least 1 on-demand task
  },
];
```

**Note:** Spot tasks can be interrupted with 2-minute notice.

### Blue/Green Deployments

Use CodeDeploy for zero-downtime deployments:

```typescript
import * as codedeploy from 'aws-cdk-lib/aws-codedeploy';

const deploymentConfig = new codedeploy.EcsDeploymentConfig(this, 'Config', {
  trafficRouting: codedeploy.TimeBasedLinearTrafficRouting.allAtOnce(),
});
```

## Maintenance

### Update Container Image

1. Build new image
2. Push to ECR
3. CDK will automatically deploy new version

```bash
./scripts/deploy.sh -e prod
```

ECS performs rolling update with circuit breaker.

### Rotate Master API Key

```bash
# Generate new secret value
aws secretsmanager update-secret \
  --secret-id anthropic-proxy-prod-master-api-key \
  --secret-string '{"password": "new-key-value"}'

# Restart ECS tasks to pick up new value
aws ecs update-service \
  --cluster anthropic-proxy-prod \
  --service anthropic-proxy-prod \
  --force-new-deployment
```

### Scale ECS Service

```bash
# Manual scaling
aws ecs update-service \
  --cluster anthropic-proxy-prod \
  --service anthropic-proxy-prod \
  --desired-count 5

# Or update config.ts and redeploy
```

### View DynamoDB Items

```bash
# List API keys
aws dynamodb scan \
  --table-name anthropic-proxy-dev-api-keys \
  --region us-west-2

# Get specific API key
aws dynamodb get-item \
  --table-name anthropic-proxy-dev-api-keys \
  --key '{"api_key": {"S": "sk-..."}}' \
  --region us-west-2
```

## Security Best Practices

1. **Rotate Secrets:** Rotate master API key quarterly
2. **Review IAM Roles:** Use least privilege principle
3. **Enable WAF:** Always enable in production
4. **Monitor Logs:** Set up CloudWatch alarms for errors
5. **Update Dependencies:** Keep CDK and container dependencies up to date
6. **Use Secrets Manager:** Never hardcode credentials
7. **Enable Encryption:** Use AWS-managed keys for DynamoDB, S3
8. **Network Isolation:** ECS tasks in private subnets only

## Support

For issues or questions:
1. Check CloudWatch logs
2. Review CloudFormation events
3. Consult [AWS CDK Documentation](https://docs.aws.amazon.com/cdk/)
4. Review project ARCHITECTURE.md for application details

## License

See main project LICENSE file.
