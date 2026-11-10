// Offline topology proofs. Run after npm run build:
// node --test test/client-ip.test.js
import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { ContainerImage } from 'aws-cdk-lib/aws-ecs';
import { getConfig } from '../config/config';
import { NetworkStack } from '../lib/network-stack';
import { DynamoDBStack } from '../lib/dynamodb-stack';
import { ECSStack } from '../lib/ecs-stack';

for (const launchType of ['fargate', 'ec2'] as const) {
  for (const enableCloudFront of [false, true]) {
    test(`${launchType}: ${enableCloudFront ? 'CloudFront + ALB' : 'ALB'} trust boundary`, (t) => {
      // Topology tests need no Docker context hashing/staging or image builds.
      t.mock.method(ContainerImage, 'fromAsset', () => ContainerImage.fromRegistry('public.ecr.aws/docker/library/busybox:latest'));
      const app = new cdk.App();
      const config = {
        ...getConfig('dev'), launchType, enableCloudFront,
        adminPortalEnabled: true,
        cloudFrontDomainName: undefined, cloudFrontCertificateArn: undefined,
      };
      const env = { account: '123456789012', region: 'us-east-1' };
      const network = new NetworkStack(app, 'Network', { config, env });
      const tables = new DynamoDBStack(app, 'Tables', { config, env });
      const stack = new ECSStack(app, 'ECS', {
        config, env,
        vpc: network.vpc,
        albSecurityGroup: network.albSecurityGroup,
        ecsSecurityGroup: network.ecsSecurityGroup,
        apiKeysTable: tables.apiKeysTable,
        usageTable: tables.usageTable,
        modelMappingTable: tables.modelMappingTable,
        usageStatsTable: tables.usageStatsTable,
        modelPricingTable: tables.modelPricingTable,
        providerKeysTable: tables.providerKeysTable,
        routingRulesTable: tables.routingRulesTable,
        failoverChainsTable: tables.failoverChainsTable,
        smartRoutingConfigTable: tables.smartRoutingConfigTable,
        providersTable: tables.providersTable,
        betaHeadersTable: tables.betaHeadersTable,
        responseContextTable: tables.responseContextTable,
        speedTestsTable: tables.speedTestsTable,
      });
      const template = Template.fromStack(stack);
      const lbs = Object.values(template.findResources('AWS::ElasticLoadBalancingV2::LoadBalancer'));
      assert.equal(lbs.length, 1);
      const attributes = lbs[0].Properties.LoadBalancerAttributes;
      assert(attributes.some((a: any) => a.Key === 'routing.http.xff_header_processing.mode' && a.Value === 'append'));
      assert(attributes.some((a: any) => a.Key === 'routing.http.xff_client_port.enabled' && a.Value === 'false'));
      // CloudFront mode: internal ALB in private subnets behind a VPC origin.
      const albSubnets = enableCloudFront ? network.vpc.privateSubnets : network.vpc.publicSubnets;
      assert.deepEqual(lbs[0].Properties.Subnets, stack.resolve(albSubnets.map(s => s.subnetId)));
      assert.equal(lbs[0].Properties.Scheme, enableCloudFront ? 'internal' : 'internet-facing');

      const tasks = Object.values(template.findResources('AWS::ECS::TaskDefinition'));
      const containers = tasks.flatMap(task => task.Properties.ContainerDefinitions);
      const proxy = containers.find(container => container.Environment?.some((v: any) => v.Name === 'CLIENT_IP_TRUSTED_PROXY_HOPS'));
      assert(proxy, 'proxy must receive trust config in both launch modes');
      const vars = Object.fromEntries(proxy.Environment.map((v: any) => [v.Name, v.Value]));
      assert.equal(vars.CLIENT_IP_TRUSTED_PROXY_HOPS, enableCloudFront ? '2' : '1');
      assert.deepEqual(vars.CLIENT_IP_TRUSTED_PROXY_CIDRS,
        stack.resolve(albSubnets.map(s => s.ipv4CidrBlock).join(',')));
      assert.equal(proxy.Command, undefined, 'do not override the protected Docker CMD');

      // Provider credentials are encrypted at rest: proxy and admin containers
      // both receive the encryption key from Secrets Manager.
      const withEncKey = containers.filter((c: any) =>
        (c.Secrets ?? []).some((s: any) => s.Name === 'PROVIDER_KEY_ENCRYPTION_SECRET'));
      assert.equal(withEncKey.length, 2, 'proxy + admin get PROVIDER_KEY_ENCRYPTION_SECRET');

      // Bedrock invoke permissions are scoped by resource type, never '*'.
      // CDK may split large role policies into overflow managed policies.
      const statements = [
        ...Object.values(template.findResources('AWS::IAM::Policy')),
        ...Object.values(template.findResources('AWS::IAM::ManagedPolicy')),
      ].flatMap((p: any) => p.Properties.PolicyDocument.Statement);
      const invoke = statements.filter((st: any) =>
        ([] as string[]).concat(st.Action).some(a => a.startsWith('bedrock:Invoke')));
      assert(invoke.length >= 1);
      for (const st of invoke) {
        // CDK renders a single-element Resource list as a scalar.
        const resources = ([] as unknown[]).concat(st.Resource);
        assert(!resources.includes('*'), 'no wildcard resource for bedrock:Invoke*');
      }
      // Runtime Responses (/openai/v1/responses) also authorizes InvokeModel on
      // the account's project (project/default); without it SigV4 calls get 401.
      assert(invoke.some((st: any) =>
        JSON.stringify(st.Resource).includes(':project/*')),
        'task role must allow bedrock:InvokeModel on project/* for Runtime Responses');

      const listeners = Object.values(template.findResources('AWS::ElasticLoadBalancingV2::Listener'));
      assert.equal(listeners.length, 1, 'no alternate unprotected listener');
      const rules = Object.values(template.findResources('AWS::ElasticLoadBalancingV2::ListenerRule'));
      if (enableCloudFront) {
        assert.equal(listeners[0].Properties.DefaultActions[0].Type, 'fixed-response');
        assert.equal(listeners[0].Properties.DefaultActions[0].FixedResponseConfig.StatusCode, '403');
        const distributions = Object.values(template.findResources('AWS::CloudFront::Distribution'));
        const origin = distributions[0].Properties.DistributionConfig.Origins[0];
        assert(origin.VpcOriginConfig, 'CloudFront must reach the ALB through a VPC origin');
        assert.equal(origin.CustomOriginConfig, undefined, 'no public custom (HTTP) origin');
        const vpcOrigins = Object.values(template.findResources('AWS::CloudFront::VpcOrigin'));
        assert.equal(vpcOrigins.length, 1);
        assert.deepEqual(vpcOrigins[0].Properties.VpcOriginEndpointConfig.Arn,
          stack.resolve(stack.alb.loadBalancerArn));
        const header = origin.OriginCustomHeaders
          .find((h: any) => h.HeaderName === 'X-CloudFront-Secret');
        assert(header);
        assert.equal(rules.length, 3, 'API and both admin routes must all be secret protected');
        for (const rule of rules) {
          const secret = rule.Properties.Conditions.find((c: any) => c.Field === 'http-header');
          assert.equal(secret?.HttpHeaderConfig.HttpHeaderName, 'X-CloudFront-Secret');
          assert.deepEqual(secret.HttpHeaderConfig.Values, [header.HeaderValue]);
        }
      } else {
        assert.equal(listeners[0].Properties.DefaultActions[0].Type, 'forward');
        assert.equal(rules.length, 2, 'only admin path rules in direct ALB mode');
      }

      const net = Template.fromStack(network);
      const groups = net.findResources('AWS::EC2::SecurityGroup');
      const ecsId = Object.keys(groups).find(id => id.startsWith('ECSSecurityGroup'))!;
      const albId = Object.keys(groups).find(id => id.startsWith('ALBSecurityGroup'))!;
      // No CIDR-based task ingress, whether CDK renders inline or standalone rules.
      const ingress = [
        ...(groups[ecsId].Properties.SecurityGroupIngress ?? []),
        ...Object.values(net.findResources('AWS::EC2::SecurityGroupIngress'))
          .map(r => r.Properties).filter(p => JSON.stringify(p.GroupId).includes(ecsId)),
      ];
      // Target attachment adds port-specific ALB rules alongside allTcp().
      assert(ingress.length >= 1);
      for (const rule of ingress) {
        assert.deepEqual(rule.SourceSecurityGroupId, { 'Fn::GetAtt': [albId, 'GroupId'] });
        assert.equal(rule.CidrIp, undefined);
        assert.equal(rule.CidrIpv6, undefined);
      }
      // ALB ingress: CloudFront mode admits only the CloudFront origin-facing
      // prefix list on the listener port; never 0.0.0.0/0.
      const albIngress = [
        ...(groups[albId].Properties.SecurityGroupIngress ?? []),
        ...Object.values(net.findResources('AWS::EC2::SecurityGroupIngress'))
          .map(r => r.Properties).filter(p => JSON.stringify(p.GroupId).includes(albId)),
      ];
      if (enableCloudFront) {
        assert.equal(albIngress.length, 1, 'exactly one ALB ingress rule in CloudFront mode');
        assert(albIngress[0].SourcePrefixListId, 'ALB ingress must come from the CloudFront prefix list');
        assert.equal(albIngress[0].CidrIp, undefined);
        assert.equal(albIngress[0].FromPort, 80);
        assert.equal(albIngress[0].ToPort, 80);
      } else {
        assert(albIngress.some(r => r.CidrIp === '0.0.0.0/0' && r.FromPort === 80));
      }

      // Both Fargate task ENIs and EC2 bridge hosts retain the existing ECS SG.
      if (launchType === 'fargate') {
        const services = Object.values(template.findResources('AWS::ECS::Service'));
        assert(services.every(s => s.Properties.NetworkConfiguration.AwsvpcConfiguration.AssignPublicIp === 'DISABLED'));
        assert(services.every(s => JSON.stringify(s.Properties.NetworkConfiguration).includes('ECSSecurityGroup')));
      } else {
        const hosts = Object.values(template.findResources('AWS::EC2::LaunchTemplate'));
        assert(hosts.some(h => JSON.stringify(h.Properties.LaunchTemplateData.SecurityGroupIds).includes('ECSSecurityGroup')));
      }
    });
  }
}
