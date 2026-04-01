import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as elbv2 from 'aws-cdk-lib/aws-elasticloadbalancingv2';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as autoscaling from 'aws-cdk-lib/aws-autoscaling';
import { Platform } from 'aws-cdk-lib/aws-ecr-assets';
import { Construct } from 'constructs';
import { EnvironmentConfig } from '../config/config';
import * as path from 'path';

export interface ECSStackProps extends cdk.StackProps {
  config: EnvironmentConfig;
  vpc: ec2.Vpc;
  albSecurityGroup: ec2.SecurityGroup;
  ecsSecurityGroup: ec2.SecurityGroup;
  apiKeysTable: dynamodb.Table;
  usageTable: dynamodb.Table;
  modelMappingTable: dynamodb.Table;
  usageStatsTable: dynamodb.Table;
  modelPricingTable: dynamodb.Table;
  providerKeysTable: dynamodb.Table;
  routingRulesTable: dynamodb.Table;
  failoverChainsTable: dynamodb.Table;
  smartRoutingConfigTable: dynamodb.Table;
  providersTable: dynamodb.Table;
  // Cognito (optional - for admin portal)
  cognitoUserPoolId?: string;
  cognitoClientId?: string;
}

export class ECSStack extends cdk.Stack {
  public readonly cluster: ecs.Cluster;
  public service: ecs.BaseService;
  public readonly alb: elbv2.ApplicationLoadBalancer;
  public readonly listener: elbv2.ApplicationListener;
  public taskDefinition: ecs.TaskDefinition;
  public distribution?: cloudfront.Distribution;
  private adminTargetGroup?: elbv2.ApplicationTargetGroup;

  constructor(scope: Construct, id: string, props: ECSStackProps) {
    super(scope, id, props);

    const { config, vpc, albSecurityGroup, ecsSecurityGroup } = props;
    const { apiKeysTable, usageTable, modelMappingTable, usageStatsTable, modelPricingTable } = props;
    const { providerKeysTable, routingRulesTable, failoverChainsTable, smartRoutingConfigTable, providersTable } = props;
    const { cognitoUserPoolId, cognitoClientId } = props;

    // Create ECS Cluster
    this.cluster = new ecs.Cluster(this, 'Cluster', {
      clusterName: `anthropic-proxy-${config.environmentName}`,
      vpc,
    });

    // Enable Container Insights at CloudFormation level (avoiding deprecated L2 property)
    if (config.enableContainerInsights) {
      const cfnCluster = this.cluster.node.defaultChild as ecs.CfnCluster;
      cfnCluster.clusterSettings = [{
        name: 'containerInsights',
        value: 'enabled',
      }];
    }

    // Create ALB
    this.alb = new elbv2.ApplicationLoadBalancer(this, 'ALB', {
      loadBalancerName: `anthropic-proxy-${config.environmentName}-alb`,
      vpc,
      internetFacing: true,
      securityGroup: albSecurityGroup,
      vpcSubnets: {
        subnetType: ec2.SubnetType.PUBLIC,
      },
      deletionProtection: false,
      idleTimeout: cdk.Duration.seconds(600),
    });

    // Create Target Group - target type depends on launch type
    const targetType = config.launchType === 'ec2'
      ? elbv2.TargetType.INSTANCE
      : elbv2.TargetType.IP;

    const targetGroup = new elbv2.ApplicationTargetGroup(this, 'TargetGroup', {
      vpc,
      port: config.containerPort,
      protocol: elbv2.ApplicationProtocol.HTTP,
      targetType,
      healthCheck: {
        path: config.healthCheckPath,
        interval: cdk.Duration.seconds(config.healthCheckInterval),
        timeout: cdk.Duration.seconds(config.healthCheckTimeout),
        healthyThresholdCount: config.healthCheckHealthyThreshold,
        unhealthyThresholdCount: 5,
        healthyHttpCodes: '200',
      },
      deregistrationDelay: cdk.Duration.seconds(30),
      // Enable sticky sessions for PTC (Programmatic Tool Calling)
      // This ensures continuation requests go to the same instance that created the session
      stickinessCookieDuration: cdk.Duration.seconds(300), // 5 minutes - matches PTC session timeout
    });

    // Create HTTP Listener
    this.listener = this.alb.addListener('HTTPListener', {
      port: 80,
      protocol: elbv2.ApplicationProtocol.HTTP,
      defaultTargetGroups: [targetGroup],
    });

    // Create CloudWatch Log Group
    const logGroup = new logs.LogGroup(this, 'LogGroup', {
      logGroupName: `/ecs/anthropic-proxy-${config.environmentName}`,
      retention: config.logRetentionDays as logs.RetentionDays,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // Create Task Execution Role
    // Note: no explicit roleName — CDK generates a stable, unique name from the construct path.
    // Explicit names with random suffixes cause replacement on every deploy and orphan old roles.
    const taskExecutionRole = new iam.Role(this, 'TaskExecutionRole', {
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AmazonECSTaskExecutionRolePolicy'),
      ],
    });

    // Create Task Role
    const taskRole = new iam.Role(this, 'TaskRole', {
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
    });

    // Grant DynamoDB permissions
    apiKeysTable.grantReadWriteData(taskRole);
    usageTable.grantReadWriteData(taskRole);
    modelMappingTable.grantReadWriteData(taskRole);
    providerKeysTable.grantReadWriteData(taskRole);
    routingRulesTable.grantReadWriteData(taskRole);
    failoverChainsTable.grantReadWriteData(taskRole);
    smartRoutingConfigTable.grantReadWriteData(taskRole);
    providersTable.grantReadWriteData(taskRole);

    // Grant Bedrock permissions
    taskRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          'bedrock:InvokeModel',
          'bedrock:InvokeModelWithResponseStream',
          'bedrock:ListFoundationModels',
        ],
        resources: ['*'],
      })
    );

    // Grant CloudWatch Logs permissions
    taskRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents'],
        resources: [logGroup.logGroupArn],
      })
    );

    // Grant AWS Marketplace permissions (subscribe and view subscriptions)
    taskRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          'aws-marketplace:Subscribe',
          'aws-marketplace:Unsubscribe',
          'aws-marketplace:ViewSubscriptions',
          'aws-marketplace:GetEntitlements',
          'aws-marketplace:ListEntitlements',
        ],
        resources: ['*'],
      })
    );

    // Create Secret for Master API Key
    const masterApiKeySecret = new secretsmanager.Secret(this, 'MasterAPIKeySecret', {
      secretName: `anthropic-proxy-${config.environmentName}-master-api-key`,
      description: 'Master API key for Anthropic proxy',
      generateSecretString: {
        secretStringTemplate: JSON.stringify({ username: 'master' }),
        generateStringKey: 'password',
        excludePunctuation: true,
        passwordLength: 32,
      },
    });

    // Grant read access to secret
    masterApiKeySecret.grantRead(taskRole);

    // Map platform string to ECS CpuArchitecture
    const cpuArchitecture = config.platform === 'arm64'
      ? ecs.CpuArchitecture.ARM64
      : ecs.CpuArchitecture.X86_64;

    // Map platform string to Docker Platform
    const dockerPlatform = config.platform === 'arm64'
      ? Platform.LINUX_ARM64
      : Platform.LINUX_AMD64;

    // Common environment variables
    const environmentVars: { [key: string]: string } = {
      // AWS Configuration
      AWS_REGION: config.region,
      AWS_DEFAULT_REGION: config.region,

      // Environment
      ENVIRONMENT: config.environmentName === 'prod' ? 'production' : 'development',
      LOG_LEVEL: config.environmentName === 'prod' ? 'INFO' : 'DEBUG',

      // DynamoDB Tables
      DYNAMODB_API_KEYS_TABLE: apiKeysTable.tableName,
      DYNAMODB_USAGE_TABLE: usageTable.tableName,
      DYNAMODB_MODEL_MAPPING_TABLE: modelMappingTable.tableName,
      // Multi-Provider Gateway Tables
      DYNAMODB_PROVIDER_KEYS_TABLE: providerKeysTable.tableName,
      DYNAMODB_ROUTING_RULES_TABLE: routingRulesTable.tableName,
      DYNAMODB_FAILOVER_CHAINS_TABLE: failoverChainsTable.tableName,
      DYNAMODB_SMART_ROUTING_CONFIG_TABLE: smartRoutingConfigTable.tableName,
      DYNAMODB_PROVIDERS_TABLE: providersTable.tableName,

      // Authentication
      API_KEY_HEADER: 'x-api-key',
      REQUIRE_API_KEY: config.requireApiKey.toString(),

      // Rate Limiting
      RATE_LIMIT_ENABLED: config.rateLimitEnabled.toString(),
      RATE_LIMIT_REQUESTS: config.rateLimitRequests.toString(),
      RATE_LIMIT_WINDOW: config.rateLimitWindow.toString(),

      // Features
      ENABLE_TOOL_USE: 'True',
      ENABLE_EXTENDED_THINKING: 'True',
      ENABLE_DOCUMENT_SUPPORT: 'True',
      PROMPT_CACHING_ENABLED: 'True',
      FINE_GRAINED_TOOL_STREAMING_ENABLED: 'True',
      INTERLEAVED_THINKING_ENABLED: 'True',

      // PTC (Programmatic Tool Calling)
      ENABLE_PROGRAMMATIC_TOOL_CALLING: config.enablePtc.toString(),
      PTC_SANDBOX_IMAGE: config.ptcSandboxImage,
      PTC_SESSION_TIMEOUT: config.ptcSessionTimeout.toString(),
      PTC_EXECUTION_TIMEOUT: config.ptcExecutionTimeout.toString(),
      PTC_MEMORY_LIMIT: config.ptcMemoryLimit,

      // Metrics
      ENABLE_METRICS: config.enableMetrics.toString(),

      // OpenTelemetry Tracing
      ENABLE_TRACING: config.enableTracing.toString(),
      ...(config.otelExporterEndpoint && { OTEL_EXPORTER_OTLP_ENDPOINT: config.otelExporterEndpoint }),
      ...(config.otelExporterProtocol && { OTEL_EXPORTER_OTLP_PROTOCOL: config.otelExporterProtocol }),
      ...(config.otelExporterHeaders && { OTEL_EXPORTER_OTLP_HEADERS: config.otelExporterHeaders }),
      ...(config.otelServiceName && { OTEL_SERVICE_NAME: config.otelServiceName }),
      ...(config.otelTraceContent !== undefined && { OTEL_TRACE_CONTENT: config.otelTraceContent.toString() }),
      ...(config.otelTraceSamplingRatio !== undefined && { OTEL_TRACE_SAMPLING_RATIO: config.otelTraceSamplingRatio.toString() }),

      // Web Search
      ENABLE_WEB_SEARCH: config.enableWebSearch.toString(),
      ...(config.webSearchProvider && { WEB_SEARCH_PROVIDER: config.webSearchProvider }),
      ...(config.webSearchApiKey && { WEB_SEARCH_API_KEY: config.webSearchApiKey }),
      ...(config.webSearchMaxResults && { WEB_SEARCH_MAX_RESULTS: config.webSearchMaxResults.toString() }),
      ...(config.webSearchDefaultMaxUses && { WEB_SEARCH_DEFAULT_MAX_USES: config.webSearchDefaultMaxUses.toString() }),

      // Web Fetch
      ENABLE_WEB_FETCH: config.enableWebFetch.toString(),
      ...(config.webFetchDefaultMaxUses && { WEB_FETCH_DEFAULT_MAX_USES: config.webFetchDefaultMaxUses.toString() }),
      ...(config.webFetchDefaultMaxContentTokens && { WEB_FETCH_DEFAULT_MAX_CONTENT_TOKENS: config.webFetchDefaultMaxContentTokens.toString() }),

      // Cache TTL
      ...(config.defaultCacheTtl && { DEFAULT_CACHE_TTL: config.defaultCacheTtl }),
      ...(config.stripCacheScope !== undefined && { STRIP_CACHE_SCOPE: config.stripCacheScope.toString() }),

      // OpenAI-Compatible API (Bedrock Mantle)
      ENABLE_OPENAI_COMPAT: config.enableOpenaiCompat.toString(),
      ...(config.openaiBaseUrl && { OPENAI_BASE_URL: config.openaiBaseUrl }),
      ...(process.env.OPENAI_API_KEY && { OPENAI_API_KEY: process.env.OPENAI_API_KEY }),

      // Streaming
      STREAMING_TIMEOUT: '300',

      // Bedrock Concurrency
      BEDROCK_THREAD_POOL_SIZE: config.bedrockThreadPoolSize.toString(),
      BEDROCK_SEMAPHORE_SIZE: config.bedrockSemaphoreSize.toString(),
    };

    // Create service based on launch type
    if (config.launchType === 'ec2') {
      // ========== EC2 Launch Type ==========
      this.createEc2Service(
        config, vpc, ecsSecurityGroup, targetGroup, logGroup,
        taskExecutionRole, taskRole, masterApiKeySecret,
        cpuArchitecture, dockerPlatform, environmentVars
      );
    } else {
      // ========== Fargate Launch Type (Default) ==========
      this.createFargateService(
        config, ecsSecurityGroup, targetGroup, logGroup,
        taskExecutionRole, taskRole, masterApiKeySecret,
        cpuArchitecture, dockerPlatform, environmentVars
      );
    }

    // Create Admin Portal Service (if enabled)
    if (config.adminPortalEnabled) {
      this.createAdminPortalService(
        config, vpc, ecsSecurityGroup, logGroup,
        taskExecutionRole, taskRole, cpuArchitecture, dockerPlatform,
        {
          apiKeysTable,
          usageTable,
          modelMappingTable,
          usageStatsTable,
          modelPricingTable,
          providerKeysTable,
          routingRulesTable,
          failoverChainsTable,
          smartRoutingConfigTable,
          providersTable,
        },
        cognitoUserPoolId,
        cognitoClientId
      );
    }

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
        removalPolicy: config.environmentName === 'prod'
          ? cdk.RemovalPolicy.RETAIN
          : cdk.RemovalPolicy.DESTROY,
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
      // NOTE: readTimeout (default 60s, max 180s with quota increase) affects:
      //   - Streaming: only time-to-first-byte (message_start arrives quickly, so 60s is fine)
      //   - Non-streaming: entire response generation time (may 504 if Bedrock takes >60s)
      //   Recommend streaming mode when using CloudFront. For non-streaming with long
      //   responses, request AWS quota increase via Support Console.
      const distribution = new cloudfront.Distribution(this, 'Distribution', {
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
        priceClass: cloudfront.PriceClass.PRICE_CLASS_ALL,
      });

      this.distribution = distribution;

      // Modify ALB listener: change default action to reject requests without the secret header
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

      // Add rule that allows traffic with the correct secret header (main API)
      new elbv2.ApplicationListenerRule(this, 'CloudFrontValidationRule', {
        listener: this.listener,
        priority: 100,
        conditions: [
          elbv2.ListenerCondition.httpHeader('X-CloudFront-Secret', [
            cloudFrontSecret.secretValue.unsafeUnwrap(),
          ]),
        ],
        targetGroups: [targetGroup],
      });

      // Admin portal routes with header validation
      // NOTE: Priorities 15/25 (not 10/20) to avoid conflicts during CloudFormation updates
      // when transitioning from non-CloudFront rules (priority 10/20) to CloudFront rules.
      // CloudFormation creates new resources before deleting old ones, so overlapping priorities fail.
      if (config.adminPortalEnabled && this.adminTargetGroup) {
        new elbv2.ApplicationListenerRule(this, 'AdminPortalCloudFrontRule', {
          listener: this.listener,
          priority: 15,
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
          priority: 25,
          conditions: [
            elbv2.ListenerCondition.pathPatterns(['/api/*']),
            elbv2.ListenerCondition.httpHeader('X-CloudFront-Secret', [
              cloudFrontSecret.secretValue.unsafeUnwrap(),
            ]),
          ],
          targetGroups: [this.adminTargetGroup],
        });
      }

      // CloudFront Outputs
      new cdk.CfnOutput(this, 'CloudFrontDomainName', {
        value: distribution.distributionDomainName,
        description: 'CloudFront Distribution Domain Name',
      });

      new cdk.CfnOutput(this, 'CloudFrontDistributionId', {
        value: distribution.distributionId,
        description: 'CloudFront Distribution ID',
      });

      new cdk.CfnOutput(this, 'ProxyURL', {
        value: `https://${distribution.distributionDomainName}`,
        description: 'Proxy URL (HTTPS via CloudFront)',
      });

      if (config.adminPortalEnabled) {
        new cdk.CfnOutput(this, 'AdminPortalHTTPSURL', {
          value: `https://${distribution.distributionDomainName}/admin/`,
          description: 'Admin Portal URL (HTTPS via CloudFront)',
        });
      }
    }

    // Apply tags
    cdk.Tags.of(this.cluster).add('Environment', config.environmentName);
    cdk.Tags.of(this.cluster).add('LaunchType', config.launchType);
    Object.entries(config.tags).forEach(([key, value]) => {
      cdk.Tags.of(this.cluster).add(key, value);
    });

    // Outputs
    new cdk.CfnOutput(this, 'ClusterName', {
      value: this.cluster.clusterName,
      description: 'ECS Cluster Name',
    });

    new cdk.CfnOutput(this, 'ServiceName', {
      value: this.service.serviceName,
      description: 'ECS Service Name',
    });

    new cdk.CfnOutput(this, 'ALBDNSName', {
      value: this.alb.loadBalancerDnsName,
      description: 'ALB DNS Name',
    });

    new cdk.CfnOutput(this, 'ALBARN', {
      value: this.alb.loadBalancerArn,
      description: 'ALB ARN',
    });

    new cdk.CfnOutput(this, 'MasterAPIKeySecretName', {
      value: masterApiKeySecret.secretName,
      description: 'Master API Key Secret Name',
    });

    new cdk.CfnOutput(this, 'LaunchType', {
      value: config.launchType.toUpperCase(),
      description: 'ECS Launch Type',
    });

    new cdk.CfnOutput(this, 'PTCEnabled', {
      value: config.enablePtc.toString(),
      description: 'PTC (Programmatic Tool Calling) Enabled',
    });
  }

  /**
   * Create Fargate-based ECS service (default, no PTC support)
   */
  private createFargateService(
    config: EnvironmentConfig,
    ecsSecurityGroup: ec2.SecurityGroup,
    targetGroup: elbv2.ApplicationTargetGroup,
    logGroup: logs.LogGroup,
    taskExecutionRole: iam.Role,
    taskRole: iam.Role,
    masterApiKeySecret: secretsmanager.Secret,
    cpuArchitecture: ecs.CpuArchitecture,
    dockerPlatform: Platform,
    environmentVars: { [key: string]: string }
  ): void {
    // Create Fargate Task Definition
    const taskDefinition = new ecs.FargateTaskDefinition(this, 'TaskDefinition', {
      family: `anthropic-proxy-${config.environmentName}`,
      cpu: config.ecsCpu,
      memoryLimitMiB: config.ecsMemory,
      executionRole: taskExecutionRole,
      taskRole: taskRole,
      runtimePlatform: {
        cpuArchitecture,
        operatingSystemFamily: ecs.OperatingSystemFamily.LINUX,
      },
    });

    // Add Container
    taskDefinition.addContainer('app', {
      containerName: 'anthropic-proxy',
      image: ecs.ContainerImage.fromAsset(path.join(__dirname, '../../'), {
        file: 'Dockerfile',
        exclude: ['cdk/cdk.out', 'cdk/node_modules', 'cdk/.git'],
        platform: dockerPlatform,
      }),
      logging: ecs.LogDriver.awsLogs({
        streamPrefix: 'anthropic-proxy',
        logGroup,
      }),
      environment: environmentVars,
      secrets: {
        MASTER_API_KEY: ecs.Secret.fromSecretsManager(masterApiKeySecret, 'password'),
      },
      portMappings: [
        {
          containerPort: config.containerPort,
          protocol: ecs.Protocol.TCP,
        },
      ],
      healthCheck: {
        command: [
          'CMD-SHELL',
          `curl -f http://localhost:${config.containerPort}/health || exit 1`,
        ],
        interval: cdk.Duration.seconds(30),
        timeout: cdk.Duration.seconds(5),
        retries: 3,
        startPeriod: cdk.Duration.seconds(90),
      },
    });

    this.taskDefinition = taskDefinition;

    // Create Fargate Service
    const service = new ecs.FargateService(this, 'Service', {
      cluster: this.cluster,
      taskDefinition,
      desiredCount: config.ecsDesiredCount,
      assignPublicIp: false,
      securityGroups: [ecsSecurityGroup],
      vpcSubnets: {
        subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
      },
      healthCheckGracePeriod: cdk.Duration.seconds(300),
      circuitBreaker: {
        rollback: true,
      },
      enableExecuteCommand: config.environmentName !== 'prod',
      minHealthyPercent: 100,
      maxHealthyPercent: 200,
    });

    // Attach to Target Group
    service.attachToApplicationTargetGroup(targetGroup);

    // Auto Scaling
    const scaling = service.autoScaleTaskCount({
      minCapacity: config.ecsMinCapacity,
      maxCapacity: config.ecsMaxCapacity,
    });

    scaling.scaleOnCpuUtilization('CpuScaling', {
      targetUtilizationPercent: config.ecsTargetCpuUtilization,
      scaleInCooldown: cdk.Duration.seconds(60),
      scaleOutCooldown: cdk.Duration.seconds(60),
    });

    scaling.scaleOnMemoryUtilization('MemoryScaling', {
      targetUtilizationPercent: 70,
      scaleInCooldown: cdk.Duration.seconds(60),
      scaleOutCooldown: cdk.Duration.seconds(60),
    });

    scaling.scaleOnRequestCount('RequestCountScaling', {
      requestsPerTarget: 1000,
      targetGroup,
      scaleInCooldown: cdk.Duration.seconds(60),
      scaleOutCooldown: cdk.Duration.seconds(60),
    });

    this.service = service;
  }

  /**
   * Create EC2-based ECS service (supports PTC with Docker socket)
   */
  private createEc2Service(
    config: EnvironmentConfig,
    vpc: ec2.Vpc,
    ecsSecurityGroup: ec2.SecurityGroup,
    targetGroup: elbv2.ApplicationTargetGroup,
    logGroup: logs.LogGroup,
    taskExecutionRole: iam.Role,
    taskRole: iam.Role,
    masterApiKeySecret: secretsmanager.Secret,
    cpuArchitecture: ecs.CpuArchitecture,
    dockerPlatform: Platform,
    environmentVars: { [key: string]: string }
  ): void {
    // Get the appropriate ECS-optimized AMI based on platform
    const machineImage = config.platform === 'arm64'
      ? ecs.EcsOptimizedImage.amazonLinux2(ecs.AmiHardwareType.ARM)
      : ecs.EcsOptimizedImage.amazonLinux2(ecs.AmiHardwareType.STANDARD);

    // Create Launch Template for EC2 instances
    const launchTemplate = new ec2.LaunchTemplate(this, 'LaunchTemplate', {
      launchTemplateName: `anthropic-proxy-${config.environmentName}-lt`,
      machineImage,
      instanceType: new ec2.InstanceType(config.ec2InstanceType),
      securityGroup: ecsSecurityGroup,
      blockDevices: [
        {
          deviceName: '/dev/xvda',
          volume: ec2.BlockDeviceVolume.ebs(config.ec2RootVolumeSize, {
            volumeType: ec2.EbsDeviceVolumeType.GP3,
            encrypted: true,
          }),
        },
      ],
      userData: ec2.UserData.forLinux(),
      role: new iam.Role(this, 'EC2InstanceRole', {
        assumedBy: new iam.ServicePrincipal('ec2.amazonaws.com'),
        managedPolicies: [
          iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AmazonEC2ContainerServiceforEC2Role'),
          iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonSSMManagedInstanceCore'),
        ],
      }),
    });

    // Add user data to configure ECS agent
    launchTemplate.userData!.addCommands(
      '#!/bin/bash',
      `echo ECS_CLUSTER=${this.cluster.clusterName} >> /etc/ecs/ecs.config`,
      'echo ECS_ENABLE_CONTAINER_METADATA=true >> /etc/ecs/ecs.config',
      // Ensure Docker socket has correct permissions for PTC
      'chmod 666 /var/run/docker.sock',
    );

    // Create Auto Scaling Group
    const autoScalingGroup = new autoscaling.AutoScalingGroup(this, 'ASG', {
      autoScalingGroupName: `anthropic-proxy-${config.environmentName}-asg`,
      vpc,
      launchTemplate,
      minCapacity: config.ecsMinCapacity,
      maxCapacity: config.ecsMaxCapacity,
      desiredCapacity: config.ecsDesiredCount,
      vpcSubnets: {
        subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
      },
      healthChecks: autoscaling.HealthChecks.ec2({
        gracePeriod: cdk.Duration.seconds(300),
      }),
      updatePolicy: autoscaling.UpdatePolicy.rollingUpdate({
        maxBatchSize: 1,
        minInstancesInService: config.ecsMinCapacity,
        pauseTime: cdk.Duration.minutes(5),
      }),
      newInstancesProtectedFromScaleIn: false,
    });

    // Use Spot instances if configured (for cost savings in dev)
    if (config.ec2UseSpot) {
      const cfnLaunchTemplate = launchTemplate.node.defaultChild as ec2.CfnLaunchTemplate;
      cfnLaunchTemplate.addPropertyOverride('LaunchTemplateData.InstanceMarketOptions', {
        MarketType: 'spot',
        SpotOptions: {
          SpotInstanceType: 'one-time',
          ...(config.ec2SpotMaxPrice && { MaxPrice: config.ec2SpotMaxPrice }),
        },
      });
    }

    // Add capacity provider
    const capacityProvider = new ecs.AsgCapacityProvider(this, 'AsgCapacityProvider', {
      capacityProviderName: `anthropic-proxy-${config.environmentName}-cp`,
      autoScalingGroup,
      enableManagedScaling: true,
      enableManagedTerminationProtection: false,
      targetCapacityPercent: 100,
    });

    this.cluster.addAsgCapacityProvider(capacityProvider);

    // Create EC2 Task Definition
    const taskDefinition = new ecs.Ec2TaskDefinition(this, 'TaskDefinition', {
      family: `anthropic-proxy-${config.environmentName}`,
      executionRole: taskExecutionRole,
      taskRole: taskRole,
      networkMode: ecs.NetworkMode.BRIDGE,
    });

    // Add Docker socket volume for PTC support
    if (config.ec2EnableDockerSocket) {
      taskDefinition.addVolume({
        name: 'docker-socket',
        host: {
          sourcePath: '/var/run/docker.sock',
        },
      });
    }

    // Add Container
    const container = taskDefinition.addContainer('app', {
      containerName: 'anthropic-proxy',
      image: ecs.ContainerImage.fromAsset(path.join(__dirname, '../../'), {
        file: 'Dockerfile',
        exclude: ['cdk/cdk.out', 'cdk/node_modules', 'cdk/.git'],
        platform: dockerPlatform,
      }),
      logging: ecs.LogDriver.awsLogs({
        streamPrefix: 'anthropic-proxy',
        logGroup,
      }),
      environment: environmentVars,
      secrets: {
        MASTER_API_KEY: ecs.Secret.fromSecretsManager(masterApiKeySecret, 'password'),
      },
      memoryReservationMiB: config.ecsMemory,
      cpu: config.ecsCpu,
      portMappings: [
        {
          containerPort: config.containerPort,
          hostPort: 0, // Dynamic port mapping for bridge mode
          protocol: ecs.Protocol.TCP,
        },
      ],
      healthCheck: {
        command: [
          'CMD-SHELL',
          `curl -f http://localhost:${config.containerPort}/health || exit 1`,
        ],
        interval: cdk.Duration.seconds(30),
        timeout: cdk.Duration.seconds(5),
        retries: 3,
        startPeriod: cdk.Duration.seconds(90),
      },
    });

    // Mount Docker socket for PTC
    if (config.ec2EnableDockerSocket) {
      container.addMountPoints({
        containerPath: '/var/run/docker.sock',
        sourceVolume: 'docker-socket',
        readOnly: false,
      });
    }

    this.taskDefinition = taskDefinition;

    // Create EC2 Service
    // Note: In bridge networking mode, security groups are applied at EC2 instance level (via ASG),
    // not at the service level. Do not specify securityGroups, vpcSubnets, or assignPublicIp here.
    const service = new ecs.Ec2Service(this, 'Service', {
      cluster: this.cluster,
      taskDefinition,
      desiredCount: config.ecsDesiredCount,
      healthCheckGracePeriod: cdk.Duration.seconds(300),
      circuitBreaker: {
        rollback: true,
      },
      enableExecuteCommand: config.environmentName !== 'prod',
      minHealthyPercent: 100,
      maxHealthyPercent: 200,
      capacityProviderStrategies: [
        {
          capacityProvider: capacityProvider.capacityProviderName,
          weight: 1,
        },
      ],
    });

    // Attach to Target Group
    service.attachToApplicationTargetGroup(targetGroup);

    // Service Auto Scaling (separate from EC2 Auto Scaling)
    const scaling = service.autoScaleTaskCount({
      minCapacity: config.ecsMinCapacity,
      maxCapacity: config.ecsMaxCapacity,
    });

    scaling.scaleOnCpuUtilization('CpuScaling', {
      targetUtilizationPercent: config.ecsTargetCpuUtilization,
      scaleInCooldown: cdk.Duration.seconds(60),
      scaleOutCooldown: cdk.Duration.seconds(60),
    });

    scaling.scaleOnMemoryUtilization('MemoryScaling', {
      targetUtilizationPercent: 70,
      scaleInCooldown: cdk.Duration.seconds(60),
      scaleOutCooldown: cdk.Duration.seconds(60),
    });

    this.service = service;

    // Output EC2-specific information
    new cdk.CfnOutput(this, 'EC2InstanceType', {
      value: config.ec2InstanceType,
      description: 'EC2 Instance Type',
    });

    new cdk.CfnOutput(this, 'EC2UseSpot', {
      value: config.ec2UseSpot.toString(),
      description: 'Using Spot Instances',
    });
  }

  /**
   * Create Admin Portal Fargate service
   * Runs the admin portal with path-based routing on /admin/*
   */
  private createAdminPortalService(
    config: EnvironmentConfig,
    vpc: ec2.Vpc,
    ecsSecurityGroup: ec2.SecurityGroup,
    _logGroup: logs.LogGroup, // Unused - admin portal has its own log group
    taskExecutionRole: iam.Role,
    taskRole: iam.Role,
    cpuArchitecture: ecs.CpuArchitecture,
    dockerPlatform: Platform,
    tables: {
      apiKeysTable: dynamodb.Table;
      usageTable: dynamodb.Table;
      modelMappingTable: dynamodb.Table;
      usageStatsTable: dynamodb.Table;
      modelPricingTable: dynamodb.Table;
      providerKeysTable: dynamodb.Table;
      routingRulesTable: dynamodb.Table;
      failoverChainsTable: dynamodb.Table;
      smartRoutingConfigTable: dynamodb.Table;
      providersTable: dynamodb.Table;
    },
    cognitoUserPoolId?: string,
    cognitoClientId?: string
  ): void {
    // Create Admin Portal Log Group
    const adminLogGroup = new logs.LogGroup(this, 'AdminPortalLogGroup', {
      logGroupName: `/ecs/admin-portal-${config.environmentName}`,
      retention: config.logRetentionDays as logs.RetentionDays,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // Create Admin Portal Task Definition
    const adminTaskDefinition = new ecs.FargateTaskDefinition(this, 'AdminPortalTaskDefinition', {
      family: `admin-portal-${config.environmentName}`,
      cpu: config.adminPortalCpu,
      memoryLimitMiB: config.adminPortalMemory,
      executionRole: taskExecutionRole,
      taskRole: taskRole,
      runtimePlatform: {
        cpuArchitecture,
        operatingSystemFamily: ecs.OperatingSystemFamily.LINUX,
      },
    });

    // Admin Portal environment variables
    const adminEnvVars: { [key: string]: string } = {
      AWS_REGION: config.region,
      AWS_DEFAULT_REGION: config.region,
      ENVIRONMENT: config.environmentName === 'prod' ? 'production' : 'development',
      LOG_LEVEL: config.environmentName === 'prod' ? 'INFO' : 'DEBUG',
      // DynamoDB Tables
      DYNAMODB_API_KEYS_TABLE: tables.apiKeysTable.tableName,
      DYNAMODB_USAGE_TABLE: tables.usageTable.tableName,
      DYNAMODB_MODEL_MAPPING_TABLE: tables.modelMappingTable.tableName,
      DYNAMODB_USAGE_STATS_TABLE: tables.usageStatsTable.tableName,
      DYNAMODB_MODEL_PRICING_TABLE: tables.modelPricingTable.tableName,
      // Multi-Provider Gateway Tables
      DYNAMODB_PROVIDER_KEYS_TABLE: tables.providerKeysTable.tableName,
      DYNAMODB_ROUTING_RULES_TABLE: tables.routingRulesTable.tableName,
      DYNAMODB_FAILOVER_CHAINS_TABLE: tables.failoverChainsTable.tableName,
      DYNAMODB_SMART_ROUTING_CONFIG_TABLE: tables.smartRoutingConfigTable.tableName,
      DYNAMODB_PROVIDERS_TABLE: tables.providersTable.tableName,
      // Cognito (if configured)
      ...(cognitoUserPoolId && { COGNITO_USER_POOL_ID: cognitoUserPoolId }),
      ...(cognitoClientId && { COGNITO_CLIENT_ID: cognitoClientId }),
      COGNITO_REGION: config.region,
      // Static file serving
      SERVE_STATIC_FILES: 'true',
    };

    // Add Admin Portal Container
    adminTaskDefinition.addContainer('admin-portal', {
      containerName: 'admin-portal',
      image: ecs.ContainerImage.fromAsset(path.join(__dirname, '../../'), {
        file: 'admin_portal/Dockerfile',
        exclude: ['cdk/cdk.out', 'cdk/node_modules', '.git'],
        platform: dockerPlatform,
      }),
      logging: ecs.LogDriver.awsLogs({
        streamPrefix: 'admin-portal',
        logGroup: adminLogGroup,
      }),
      environment: adminEnvVars,
      portMappings: [
        {
          containerPort: config.adminPortalContainerPort,
          protocol: ecs.Protocol.TCP,
        },
      ],
      healthCheck: {
        command: [
          'CMD-SHELL',
          `curl -f http://localhost:${config.adminPortalContainerPort}${config.adminPortalHealthCheckPath} || exit 1`,
        ],
        interval: cdk.Duration.seconds(30),
        timeout: cdk.Duration.seconds(5),
        retries: 3,
        startPeriod: cdk.Duration.seconds(60),
      },
    });

    // Create Admin Portal Target Group
    const adminTargetGroup = new elbv2.ApplicationTargetGroup(this, 'AdminPortalTargetGroup', {
      vpc,
      port: config.adminPortalContainerPort,
      protocol: elbv2.ApplicationProtocol.HTTP,
      targetType: elbv2.TargetType.IP,
      healthCheck: {
        path: config.adminPortalHealthCheckPath,
        interval: cdk.Duration.seconds(30),
        timeout: cdk.Duration.seconds(10),
        healthyThresholdCount: 2,
        unhealthyThresholdCount: 5,
        healthyHttpCodes: '200',
      },
      deregistrationDelay: cdk.Duration.seconds(30),
    });

    // Store admin target group for CloudFront header validation rules
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

    // Create Admin Portal Fargate Service
    const adminService = new ecs.FargateService(this, 'AdminPortalService', {
      cluster: this.cluster,
      taskDefinition: adminTaskDefinition,
      desiredCount: config.adminPortalMinCapacity,
      assignPublicIp: false,
      securityGroups: [ecsSecurityGroup],
      vpcSubnets: {
        subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
      },
      healthCheckGracePeriod: cdk.Duration.seconds(120),
      circuitBreaker: {
        rollback: true,
      },
      enableExecuteCommand: config.environmentName !== 'prod',
      minHealthyPercent: 100,
      maxHealthyPercent: 200,
    });

    // Attach to Target Group
    adminService.attachToApplicationTargetGroup(adminTargetGroup);

    // Auto Scaling
    const adminScaling = adminService.autoScaleTaskCount({
      minCapacity: config.adminPortalMinCapacity,
      maxCapacity: config.adminPortalMaxCapacity,
    });

    adminScaling.scaleOnCpuUtilization('AdminPortalCpuScaling', {
      targetUtilizationPercent: 70,
      scaleInCooldown: cdk.Duration.seconds(60),
      scaleOutCooldown: cdk.Duration.seconds(60),
    });

    // Grant DynamoDB permissions to task role (if not already granted)
    tables.usageStatsTable.grantReadWriteData(taskRole);
    tables.modelPricingTable.grantReadWriteData(taskRole);
    tables.providersTable.grantReadWriteData(taskRole);

    // Output Admin Portal information
    new cdk.CfnOutput(this, 'AdminPortalServiceName', {
      value: adminService.serviceName,
      description: 'Admin Portal ECS Service Name',
    });

    // Only output HTTP admin URL when CloudFront is not enabled
    // (CloudFront outputs its own HTTPS admin URL in the constructor)
    if (!config.enableCloudFront) {
      new cdk.CfnOutput(this, 'AdminPortalURL', {
        value: `http://${this.alb.loadBalancerDnsName}/admin/`,
        description: 'Admin Portal URL',
      });
    }
  }
}
