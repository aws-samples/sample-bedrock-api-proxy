# CloudFront HTTPS Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a CloudFront distribution in front of the existing ALB to provide HTTPS termination using AWS-managed `*.cloudfront.net` certificates.

**Architecture:** CloudFront → ALB (HTTP) → ECS. CloudFront provides HTTPS with managed certs. ALB validates a secret header to reject direct access. Feature is controlled by `enableCloudFront` config (default `true`).

**Tech Stack:** AWS CDK (TypeScript), CloudFront, ALB, ECS (existing)

---

### Task 1: Add CloudFront config options to EnvironmentConfig interface

**Files:**
- Modify: `cdk/config/config.ts:5-114` (EnvironmentConfig interface)

**Step 1: Add CloudFront fields to the EnvironmentConfig interface**

Add after line 113 (before the closing `}`):

```typescript
  // CloudFront Configuration
  enableCloudFront: boolean;            // Enable CloudFront distribution with HTTPS
  cloudFrontOriginReadTimeout: number;  // Origin read timeout in seconds (max 180)
```

**Step 2: Add CloudFront config to dev environment**

Add after `enableContainerInsights: false,` (line 210) in the dev config:

```typescript
    // CloudFront (HTTPS)
    enableCloudFront: true,
    cloudFrontOriginReadTimeout: 180,
```

**Step 3: Add CloudFront config to prod environment**

Add after `enableContainerInsights: true,` (line 310) in the prod config:

```typescript
    // CloudFront (HTTPS)
    enableCloudFront: true,
    cloudFrontOriginReadTimeout: 180,
```

**Step 4: Add env var override in getConfig()**

Add after the `enableOpenaiCompat` override block (around line 398) in `getConfig()`:

```typescript
  // Override CloudFront settings from environment variables
  const enableCloudFront = process.env.ENABLE_CLOUDFRONT
    ? process.env.ENABLE_CLOUDFRONT.toLowerCase() === 'true'
    : config.enableCloudFront;
```

And include it in the return object spread (after `enableOpenaiCompat,`):

```typescript
    enableCloudFront,
```

**Step 5: Commit**

```bash
git add cdk/config/config.ts
git commit -m "feat: add CloudFront HTTPS config options"
```

---

### Task 2: Add CloudFront Distribution to ECS Stack

**Files:**
- Modify: `cdk/lib/ecs-stack.ts`

**Step 1: Add CloudFront imports**

Add to imports at the top of the file:

```typescript
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
```

**Step 2: Add CloudFront distribution property to the class**

Add after `public taskDefinition: ecs.TaskDefinition;` (line 39):

```typescript
  public readonly distribution?: cloudfront.Distribution;
```

**Step 3: Add CloudFront creation block in the constructor**

Add after the admin portal section and before the tags section (after line 325, before `// Apply tags`). This block creates the CloudFront distribution and modifies the ALB default action to require the secret header:

```typescript
    // CloudFront HTTPS (if enabled)
    if (config.enableCloudFront) {
      // Generate a secret value for ALB header validation
      const cloudFrontSecret = new secretsmanager.Secret(this, 'CloudFrontSecret', {
        secretName: `anthropic-proxy-${config.environmentName}-cloudfront-secret`,
        description: 'Secret header value for CloudFront-to-ALB validation',
        generateSecretString: {
          excludePunctuation: true,
          passwordLength: 32,
        },
      });

      // Create Response Headers Policy with HSTS
      const responseHeadersPolicy = new cloudfront.ResponseHeadersPolicy(this, 'ResponseHeadersPolicy', {
        responseHeadersPolicyName: `anthropic-proxy-${config.environmentName}-hsts`,
        securityHeadersBehavior: {
          strictTransportSecurity: {
            accessControlMaxAge: cdk.Duration.seconds(31536000),
            includeSubdomains: true,
            override: true,
          },
        },
      });

      // Create CloudFront Distribution
      this.distribution = new cloudfront.Distribution(this, 'Distribution', {
        comment: `Anthropic Proxy ${config.environmentName} - HTTPS termination`,
        defaultBehavior: {
          origin: new origins.HttpOrigin(this.alb.loadBalancerDnsName, {
            protocolPolicy: cloudfront.OriginProtocolPolicy.HTTP_ONLY,
            readTimeout: cdk.Duration.seconds(config.cloudFrontOriginReadTimeout),
            customHeaders: {
              'X-CloudFront-Secret': cloudFrontSecret.secretValue.unsafeUnwrap(),
            },
          }),
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
          originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
          responseHeadersPolicy,
        },
        enableLogging: false,
        priceClass: cloudfront.PriceClass.PRICE_CLASS_ALL,
      });

      // Modify ALB listener: change default action to reject requests without the secret header
      // Remove the existing default action and replace with a fixed 403 response
      const cfnListener = this.listener.node.defaultChild as elbv2.CfnListener;
      cfnListener.defaultActions = [
        {
          type: 'fixed-response',
          fixedResponseConfig: {
            statusCode: '403',
            contentType: 'text/plain',
            messageBody: 'Forbidden - Direct ALB access is not allowed',
          },
        },
      ];

      // Add a new rule that allows traffic with the correct secret header
      // This applies to the main target group (default API traffic)
      new elbv2.ApplicationListenerRule(this, 'CloudFrontValidationRule', {
        listener: this.listener,
        priority: 100, // Lower priority than admin portal rules (10, 20)
        conditions: [
          elbv2.ListenerCondition.httpHeader('X-CloudFront-Secret', [
            cloudFrontSecret.secretValue.unsafeUnwrap(),
          ]),
        ],
        targetGroups: [targetGroup],
      });

      // Also update admin portal rules to require the secret header
      // (The existing admin portal rules at priority 10 and 20 don't check the header,
      // but since the default action is now 403, we need new rules that check both
      // the path pattern AND the secret header)
      if (config.adminPortalEnabled) {
        // We need to get the admin target group - it was created inside createAdminPortalService
        // Instead, we'll handle this by modifying createAdminPortalService to accept the secret
      }

      // CloudFront Outputs
      new cdk.CfnOutput(this, 'CloudFrontDomainName', {
        value: this.distribution.distributionDomainName,
        description: 'CloudFront Distribution Domain Name',
      });

      new cdk.CfnOutput(this, 'CloudFrontDistributionId', {
        value: this.distribution.distributionId,
        description: 'CloudFront Distribution ID',
      });

      new cdk.CfnOutput(this, 'ProxyURL', {
        value: `https://${this.distribution.distributionDomainName}`,
        description: 'Proxy URL (HTTPS via CloudFront)',
      });

      if (config.adminPortalEnabled) {
        new cdk.CfnOutput(this, 'AdminPortalHTTPSURL', {
          value: `https://${this.distribution.distributionDomainName}/admin/`,
          description: 'Admin Portal URL (HTTPS via CloudFront)',
        });
      }
    }
```

**Important design note on admin portal routes:** The existing admin portal listener rules at priority 10 (`/admin/*`) and 20 (`/api/*`) were created in `createAdminPortalService`. When CloudFront is enabled, the ALB default action becomes 403, but the admin portal rules still have their own target groups — they'll continue to work because they match paths before the default action fires. However, they won't enforce the secret header check.

To enforce the header on admin portal routes too, we need to modify `createAdminPortalService` to add the header condition to its rules when CloudFront is enabled. See Task 3.

**Step 4: Commit**

```bash
git add cdk/lib/ecs-stack.ts
git commit -m "feat: add CloudFront distribution for HTTPS termination"
```

---

### Task 3: Enforce CloudFront secret header on Admin Portal routes

**Files:**
- Modify: `cdk/lib/ecs-stack.ts` — `createAdminPortalService` method

**Step 1: Pass CloudFront secret to createAdminPortalService**

The `createAdminPortalService` method needs to know the CloudFront secret to add it as a condition to listener rules. However, since the CloudFront block runs *after* `createAdminPortalService`, we need to restructure:

**Option chosen:** Move the admin portal listener rule creation out of `createAdminPortalService` and into the constructor, after CloudFront setup. This avoids passing the secret around.

Actually, the simpler approach: Instead of modifying admin portal rules individually, use a **single catch-all rule** at a low priority that checks the header. The admin portal specific path rules (priority 10, 20) already match first.

**Revised approach:** The admin portal rules at priority 10 and 20 match specific paths and forward to the admin target group. The CloudFront validation rule at priority 100 is a catch-all for everything else. The issue is that admin portal rules don't check the header.

**Final approach — reconstruct all rules when CloudFront is enabled:**

Refactor the CloudFront block in the constructor. Instead of creating rules in `createAdminPortalService`, store the admin target group as a class property. Then in the CloudFront block, create all rules (admin + main) with the header condition.

Modify `createAdminPortalService` to:
1. Store `adminTargetGroup` as a class property instead of creating listener rules
2. Return the target group (or store as `this.adminTargetGroup`)

Then in the constructor's CloudFront block, create all listener rules with the header condition.

When CloudFront is disabled, the admin portal rules are created as before (inside `createAdminPortalService`).

**Detailed changes:**

Add class property after `public taskDefinition`:
```typescript
  private adminTargetGroup?: elbv2.ApplicationTargetGroup;
```

In `createAdminPortalService`, make the listener rule creation conditional:

Replace the two `this.listener.addTargetGroups(...)` calls (lines 832-848) with:

```typescript
    // Store admin target group for later use
    this.adminTargetGroup = adminTargetGroup;

    // Only add listener rules here if CloudFront is NOT enabled.
    // When CloudFront is enabled, the constructor adds rules with header validation.
    if (!config.enableCloudFront) {
      // Add path-based routing rule for /admin/*
      this.listener.addTargetGroups('AdminPortalRouting', {
        priority: 10,
        conditions: [
          elbv2.ListenerCondition.pathPatterns(['/admin', '/admin/*']),
        ],
        targetGroups: [adminTargetGroup],
      });

      // Add path-based routing rule for /api/*
      this.listener.addTargetGroups('AdminPortalApiRouting', {
        priority: 20,
        conditions: [
          elbv2.ListenerCondition.pathPatterns(['/api/*']),
        ],
        targetGroups: [adminTargetGroup],
      });
    }
```

Then in the CloudFront block (Task 2), replace the admin portal comment block with actual rules:

```typescript
      // Admin portal routes with header validation
      if (config.adminPortalEnabled && this.adminTargetGroup) {
        new elbv2.ApplicationListenerRule(this, 'AdminPortalCloudFrontRule', {
          listener: this.listener,
          priority: 10,
          conditions: [
            elbv2.ListenerCondition.pathPatterns(['/admin', '/admin/*']),
            elbv2.ListenerCondition.httpHeader('X-CloudFront-Secret', [
              cloudFrontSecret.secretValue.unsafeUnwrap(),
            ]),
          ],
          targetGroups: [this.adminTargetGroup],
        });

        new elbv2.ApplicationListenerRule(this, 'AdminApiCloudFrontRule', {
          listener: this.listener,
          priority: 20,
          conditions: [
            elbv2.ListenerCondition.pathPatterns(['/api/*']),
            elbv2.ListenerCondition.httpHeader('X-CloudFront-Secret', [
              cloudFrontSecret.secretValue.unsafeUnwrap(),
            ]),
          ],
          targetGroups: [this.adminTargetGroup],
        });
      }
```

**Step 2: Commit**

```bash
git add cdk/lib/ecs-stack.ts
git commit -m "feat: enforce CloudFront secret header on all ALB routes"
```

---

### Task 4: Update deploy script to output CloudFront URL

**Files:**
- Modify: `cdk/scripts/deploy.sh`

**Step 1: Add CloudFront URL retrieval after deployment**

After the `ADMIN_PORTAL_URL` retrieval (line 332), add:

```bash
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
```

**Step 2: Update the Access URLs output section**

Replace lines 348-352:

```bash
    echo -e "${GREEN}Access URLs:${NC}"
    echo -e "  API Proxy: http://${ALB_DNS}"
    if [[ "$ADMIN_PORTAL_URL" != "N/A" ]]; then
        echo -e "  Admin Portal: ${ADMIN_PORTAL_URL}"
    fi
```

With:

```bash
    echo -e "${GREEN}Access URLs:${NC}"
    if [[ -n "$CLOUDFRONT_URL" && "$CLOUDFRONT_URL" != "None" ]]; then
        echo -e "  API Proxy (HTTPS): ${CLOUDFRONT_URL}"
        if [[ "$ADMIN_PORTAL_URL" != "N/A" ]]; then
            echo -e "  Admin Portal (HTTPS): https://${CLOUDFRONT_DOMAIN}/admin/"
        fi
        echo -e "  ALB (internal HTTP): http://${ALB_DNS}"
    else
        echo -e "  API Proxy: http://${ALB_DNS}"
        if [[ "$ADMIN_PORTAL_URL" != "N/A" ]]; then
            echo -e "  Admin Portal: ${ADMIN_PORTAL_URL}"
        fi
    fi
```

**Step 3: Update Next Steps section**

Replace line 374:

```bash
    echo -e "  2. Test the health endpoint: curl http://${ALB_DNS}/health"
```

With:

```bash
    if [[ -n "$CLOUDFRONT_URL" && "$CLOUDFRONT_URL" != "None" ]]; then
        echo -e "  2. Test the health endpoint: curl ${CLOUDFRONT_URL}/health"
    else
        echo -e "  2. Test the health endpoint: curl http://${ALB_DNS}/health"
    fi
```

**Step 4: Commit**

```bash
git add cdk/scripts/deploy.sh
git commit -m "feat: update deploy script to output CloudFront HTTPS URLs"
```

---

### Task 5: Update AdminPortalURL output to be conditional

**Files:**
- Modify: `cdk/lib/ecs-stack.ts` — `createAdminPortalService` method

**Step 1: Make the AdminPortalURL output conditional on CloudFront**

Replace lines 895-897:

```typescript
    new cdk.CfnOutput(this, 'AdminPortalURL', {
      value: `http://${this.alb.loadBalancerDnsName}/admin/`,
      description: 'Admin Portal URL',
    });
```

With:

```typescript
    // Only output HTTP admin URL when CloudFront is not enabled
    // (CloudFront outputs its own HTTPS admin URL in the constructor)
    if (!config.enableCloudFront) {
      new cdk.CfnOutput(this, 'AdminPortalURL', {
        value: `http://${this.alb.loadBalancerDnsName}/admin/`,
        description: 'Admin Portal URL',
      });
    }
```

**Step 2: Commit**

```bash
git add cdk/lib/ecs-stack.ts
git commit -m "feat: conditional admin portal URL output based on CloudFront"
```

---

### Task 6: Verify with CDK synth

**Step 1: Build the CDK project**

```bash
cd cdk && npm run build
```

Expected: No TypeScript errors.

**Step 2: Run CDK synth for dev environment**

```bash
CDK_PLATFORM=arm64 CDK_LAUNCH_TYPE=fargate CDK_ENVIRONMENT=dev npx cdk synth -c environment=dev --quiet
```

Expected: Successful synthesis with no errors.

**Step 3: Run CDK synth with CloudFront disabled**

```bash
ENABLE_CLOUDFRONT=false CDK_PLATFORM=arm64 CDK_LAUNCH_TYPE=fargate CDK_ENVIRONMENT=dev npx cdk synth -c environment=dev --quiet
```

Expected: Successful synthesis, no CloudFront resources in output.

**Step 4: Commit any fixes if needed, then final commit**

```bash
git add -A
git commit -m "chore: verify CDK synth passes with CloudFront HTTPS"
```
