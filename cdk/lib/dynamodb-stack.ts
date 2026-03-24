import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as cr from 'aws-cdk-lib/custom-resources';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import { EnvironmentConfig } from '../config/config';

// Default model pricing data to seed on table creation (prices per 1M tokens in USD)
const DEFAULT_PRICING = [
  // Anthropic models
  {
    model_id: 'anthropic.claude-3-5-haiku-20241022-v1:0',
    provider: 'Anthropic',
    display_name: 'Claude 3.5 Haiku',
    input_price: '0.80',
    output_price: '4.00',
    cache_read_price: '0.08',
    cache_write_price: '1.00',
    status: 'active',
  },
  {
    model_id: 'global.anthropic.claude-haiku-4-5-20251001-v1:0',
    provider: 'Anthropic',
    display_name: 'Claude Haiku 4.5',
    input_price: '1.00',
    output_price: '5.00',
    cache_read_price: '0.10',
    cache_write_price: '1.25',
    status: 'active',
  },
  {
    model_id: 'global.anthropic.claude-opus-4-5-20251101-v1:0',
    provider: 'Anthropic',
    display_name: 'Claude Opus 4.5',
    input_price: '5.00',
    output_price: '25.00',
    cache_read_price: '0.50',
    cache_write_price: '6.25',
    status: 'active',
  },
  {
    model_id: 'global.anthropic.claude-opus-4-6-v1',
    provider: 'Anthropic',
    display_name: 'Claude Opus 4.6',
    input_price: '5.00',
    output_price: '25.00',
    cache_read_price: '0.50',
    cache_write_price: '6.25',
    status: 'active',
  },
  {
    model_id: 'global.anthropic.claude-sonnet-4-6',
    provider: 'Anthropic',
    display_name: 'Claude Sonnet 4.6',
    input_price: '3.00',
    output_price: '15.00',
    cache_read_price: '0.30',
    cache_write_price: '3.75',
    status: 'active',
  },
  {
    model_id: 'global.anthropic.claude-sonnet-4-5-20250929-v1:0',
    provider: 'Anthropic',
    display_name: 'Claude Sonnet 4.5',
    input_price: '3.00',
    output_price: '15.00',
    cache_read_price: '0.30',
    cache_write_price: '3.75',
    status: 'active',
  },
  // MiniMax models
  {
    model_id: 'minimax.minimax-m2',
    provider: 'MiniMax',
    display_name: 'MiniMax M2',
    input_price: '0.15',
    output_price: '0.60',
    cache_read_price: '0.00',
    cache_write_price: '0.00',
    status: 'active',
  },
  {
    model_id: 'minimax.minimax-m2.1',
    provider: 'MiniMax',
    display_name: 'MiniMax M2.1',
    input_price: '0.30',
    output_price: '1.20',
    cache_read_price: '0.00',
    cache_write_price: '0.00',
    status: 'active',
  },
  // Qwen models
  {
    model_id: 'qwen.qwen3-coder-480b-a35b-v1:0',
    provider: 'Qwen',
    display_name: 'Qwen3 Coder 480B A35B',
    input_price: '0.45',
    output_price: '0.90',
    cache_read_price: '0.00',
    cache_write_price: '0.00',
    status: 'active',
  },
  {
    model_id: 'qwen.qwen3-235b-a22b-2507-v1:0',
    provider: 'Qwen',
    display_name: 'Qwen3 235B A22B',
    input_price: '0.11',
    output_price: '0.88',
    cache_read_price: '0.00',
    cache_write_price: '0.00',
    status: 'active',
  },
  {
    model_id: 'qwen.qwen3-next-80b-a3b',
    provider: 'Qwen',
    display_name: 'Qwen3 Next 80B A3B',
    input_price: '0.14',
    output_price: '1.20',
    cache_read_price: '0.00',
    cache_write_price: '0.00',
    status: 'active',
  },
  {
    model_id: 'qwen.qwen3-32b-v1:0',
    provider: 'Qwen',
    display_name: 'Qwen3 32B',
    input_price: '0.15',
    output_price: '0.60',
    cache_read_price: '0.00',
    cache_write_price: '0.00',
    status: 'active',
  },
  {
    model_id: 'qwen.qwen3-coder-30b-a3b-v1:0',
    provider: 'Qwen',
    display_name: 'Qwen3 Coder 30B A3B',
    input_price: '0.075',
    output_price: '0.60',
    cache_read_price: '0.00',
    cache_write_price: '0.00',
    status: 'active',
  },
  {
    model_id: 'qwen.qwen3-vl-235b-a22b',
    provider: 'Qwen',
    display_name: 'Qwen3 VL 235B A22B',
    input_price: '0.53',
    output_price: '1.33',
    cache_read_price: '0.00',
    cache_write_price: '0.00',
    status: 'active',
  },
  // DeepSeek models
  {
    model_id: 'deepseek.v3-v1:0',
    provider: 'DeepSeek',
    display_name: 'DeepSeek V3.1',
    input_price: '0.58',
    output_price: '1.68',
    cache_read_price: '0.00',
    cache_write_price: '0.00',
    status: 'active',
  },
  {
    model_id: 'deepseek.v3.2',
    provider: 'DeepSeek',
    display_name: 'DeepSeek V3.2',
    input_price: '0.62',
    output_price: '1.85',
    cache_read_price: '0.00',
    cache_write_price: '0.00',
    status: 'active',
  },
  // Moonshot AI models
  {
    model_id: 'moonshotai.kimi-k2.5',
    provider: 'Moonshot AI',
    display_name: 'Kimi K2.5',
    input_price: '0.60',
    output_price: '3.00',
    cache_read_price: '0.00',
    cache_write_price: '0.00',
    status: 'active',
  },
  {
    model_id: 'moonshot.kimi-k2-thinking',
    provider: 'Moonshot AI',
    display_name: 'Kimi K2 Thinking',
    input_price: '0.60',
    output_price: '2.50',
    cache_read_price: '0.00',
    cache_write_price: '0.00',
    status: 'active',
  },
  // Z AI models
  {
    model_id: 'zai.glm-4.7',
    provider: 'Z AI',
    display_name: 'GLM 4.7',
    input_price: '0.60',
    output_price: '2.20',
    cache_read_price: '0.00',
    cache_write_price: '0.00',
    status: 'active',
  },
  {
    model_id: 'zai.glm-4.7-flash',
    provider: 'Z AI',
    display_name: 'GLM 4.7 Flash',
    input_price: '0.07',
    output_price: '0.40',
    cache_read_price: '0.00',
    cache_write_price: '0.00',
    status: 'active',
  },
];

export interface DynamoDBStackProps extends cdk.StackProps {
  config: EnvironmentConfig;
}

export class DynamoDBStack extends cdk.Stack {
  public readonly apiKeysTable: dynamodb.Table;
  public readonly usageTable: dynamodb.Table;
  public readonly modelMappingTable: dynamodb.Table;
  public readonly usageStatsTable: dynamodb.Table;
  public readonly modelPricingTable: dynamodb.Table;
  public readonly providerKeysTable: dynamodb.Table;
  public readonly routingRulesTable: dynamodb.Table;
  public readonly failoverChainsTable: dynamodb.Table;
  public readonly smartRoutingConfigTable: dynamodb.Table;

  constructor(scope: Construct, id: string, props: DynamoDBStackProps) {
    super(scope, id, props);

    const { config } = props;

    // API Keys Table
    // Note: tableName is intentionally omitted to let CDK generate unique names
    // and avoid resource conflicts across deployments
    this.apiKeysTable = new dynamodb.Table(this, 'APIKeysTable', {
      partitionKey: {
        name: 'api_key',
        type: dynamodb.AttributeType.STRING,
      },
      billingMode:
        config.dynamodbBillingMode === 'PAY_PER_REQUEST'
          ? dynamodb.BillingMode.PAY_PER_REQUEST
          : dynamodb.BillingMode.PROVISIONED,
      readCapacity: config.dynamodbReadCapacity,
      writeCapacity: config.dynamodbWriteCapacity,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      pointInTimeRecoverySpecification: {
        pointInTimeRecoveryEnabled: config.environmentName === 'prod',
      },
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
    });

    // GSI for user_id lookups
    this.apiKeysTable.addGlobalSecondaryIndex({
      indexName: 'user_id-index',
      partitionKey: {
        name: 'user_id',
        type: dynamodb.AttributeType.STRING,
      },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // Usage Tracking Table
    this.usageTable = new dynamodb.Table(this, 'UsageTable', {
      partitionKey: {
        name: 'api_key',
        type: dynamodb.AttributeType.STRING,
      },
      sortKey: {
        name: 'timestamp',
        type: dynamodb.AttributeType.STRING,
      },
      billingMode:
        config.dynamodbBillingMode === 'PAY_PER_REQUEST'
          ? dynamodb.BillingMode.PAY_PER_REQUEST
          : dynamodb.BillingMode.PROVISIONED,
      readCapacity: config.dynamodbReadCapacity,
      writeCapacity: config.dynamodbWriteCapacity,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      pointInTimeRecoverySpecification: {
        pointInTimeRecoveryEnabled: config.environmentName === 'prod',
      },
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      timeToLiveAttribute: 'ttl',
    });

    // GSI for request_id lookups
    this.usageTable.addGlobalSecondaryIndex({
      indexName: 'request_id-index',
      partitionKey: {
        name: 'request_id',
        type: dynamodb.AttributeType.STRING,
      },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // Model Mapping Table
    this.modelMappingTable = new dynamodb.Table(this, 'ModelMappingTable', {
      partitionKey: {
        name: 'anthropic_model_id',
        type: dynamodb.AttributeType.STRING,
      },
      billingMode:
        config.dynamodbBillingMode === 'PAY_PER_REQUEST'
          ? dynamodb.BillingMode.PAY_PER_REQUEST
          : dynamodb.BillingMode.PROVISIONED,
      readCapacity: config.dynamodbReadCapacity,
      writeCapacity: config.dynamodbWriteCapacity,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
    });

    // Usage Stats Table (aggregated usage statistics)
    // Used by admin portal for displaying aggregated token counts and budget
    this.usageStatsTable = new dynamodb.Table(this, 'UsageStatsTable', {
      partitionKey: {
        name: 'api_key',
        type: dynamodb.AttributeType.STRING,
      },
      billingMode:
        config.dynamodbBillingMode === 'PAY_PER_REQUEST'
          ? dynamodb.BillingMode.PAY_PER_REQUEST
          : dynamodb.BillingMode.PROVISIONED,
      readCapacity: config.dynamodbReadCapacity,
      writeCapacity: config.dynamodbWriteCapacity,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      pointInTimeRecoverySpecification: {
        pointInTimeRecoveryEnabled: config.environmentName === 'prod',
      },
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
    });

    // Model Pricing Table (model pricing configuration)
    // Used by admin portal for cost calculation and pricing management
    this.modelPricingTable = new dynamodb.Table(this, 'ModelPricingTable', {
      partitionKey: {
        name: 'model_id',
        type: dynamodb.AttributeType.STRING,
      },
      billingMode:
        config.dynamodbBillingMode === 'PAY_PER_REQUEST'
          ? dynamodb.BillingMode.PAY_PER_REQUEST
          : dynamodb.BillingMode.PROVISIONED,
      readCapacity: config.dynamodbReadCapacity,
      writeCapacity: config.dynamodbWriteCapacity,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
    });

    // Seed default pricing data using custom resource
    // This runs on CREATE only, won't overwrite existing data on UPDATE
    this.seedDefaultPricing(config);

    // === Multi-Provider Gateway Tables ===

    // Provider Keys Table (encrypted API keys for multi-provider support)
    this.providerKeysTable = new dynamodb.Table(this, 'ProviderKeysTable', {
      partitionKey: {
        name: 'key_id',
        type: dynamodb.AttributeType.STRING,
      },
      billingMode:
        config.dynamodbBillingMode === 'PAY_PER_REQUEST'
          ? dynamodb.BillingMode.PAY_PER_REQUEST
          : dynamodb.BillingMode.PROVISIONED,
      readCapacity: config.dynamodbReadCapacity,
      writeCapacity: config.dynamodbWriteCapacity,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
    });

    this.providerKeysTable.addGlobalSecondaryIndex({
      indexName: 'provider-index',
      partitionKey: {
        name: 'provider',
        type: dynamodb.AttributeType.STRING,
      },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // Routing Rules Table (rule-based routing configuration)
    this.routingRulesTable = new dynamodb.Table(this, 'RoutingRulesTable', {
      partitionKey: {
        name: 'rule_id',
        type: dynamodb.AttributeType.STRING,
      },
      billingMode:
        config.dynamodbBillingMode === 'PAY_PER_REQUEST'
          ? dynamodb.BillingMode.PAY_PER_REQUEST
          : dynamodb.BillingMode.PROVISIONED,
      readCapacity: config.dynamodbReadCapacity,
      writeCapacity: config.dynamodbWriteCapacity,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
    });

    // Failover Chains Table (cross-model failover configuration)
    this.failoverChainsTable = new dynamodb.Table(this, 'FailoverChainsTable', {
      partitionKey: {
        name: 'source_model',
        type: dynamodb.AttributeType.STRING,
      },
      billingMode:
        config.dynamodbBillingMode === 'PAY_PER_REQUEST'
          ? dynamodb.BillingMode.PAY_PER_REQUEST
          : dynamodb.BillingMode.PROVISIONED,
      readCapacity: config.dynamodbReadCapacity,
      writeCapacity: config.dynamodbWriteCapacity,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
    });

    // Smart Routing Config Table (global smart routing parameters)
    this.smartRoutingConfigTable = new dynamodb.Table(this, 'SmartRoutingConfigTable', {
      partitionKey: {
        name: 'config_id',
        type: dynamodb.AttributeType.STRING,
      },
      billingMode:
        config.dynamodbBillingMode === 'PAY_PER_REQUEST'
          ? dynamodb.BillingMode.PAY_PER_REQUEST
          : dynamodb.BillingMode.PROVISIONED,
      readCapacity: config.dynamodbReadCapacity,
      writeCapacity: config.dynamodbWriteCapacity,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
    });

    // Apply tags to all tables
    Object.entries(config.tags).forEach(([key, value]) => {
      cdk.Tags.of(this.apiKeysTable).add(key, value);
      cdk.Tags.of(this.usageTable).add(key, value);
      cdk.Tags.of(this.modelMappingTable).add(key, value);
      cdk.Tags.of(this.usageStatsTable).add(key, value);
      cdk.Tags.of(this.modelPricingTable).add(key, value);
      cdk.Tags.of(this.providerKeysTable).add(key, value);
      cdk.Tags.of(this.routingRulesTable).add(key, value);
      cdk.Tags.of(this.failoverChainsTable).add(key, value);
      cdk.Tags.of(this.smartRoutingConfigTable).add(key, value);
    });

    // Outputs - exportName omitted to avoid cross-stack conflicts
    new cdk.CfnOutput(this, 'APIKeysTableName', {
      value: this.apiKeysTable.tableName,
      description: 'API Keys DynamoDB Table Name',
    });

    new cdk.CfnOutput(this, 'UsageTableName', {
      value: this.usageTable.tableName,
      description: 'Usage Tracking DynamoDB Table Name',
    });

    new cdk.CfnOutput(this, 'ModelMappingTableName', {
      value: this.modelMappingTable.tableName,
      description: 'Model Mapping DynamoDB Table Name',
    });

    new cdk.CfnOutput(this, 'UsageStatsTableName', {
      value: this.usageStatsTable.tableName,
      description: 'Usage Stats DynamoDB Table Name',
    });

    new cdk.CfnOutput(this, 'ModelPricingTableName', {
      value: this.modelPricingTable.tableName,
      description: 'Model Pricing DynamoDB Table Name',
    });

    new cdk.CfnOutput(this, 'ProviderKeysTableName', {
      value: this.providerKeysTable.tableName,
      description: 'Provider Keys DynamoDB Table Name',
    });

    new cdk.CfnOutput(this, 'RoutingRulesTableName', {
      value: this.routingRulesTable.tableName,
      description: 'Routing Rules DynamoDB Table Name',
    });

    new cdk.CfnOutput(this, 'FailoverChainsTableName', {
      value: this.failoverChainsTable.tableName,
      description: 'Failover Chains DynamoDB Table Name',
    });

    new cdk.CfnOutput(this, 'SmartRoutingConfigTableName', {
      value: this.smartRoutingConfigTable.tableName,
      description: 'Smart Routing Config DynamoDB Table Name',
    });
  }

  /**
   * Seeds default pricing data using AWS Custom Resource.
   * This runs on CREATE only, won't overwrite existing data on UPDATE.
   */
  private seedDefaultPricing(config: EnvironmentConfig): void {
    // Create individual PutItem calls for each pricing entry
    // Using separate custom resources to handle each item
    DEFAULT_PRICING.forEach((pricing, index) => {
      const putItemParams = {
        TableName: this.modelPricingTable.tableName,
        Item: {
          model_id: { S: pricing.model_id },
          provider: { S: pricing.provider },
          display_name: { S: pricing.display_name },
          input_price: { S: pricing.input_price },
          output_price: { S: pricing.output_price },
          cache_read_price: { S: pricing.cache_read_price },
          cache_write_price: { S: pricing.cache_write_price },
          status: { S: pricing.status },
        },
        // Only put if item doesn't exist (avoid overwriting)
        ConditionExpression: 'attribute_not_exists(model_id)',
      };

      new cr.AwsCustomResource(this, `SeedPricing${index}`, {
        onCreate: {
          service: 'DynamoDB',
          action: 'putItem',
          parameters: putItemParams,
          physicalResourceId: cr.PhysicalResourceId.of(
            `seed-pricing-${pricing.model_id}`
          ),
          // Ignore ConditionalCheckFailedException (item already exists)
          ignoreErrorCodesMatching: 'ConditionalCheckFailedException',
        },
        policy: cr.AwsCustomResourcePolicy.fromStatements([
          new iam.PolicyStatement({
            actions: ['dynamodb:PutItem'],
            resources: [this.modelPricingTable.tableArn],
          }),
        ]),
        installLatestAwsSdk: false,
      });
    });
  }
}
