# CloudFront HTTPS Encryption

The proxy includes a built-in CloudFront distribution that provides HTTPS encryption for all API traffic. It uses AWS-managed `*.cloudfront.net` certificates — **no custom domain or ACM certificate required**.

## Architecture

```
Client (Anthropic SDK)
    │
    ▼ HTTPS (443)
CloudFront (*.cloudfront.net)
    │  - AWS-managed TLS certificate
    │  - Attaches X-CloudFront-Secret header
    │  - HSTS security response header
    │
    ▼ HTTP (80) over a CloudFront VPC origin — AWS network only, never the public internet
Internal ALB (private subnets, no internet-facing endpoint)
    │  - Security group: CloudFront origin-facing prefix list only
    │  - Validates X-CloudFront-Secret (defense in depth)
    │  - Rejects requests without it (returns 403)
    │
    ▼ HTTP (8000)
ECS Tasks (private subnets)
```

TLS terminates at CloudFront. The CloudFront → ALB hop uses HTTP, but over a [CloudFront VPC origin](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-vpc-origins.html): traffic stays on the AWS network and the ALB has no public endpoint. If your compliance requirements demand TLS on that hop as well, add an HTTPS listener with an ACM certificate for a domain you own and set the origin protocol policy to HTTPS-only.

## Enabling CloudFront

CloudFront is **enabled by default for `prod`** and disabled for `dev`. Without CloudFront the ALB is internet-facing and serves **plain HTTP — use that mode for short-lived testing only and never send real API keys over it**. Enable CloudFront explicitly via environment variable:

```bash
# Enable CloudFront HTTPS distribution
ENABLE_CLOUDFRONT=true ./scripts/deploy.sh -e prod -r us-west-2 -p arm64

# Deployment output
# Access URLs:
#   API Proxy (HTTPS): https://d1234567890.cloudfront.net
#   Admin Portal (HTTPS): https://d1234567890.cloudfront.net/admin/
```

## Client Configuration

With CloudFront enabled, update `ANTHROPIC_BASE_URL` to the HTTPS URL:

```bash
export CLAUDE_CODE_USE_BEDROCK=0
export ANTHROPIC_BASE_URL=https://d1234567890.cloudfront.net
export ANTHROPIC_API_KEY=sk-xxxx
```

## Security Mechanisms

| Mechanism | Description |
|-----------|-------------|
| **HTTPS Encryption** | TLS from the client to CloudFront, protecting API keys and request data on the public internet. The CloudFront → ALB hop is HTTP over a private VPC origin (see Architecture) |
| **Private ALB** | The ALB is internal (private subnets); its security group admits only the CloudFront origin-facing managed prefix list |
| **ALB Access Control** | ALB only accepts requests with the `X-CloudFront-Secret` header, rejects anything else (defense in depth) |
| **HSTS** | Forces browsers to use HTTPS (`Strict-Transport-Security: max-age=31536000`) |
| **Auto-Generated Secret** | Secrets Manager automatically generates a 32-character random validation key |

## Streaming vs Non-Streaming Considerations

| Mode | CloudFront Behavior | Recommendation |
|------|---------------------|----------------|
| **Streaming** (`"stream": true`) | CloudFront natively supports SSE, forwards in real-time. Timeout only affects time-to-first-byte (`message_start` typically arrives within seconds) | **Recommended** |
| **Non-streaming** | Timeout covers the entire response generation time. Default 60 seconds, returns 504 on timeout | Switch to streaming for long responses |

> **Tip**: To support non-streaming requests longer than 60 seconds, request a CloudFront Origin Read Timeout quota increase (up to 180 seconds) via the AWS Support Console.

## Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `enableCloudFront` | boolean | `true` (prod), `false` (dev) | Enable CloudFront HTTPS + private ALB via VPC origin. `false` = public plain-HTTP ALB, testing only |
| `cloudFrontOriginReadTimeout` | number | `60` | Origin read timeout (seconds), default max 60s, up to 180s with quota increase |

## Disabling CloudFront

Set `enableCloudFront: false` (or `ENABLE_CLOUDFRONT=false`) and redeploy to fall back to HTTP-only direct ALB access.
