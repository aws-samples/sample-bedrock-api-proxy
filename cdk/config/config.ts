/**
 * Configuration for different deployment environments
 */

export interface EnvironmentConfig {
  // AWS Account and Region
  account?: string;
  region: string;

  // Environment name
  environmentName: string;

  // Platform architecture (set dynamically from CDK_PLATFORM env var)
  platform: 'arm64' | 'amd64';

  // Launch type (set dynamically from CDK_LAUNCH_TYPE env var)
  // - fargate: Serverless, no Docker access (default)
  // - ec2: EC2 instances, supports Docker socket for PTC
  launchType: 'fargate' | 'ec2';

  // VPC Configuration
  vpcCidr: string;
  maxAzs: number;

  // ECS Configuration
  ecsDesiredCount: number;
  ecsCpu: number;
  ecsMemory: number;
  ecsMinCapacity: number;
  ecsMaxCapacity: number;
  ecsTargetCpuUtilization: number;

  // EC2 Launch Type Configuration (only used when launchType is 'ec2')
  ec2InstanceType: string;           // e.g., 't3.medium', 'c6g.large'
  ec2UseSpot: boolean;               // Use Spot instances for cost savings
  ec2SpotMaxPrice?: string;          // Max price for Spot (e.g., '0.05')
  ec2RootVolumeSize: number;         // Root volume size in GB
  ec2EnableDockerSocket: boolean;    // Mount Docker socket for PTC support

  // Container Configuration
  containerPort: number;
  healthCheckPath: string;
  healthCheckInterval: number;
  healthCheckTimeout: number;
  healthCheckHealthyThreshold: number;
  healthCheckUnhealthyThreshold: number;

  // Application Configuration
  requireApiKey: boolean;
  rateLimitEnabled: boolean;
  rateLimitRequests: number;
  rateLimitWindow: number;
  enableMetrics: boolean;

  // PTC (Programmatic Tool Calling) Configuration
  enablePtc: boolean;                // Enable PTC feature (requires EC2 launch type)
  ptcSandboxImage: string;           // Docker image for PTC sandbox
  ptcSessionTimeout: number;         // Session timeout in seconds
  ptcExecutionTimeout: number;       // Code execution timeout in seconds
  ptcMemoryLimit: string;            // Container memory limit (e.g., '256m')

  // Web Search Configuration
  enableWebSearch: boolean;
  webSearchProvider?: string;              // 'tavily' or 'brave'
  webSearchApiKey?: string;                // Search provider API key
  webSearchMaxResults?: number;            // Max results per search (default: 5)
  webSearchDefaultMaxUses?: number;        // Max searches per request (default: 10)

  // Web Fetch Configuration
  enableWebFetch: boolean;
  webFetchDefaultMaxUses?: number;         // Max fetches per request (default: 20)
  webFetchDefaultMaxContentTokens?: number; // Max content tokens per fetch (default: 100000)

  // Cache TTL Configuration
  defaultCacheTtl?: string;                // Proxy-level default cache TTL ('5m' or '1h')
  stripCacheScope?: boolean;               // Strip unsupported 'scope' from cache_control (default: true)

  // Bedrock Concurrency Settings
  bedrockThreadPoolSize: number;
  bedrockSemaphoreSize: number;

  // OpenTelemetry Tracing Configuration
  enableTracing: boolean;                    // Enable OTEL tracing
  otelExporterEndpoint?: string;             // OTLP exporter endpoint (e.g., Langfuse, Jaeger)
  otelExporterProtocol?: string;             // http/protobuf (default) or grpc
  otelExporterHeaders?: string;              // Auth headers (format: key1=value1,key2=value2)
  otelServiceName?: string;                  // Service name in tracing backend
  otelTraceContent?: boolean;                // Record prompt/completion content (PII risk)
  otelTraceSamplingRatio?: number;           // Sampling ratio 0.0-1.0

  // OpenAI-Compatible API (Bedrock Mantle) Configuration
  enableOpenaiCompat: boolean;
  openaiBaseUrl?: string;                    // e.g., https://bedrock-mantle.us-east-1.api.aws/v1

  // Admin Portal Configuration
  adminPortalEnabled: boolean;
  adminPortalCpu: number;           // CPU units (1024 = 1 vCPU)
  adminPortalMemory: number;        // Memory in MiB
  adminPortalMinCapacity: number;   // Min number of tasks
  adminPortalMaxCapacity: number;   // Max number of tasks
  adminPortalContainerPort: number; // Container port (8005)
  adminPortalHealthCheckPath: string; // Health check path

  // DynamoDB Configuration
  dynamodbBillingMode: 'PAY_PER_REQUEST' | 'PROVISIONED';
  dynamodbReadCapacity?: number;
  dynamodbWriteCapacity?: number;

  // Logging Configuration
  logRetentionDays: number;
  enableContainerInsights: boolean;

  // CloudFront Configuration
  enableCloudFront: boolean;            // Enable CloudFront distribution with HTTPS
  cloudFrontOriginReadTimeout: number;  // Origin read timeout in seconds (default max 60; up to 180 with AWS quota increase)

  // Tags
  tags: { [key: string]: string };
}

// Environment configurations without runtime settings (platform and launchType are set at deployment time)
type EnvironmentConfigWithoutRuntime = Omit<EnvironmentConfig, 'platform' | 'launchType'>;

export const environments: { [key: string]: EnvironmentConfigWithoutRuntime } = {
  dev: {
    region: process.env.AWS_REGION || 'us-west-2',
    environmentName: 'dev',

    // VPC
    vpcCidr: '10.0.0.0/16',
    maxAzs: 2,

    // ECS
    ecsDesiredCount: 1,
    ecsCpu: 1024,          // 1 vCPU
    ecsMemory: 2048,       // 2 GB
    ecsMinCapacity: 1,
    ecsMaxCapacity: 2,
    ecsTargetCpuUtilization: 70,

    // EC2 Launch Type (used when launchType is 'ec2')
    ec2InstanceType: 't3.medium',  // Will be overridden based on platform
    ec2UseSpot: true,              // Use Spot for dev to save cost
    ec2RootVolumeSize: 30,         // 30GB root volume
    ec2EnableDockerSocket: true,   // Enable Docker socket for PTC

    // Container
    containerPort: 8000,
    healthCheckPath: '/health',
    healthCheckInterval: 30,
    healthCheckTimeout: 10,
    healthCheckHealthyThreshold: 2,
    healthCheckUnhealthyThreshold: 5,

    // Application
    requireApiKey: true,
    rateLimitEnabled: true,
    rateLimitRequests: 100,
    rateLimitWindow: 60,
    enableMetrics: true,

    // PTC (Programmatic Tool Calling)
    enablePtc: false,                     // Disabled by default, enabled when using EC2
    ptcSandboxImage: 'public.ecr.aws/f8g1z3n8/bedrock-proxy-sandbox:minimal.0.1',
    ptcSessionTimeout: 270,               // 4.5 minutes
    ptcExecutionTimeout: 60,
    ptcMemoryLimit: '256m',

    // Web Search (set via env vars: ENABLE_WEB_SEARCH, WEB_SEARCH_PROVIDER, WEB_SEARCH_API_KEY)
    enableWebSearch: false,
    // webSearchProvider: 'tavily',
    // webSearchApiKey: 'tvly-xxx',
    // webSearchMaxResults: 5,
    // webSearchDefaultMaxUses: 10,

    // Web Fetch (enabled by default, uses httpx direct fetch — no API key needed)
    enableWebFetch: true,
    // webFetchDefaultMaxUses: 20,
    // webFetchDefaultMaxContentTokens: 100000,

    // Cache TTL (set via env var: DEFAULT_CACHE_TTL)
    // defaultCacheTtl: '1h',

    // Bedrock Concurrency
    bedrockThreadPoolSize: 15,
    bedrockSemaphoreSize: 15,

    // OpenTelemetry Tracing
    enableTracing: false,
    // otelExporterEndpoint: 'https://cloud.langfuse.com/api/public/otel',
    // otelExporterProtocol: 'http/protobuf',
    // otelExporterHeaders: 'Authorization=Basic <base64(publicKey:secretKey)>',
    // otelServiceName: 'anthropic-bedrock-proxy-dev',
    // otelTraceContent: false,
    // otelTraceSamplingRatio: 1.0,

    // OpenAI-Compatible API (Bedrock Mantle)
    enableOpenaiCompat: false,
    // openaiBaseUrl: 'https://bedrock-mantle.us-east-1.api.aws/v1',

    // Admin Portal
    adminPortalEnabled: true,
    adminPortalCpu: 1024,          // 1 vCPU (Fargate: 1024 CPU requires 2048-8192 MB memory)
    adminPortalMemory: 2048,       // 2 GB
    adminPortalMinCapacity: 1,
    adminPortalMaxCapacity: 2,
    adminPortalContainerPort: 8005,
    adminPortalHealthCheckPath: '/health',

    // DynamoDB
    dynamodbBillingMode: 'PAY_PER_REQUEST',

    // CloudFront (HTTPS)
    enableCloudFront: false,
    cloudFrontOriginReadTimeout: 60,  // Max 60s default; request AWS quota increase for up to 180s

    // Logging
    logRetentionDays: 7,
    enableContainerInsights: false,

    // Tags
    tags: {
      Environment: 'dev',
      Project: 'anthropic-proxy',
      ManagedBy: 'CDK',
    },
  },

  prod: {
    region: process.env.AWS_REGION || 'us-west-2',
    environmentName: 'prod',

    // VPC
    vpcCidr: '10.1.0.0/16',
    maxAzs: 3,

    // ECS
    ecsDesiredCount: 2,
    ecsCpu: 1024,          // 1 vCPU
    ecsMemory: 2048,       // 2 GB
    ecsMinCapacity: 2,
    ecsMaxCapacity: 10,
    ecsTargetCpuUtilization: 70,

    // EC2 Launch Type (used when launchType is 'ec2')
    ec2InstanceType: 't3.large',   // Will be overridden based on platform
    ec2UseSpot: false,             // Use On-Demand for prod stability
    ec2RootVolumeSize: 100,         // 50GB root volume
    ec2EnableDockerSocket: true,   // Enable Docker socket for PTC

    // Container
    containerPort: 8000,
    healthCheckPath: '/health',
    healthCheckInterval: 30,
    healthCheckTimeout: 10,
    healthCheckHealthyThreshold: 2,
    healthCheckUnhealthyThreshold: 5,

    // Application
    requireApiKey: true,
    rateLimitEnabled: true,
    rateLimitRequests: 1000,
    rateLimitWindow: 60,
    enableMetrics: true,

    // PTC (Programmatic Tool Calling)
    enablePtc: false,                     // Disabled by default, enabled when using EC2
    ptcSandboxImage: 'python:3.11-slim',
    ptcSessionTimeout: 270,               // 4.5 minutes
    ptcExecutionTimeout: 60,
    ptcMemoryLimit: '256m',

    // Web Search (set via env vars: ENABLE_WEB_SEARCH, WEB_SEARCH_PROVIDER, WEB_SEARCH_API_KEY)
    enableWebSearch: false,
    // webSearchProvider: 'tavily',
    // webSearchApiKey: 'tvly-xxx',
    // webSearchMaxResults: 5,
    // webSearchDefaultMaxUses: 10,

    // Web Fetch (enabled by default, uses httpx direct fetch — no API key needed)
    enableWebFetch: true,
    // webFetchDefaultMaxUses: 20,
    // webFetchDefaultMaxContentTokens: 100000,

    // Cache TTL (set via env var: DEFAULT_CACHE_TTL)
    // defaultCacheTtl: '1h',

    // Bedrock Concurrency
    bedrockThreadPoolSize: 30,
    bedrockSemaphoreSize: 30,

    // OpenTelemetry Tracing
    enableTracing: false,
    // otelExporterEndpoint: 'https://cloud.langfuse.com/api/public/otel',
    // otelExporterProtocol: 'http/protobuf',
    // otelExporterHeaders: 'Authorization=Basic <base64(publicKey:secretKey)>',
    // otelServiceName: 'anthropic-bedrock-proxy-prod',
    // otelTraceContent: false,
    // otelTraceSamplingRatio: 0.1,

    // OpenAI-Compatible API (Bedrock Mantle)
    enableOpenaiCompat: false,
    // openaiBaseUrl: 'https://bedrock-mantle.us-east-1.api.aws/v1',

    // Admin Portal
    adminPortalEnabled: true,
    adminPortalCpu: 1024,          // 1 vCPU (Fargate: 1024 CPU requires 2048-8192 MB memory)
    adminPortalMemory: 2048,       // 2 GB
    adminPortalMinCapacity: 1,
    adminPortalMaxCapacity: 4,
    adminPortalContainerPort: 8005,
    adminPortalHealthCheckPath: '/health',

    // DynamoDB
    dynamodbBillingMode: 'PAY_PER_REQUEST',

    // CloudFront (HTTPS)
    enableCloudFront: false,
    cloudFrontOriginReadTimeout: 60,  // Max 60s default; request AWS quota increase for up to 180s

    // Logging
    logRetentionDays: 30,
    enableContainerInsights: true,

    // Tags
    tags: {
      Environment: 'prod',
      Project: 'anthropic-proxy',
      ManagedBy: 'CDK',
    },
  },
};

// Helper function to get EC2 instance type based on platform
function getEc2InstanceType(baseType: string, platform: 'arm64' | 'amd64'): string {
  // Map x86 instance types to ARM equivalents
  const armMapping: { [key: string]: string } = {
    't3.micro': 't4g.micro',
    't3.small': 't4g.small',
    't3.medium': 't4g.medium',
    't3.large': 't4g.large',
    't3.xlarge': 't4g.xlarge',
    't3.2xlarge': 't4g.2xlarge',
    'm5.large': 'm6g.large',
    'm5.xlarge': 'm6g.xlarge',
    'm5.2xlarge': 'm6g.2xlarge',
    'c5.large': 'c6g.large',
    'c5.xlarge': 'c6g.xlarge',
    'c5.2xlarge': 'c6g.2xlarge',
    'r5.large': 'r6g.large',
    'r5.xlarge': 'r6g.xlarge',
  };

  if (platform === 'arm64' && armMapping[baseType]) {
    return armMapping[baseType];
  }
  return baseType;
}

export function getConfig(environmentName: string = 'dev'): EnvironmentConfig {
  const config = environments[environmentName];
  if (!config) {
    throw new Error(
      `Unknown environment: ${environmentName}. Available: ${Object.keys(environments).join(', ')}`
    );
  }

  // Get platform from environment variable (set by deploy script)
  // Default to 'arm64' when not specified (e.g., during `cdk bootstrap`)
  const platform = (process.env.CDK_PLATFORM as 'arm64' | 'amd64') || 'arm64';
  if (!['arm64', 'amd64'].includes(platform)) {
    throw new Error(
      `Platform must be 'arm64' or 'amd64'. Got: ${platform}`
    );
  }

  // Get launch type from environment variable (set by deploy script)
  // Default to 'fargate' if not specified
  const launchType = (process.env.CDK_LAUNCH_TYPE as 'fargate' | 'ec2') || 'fargate';
  if (!['fargate', 'ec2'].includes(launchType)) {
    throw new Error(
      `Launch type must be 'fargate' or 'ec2'. Got: ${launchType}`
    );
  }

  // Adjust EC2 instance type based on platform
  const ec2InstanceType = getEc2InstanceType(config.ec2InstanceType, platform);

  // Enable PTC automatically when using EC2 launch type with Docker socket
  const enablePtc = launchType === 'ec2' && config.ec2EnableDockerSocket;

  // Override OTEL tracing settings from environment variables
  // This allows enabling tracing at deploy time without modifying config code
  const enableTracing = process.env.ENABLE_TRACING
    ? process.env.ENABLE_TRACING.toLowerCase() === 'true'
    : config.enableTracing;

  // Override Web Search settings from environment variables
  const enableWebSearch = process.env.ENABLE_WEB_SEARCH
    ? process.env.ENABLE_WEB_SEARCH.toLowerCase() === 'true'
    : config.enableWebSearch;

  // Override Web Fetch settings from environment variables
  const enableWebFetch = process.env.ENABLE_WEB_FETCH
    ? process.env.ENABLE_WEB_FETCH.toLowerCase() === 'true'
    : config.enableWebFetch;

  // Override OpenAI-compat settings from environment variables
  const enableOpenaiCompat = process.env.ENABLE_OPENAI_COMPAT
    ? process.env.ENABLE_OPENAI_COMPAT.toLowerCase() === 'true'
    : config.enableOpenaiCompat;

  // Override CloudFront settings from environment variables
  const enableCloudFront = process.env.ENABLE_CLOUDFRONT
    ? process.env.ENABLE_CLOUDFRONT.toLowerCase() === 'true'
    : config.enableCloudFront;

  return {
    ...config,
    platform,
    launchType,
    ec2InstanceType,
    enablePtc,
    enableTracing,
    enableWebSearch,
    enableWebFetch,
    enableOpenaiCompat,
    enableCloudFront,
    ...(process.env.OPENAI_BASE_URL && { openaiBaseUrl: process.env.OPENAI_BASE_URL }),
    ...(process.env.OTEL_EXPORTER_OTLP_ENDPOINT && { otelExporterEndpoint: process.env.OTEL_EXPORTER_OTLP_ENDPOINT }),
    ...(process.env.OTEL_EXPORTER_OTLP_PROTOCOL && { otelExporterProtocol: process.env.OTEL_EXPORTER_OTLP_PROTOCOL }),
    ...(process.env.OTEL_EXPORTER_OTLP_HEADERS && { otelExporterHeaders: process.env.OTEL_EXPORTER_OTLP_HEADERS }),
    ...(process.env.OTEL_SERVICE_NAME && { otelServiceName: process.env.OTEL_SERVICE_NAME }),
    ...(process.env.OTEL_TRACE_CONTENT && { otelTraceContent: process.env.OTEL_TRACE_CONTENT.toLowerCase() === 'true' }),
    ...(process.env.OTEL_TRACE_SAMPLING_RATIO && { otelTraceSamplingRatio: parseFloat(process.env.OTEL_TRACE_SAMPLING_RATIO) }),
    ...(process.env.WEB_SEARCH_PROVIDER && { webSearchProvider: process.env.WEB_SEARCH_PROVIDER }),
    ...(process.env.WEB_SEARCH_API_KEY && { webSearchApiKey: process.env.WEB_SEARCH_API_KEY }),
    ...(process.env.WEB_SEARCH_MAX_RESULTS && { webSearchMaxResults: parseInt(process.env.WEB_SEARCH_MAX_RESULTS) }),
    ...(process.env.WEB_SEARCH_DEFAULT_MAX_USES && { webSearchDefaultMaxUses: parseInt(process.env.WEB_SEARCH_DEFAULT_MAX_USES) }),
    ...(process.env.WEB_FETCH_DEFAULT_MAX_USES && { webFetchDefaultMaxUses: parseInt(process.env.WEB_FETCH_DEFAULT_MAX_USES) }),
    ...(process.env.WEB_FETCH_DEFAULT_MAX_CONTENT_TOKENS && { webFetchDefaultMaxContentTokens: parseInt(process.env.WEB_FETCH_DEFAULT_MAX_CONTENT_TOKENS) }),
    ...(process.env.DEFAULT_CACHE_TTL && { defaultCacheTtl: process.env.DEFAULT_CACHE_TTL }),
    ...(process.env.STRIP_CACHE_SCOPE && { stripCacheScope: process.env.STRIP_CACHE_SCOPE.toLowerCase() === 'true' }),
  };
}
