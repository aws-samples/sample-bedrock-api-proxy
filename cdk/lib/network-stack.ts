import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import { Construct } from 'constructs';
import { EnvironmentConfig } from '../config/config';

export interface NetworkStackProps extends cdk.StackProps {
  config: EnvironmentConfig;
}

export class NetworkStack extends cdk.Stack {
  public readonly vpc: ec2.Vpc;
  public readonly albSecurityGroup: ec2.SecurityGroup;
  public readonly ecsSecurityGroup: ec2.SecurityGroup;

  constructor(scope: Construct, id: string, props: NetworkStackProps) {
    super(scope, id, props);

    const { config } = props;

    // Create VPC with public and private subnets across multiple AZs
    this.vpc = new ec2.Vpc(this, 'VPC', {
      vpcName: `anthropic-proxy-${config.environmentName}-vpc`,
      ipAddresses: ec2.IpAddresses.cidr(config.vpcCidr),
      maxAzs: config.maxAzs,
      natGateways: config.environmentName === 'prod' ? config.maxAzs : 1,
      subnetConfiguration: [
        {
          name: 'Public',
          subnetType: ec2.SubnetType.PUBLIC,
          cidrMask: 24,
        },
        {
          name: 'Private',
          subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
          cidrMask: 24,
        },
      ],
      enableDnsHostnames: true,
      enableDnsSupport: true,
    });

    // Security Group for ALB
    this.albSecurityGroup = new ec2.SecurityGroup(this, 'ALBSecurityGroup', {
      vpc: this.vpc,
      securityGroupName: `anthropic-proxy-${config.environmentName}-alb-sg`,
      description: 'Security group for Application Load Balancer',
      allowAllOutbound: true,
    });

    // Allow HTTP from anywhere
    this.albSecurityGroup.addIngressRule(
      ec2.Peer.anyIpv4(),
      ec2.Port.tcp(80),
      'Allow HTTP from anywhere'
    );

    // Allow HTTPS from anywhere
    this.albSecurityGroup.addIngressRule(
      ec2.Peer.anyIpv4(),
      ec2.Port.tcp(443),
      'Allow HTTPS from anywhere'
    );

    // Security Group for ECS Tasks
    this.ecsSecurityGroup = new ec2.SecurityGroup(this, 'ECSSecurityGroup', {
      vpc: this.vpc,
      securityGroupName: `anthropic-proxy-${config.environmentName}-ecs-sg`,
      description: 'Security group for ECS tasks',
      allowAllOutbound: true,
    });

    // Allow traffic from ALB to ECS
    // Use allTcp() to support both:
    // - Fargate: fixed port (8000)
    // - EC2: dynamic port mapping (ephemeral ports 32768-65535)
    // This is secure because only ALB (with its own restricted SG) can reach ECS
    this.ecsSecurityGroup.addIngressRule(
      this.albSecurityGroup,
      ec2.Port.allTcp(),
      'Allow traffic from ALB'
    );

    // VPC Endpoints for AWS services (cost optimization - no NAT gateway charges for AWS API calls)
    if (config.environmentName === 'prod') {
      // S3 Gateway Endpoint (free)
      this.vpc.addGatewayEndpoint('S3Endpoint', {
        service: ec2.GatewayVpcEndpointAwsService.S3,
      });

      // DynamoDB Gateway Endpoint (free)
      this.vpc.addGatewayEndpoint('DynamoDBEndpoint', {
        service: ec2.GatewayVpcEndpointAwsService.DYNAMODB,
      });

      // Interface endpoints for other services
      this.vpc.addInterfaceEndpoint('EcrDockerEndpoint', {
        service: ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER,
        privateDnsEnabled: true,
      });

      this.vpc.addInterfaceEndpoint('EcrEndpoint', {
        service: ec2.InterfaceVpcEndpointAwsService.ECR,
        privateDnsEnabled: true,
      });

      this.vpc.addInterfaceEndpoint('CloudWatchLogsEndpoint', {
        service: ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS,
        privateDnsEnabled: true,
      });

      // Bedrock Runtime endpoint
      this.vpc.addInterfaceEndpoint('BedrockRuntimeEndpoint', {
        service: new ec2.InterfaceVpcEndpointService(
          `com.amazonaws.${config.region}.bedrock-runtime`,
          443
        ),
        privateDnsEnabled: true,
      });
    }

    // Apply tags
    cdk.Tags.of(this.vpc).add('Environment', config.environmentName);
    Object.entries(config.tags).forEach(([key, value]) => {
      cdk.Tags.of(this.vpc).add(key, value);
    });

    // Outputs - exportName omitted to avoid cross-stack conflicts
    new cdk.CfnOutput(this, 'VpcId', {
      value: this.vpc.vpcId,
      description: 'VPC ID',
    });

    new cdk.CfnOutput(this, 'ALBSecurityGroupId', {
      value: this.albSecurityGroup.securityGroupId,
      description: 'ALB Security Group ID',
    });

    new cdk.CfnOutput(this, 'ECSSecurityGroupId', {
      value: this.ecsSecurityGroup.securityGroupId,
      description: 'ECS Security Group ID',
    });
  }
}
