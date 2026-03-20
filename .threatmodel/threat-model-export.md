# Comprehensive Threat Model Report

**Generated**: 2026-03-19 23:37:05
**Current Phase**: 9 - Output Generation and Documentation
**Overall Completion**: 100.0%

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Business Context](#business-context)
3. [System Architecture](#system-architecture)
4. [Threat Actors](#threat-actors)
5. [Trust Boundaries](#trust-boundaries)
6. [Assets and Flows](#assets-and-flows)
7. [Threats](#threats)
8. [Mitigations](#mitigations)
9. [Assumptions](#assumptions)
10. [Phase Progress](#phase-progress)

## Executive Summary

Anthropic-Bedrock API Proxy — a FastAPI-based translation layer that enables clients using the Anthropic Python SDK to seamlessly access AWS Bedrock models. The service performs bidirectional format conversion between Anthropic's Messages API and AWS Bedrock's Converse/InvokeModel APIs. It includes an admin portal for API key management, usage tracking, pricing, model mapping, routing, and failover configuration. The system supports advanced features including Programmatic Tool Calling (PTC) via Docker sandbox execution, web search/fetch proxy-side tools, multi-provider gateway with smart routing, and OpenAI-compatible API mode. It handles sensitive data including API keys, provider credentials, user prompts/responses, and usage/billing data.

### Key Statistics

- **Total Threats**: 14
- **Total Mitigations**: 14
- **Total Assumptions**: 5
- **System Components**: 10
- **Assets**: 14
- **Threat Actors**: 11

## Business Context

**Description**: Anthropic-Bedrock API Proxy — a FastAPI-based translation layer that enables clients using the Anthropic Python SDK to seamlessly access AWS Bedrock models. The service performs bidirectional format conversion between Anthropic's Messages API and AWS Bedrock's Converse/InvokeModel APIs. It includes an admin portal for API key management, usage tracking, pricing, model mapping, routing, and failover configuration. The system supports advanced features including Programmatic Tool Calling (PTC) via Docker sandbox execution, web search/fetch proxy-side tools, multi-provider gateway with smart routing, and OpenAI-compatible API mode. It handles sensitive data including API keys, provider credentials, user prompts/responses, and usage/billing data.

### Business Features

- **Industry Sector**: Technology
- **Data Sensitivity**: Confidential
- **User Base Size**: Medium
- **Geographic Scope**: Multinational
- **Regulatory Requirements**: None
- **System Criticality**: High
- **Financial Impact**: High
- **Authentication Requirement**: Basic
- **Deployment Environment**: Cloud-Public
- **Integration Complexity**: Complex

## System Architecture

### Components

| ID | Name | Type | Service Provider | Description |
|---|---|---|---|---|
| C001 | Application Load Balancer (ALB) | Network | AWS | Internet-facing Application Load Balancer that routes traffic to ECS tasks. Supports sticky sessions for PTC continuity. |
| C002 | API Proxy Service | Compute | AWS | Main FastAPI application handling Anthropic-compatible API requests. Includes auth middleware, rate limiting, format conversion, and Bedrock integration. |
| C003 | AWS Bedrock Runtime | Compute | AWS | AWS Bedrock Runtime API for model inference. Supports Converse API, InvokeModel API, and streaming variants. |
| C004 | DynamoDB Tables | Storage | AWS | DynamoDB tables for API keys, usage tracking, model mapping, pricing, usage stats, provider keys, routing rules, failover chains, and smart routing config. |
| C005 | AWS Cognito User Pool | Security | AWS | AWS Cognito User Pool for admin portal authentication. JWT-based auth with configurable MFA. |
| C006 | Admin Portal Backend | Compute | AWS | Separate FastAPI application for admin operations: API key CRUD, usage dashboards, pricing management, model mapping, routing rules, and failover configuration. |
| C007 | AWS Secrets Manager | Security | AWS | AWS Secrets Manager storing the master API key and other sensitive configuration. |
| C008 | Admin Portal Frontend | Other | N/A | React SPA frontend for admin portal. Communicates with admin backend via REST API. Uses AWS Amplify for Cognito auth. |
| C009 | PTC Docker Sandbox | Container | N/A | Docker sandbox containers spawned on EC2 hosts for Programmatic Tool Calling code execution. Isolated with no network access and memory limits. |
| C010 | Web Search Provider (Tavily/Brave) | Other | N/A | External search APIs (Tavily or Brave) used by the web search proxy-side tool for agentic web search loops. |

### Connections

| ID | Source | Destination | Protocol | Port | Encrypted | Description |
|---|---|---|---|---|---|---|
| CN001 | C008 | C001 | HTTP | 80 | No | Client API requests to ALB |
| CN002 | C001 | C002 | HTTP | 8000 | No | ALB forwards requests to API Proxy Service |
| CN003 | C002 | C003 | HTTPS | 443 | Yes | API Proxy invokes Bedrock models for inference |
| CN004 | C002 | C004 | HTTPS | 443 | Yes | API Proxy reads/writes API keys, usage, model mappings, provider keys, routing rules, failover chains |
| CN005 | C002 | C010 | HTTPS | 443 | Yes | API Proxy calls external search APIs for web search tool |
| CN006 | C006 | C004 | HTTPS | 443 | Yes | Admin Portal Backend reads/writes DynamoDB tables for admin operations |
| CN007 | C006 | C005 | HTTPS | 443 | Yes | Admin Portal Backend validates JWT tokens with Cognito |
| CN008 | C008 | C006 | HTTP | 8005 | No | Admin Frontend communicates with Admin Backend API |
| CN009 | C002 | C007 | HTTPS | 443 | Yes | API Proxy reads master API key from Secrets Manager |
| CN010 | C002 | C009 | TCP | N/A | No | API Proxy spawns and communicates with PTC sandbox containers via Docker socket (/var/run/docker.sock) |

### Data Stores

| ID | Name | Type | Classification | Encrypted at Rest | Description |
|---|---|---|---|---|---|
| D001 | API Keys Table | NoSQL | Confidential | Yes | Stores API keys, user IDs, rate limits, service tiers, budgets, and metadata. Partition key: api_key. |
| D002 | Usage Table | NoSQL | Internal | Yes | Per-request usage logs with input/output/cache tokens, model, success status. TTL-based cleanup. |
| D003 | Provider Keys Table | NoSQL | Confidential | Yes | Encrypted provider API keys (Fernet AES-128-CBC), provider metadata, model associations. |
| D004 | Configuration Tables | NoSQL | Internal | Yes | Routing rules, failover chains, smart routing config, model mapping, and pricing data. |

## Threat Actors

### Insider

- **Type**: ThreatActorType.INSIDER
- **Capability Level**: CapabilityLevel.MEDIUM
- **Motivations**: Financial, Revenge
- **Resources**: ResourceLevel.LIMITED
- **Relevant**: Yes
- **Priority**: 3/10
- **Description**: An employee or contractor with legitimate access to the system

### External Attacker

- **Type**: ThreatActorType.EXTERNAL
- **Capability Level**: CapabilityLevel.MEDIUM
- **Motivations**: Financial
- **Resources**: ResourceLevel.MODERATE
- **Relevant**: Yes
- **Priority**: 1/10
- **Description**: An external individual or group attempting to gain unauthorized access

### Nation-state Actor

- **Type**: ThreatActorType.NATION_STATE
- **Capability Level**: CapabilityLevel.HIGH
- **Motivations**: Espionage, Political
- **Resources**: ResourceLevel.EXTENSIVE
- **Relevant**: No
- **Priority**: 1/10
- **Description**: A government-sponsored group with advanced capabilities

### Hacktivist

- **Type**: ThreatActorType.HACKTIVIST
- **Capability Level**: CapabilityLevel.MEDIUM
- **Motivations**: Ideology, Political
- **Resources**: ResourceLevel.MODERATE
- **Relevant**: No
- **Priority**: 6/10
- **Description**: An individual or group motivated by ideological or political beliefs

### Organized Crime

- **Type**: ThreatActorType.ORGANIZED_CRIME
- **Capability Level**: CapabilityLevel.HIGH
- **Motivations**: Financial
- **Resources**: ResourceLevel.EXTENSIVE
- **Relevant**: Yes
- **Priority**: 2/10
- **Description**: A criminal organization with significant resources

### Competitor

- **Type**: ThreatActorType.COMPETITOR
- **Capability Level**: CapabilityLevel.MEDIUM
- **Motivations**: Financial, Espionage
- **Resources**: ResourceLevel.MODERATE
- **Relevant**: Yes
- **Priority**: 7/10
- **Description**: A business competitor seeking competitive advantage

### Script Kiddie

- **Type**: ThreatActorType.SCRIPT_KIDDIE
- **Capability Level**: CapabilityLevel.LOW
- **Motivations**: Curiosity, Reputation
- **Resources**: ResourceLevel.LIMITED
- **Relevant**: Yes
- **Priority**: 2/10
- **Description**: An inexperienced attacker using pre-made tools

### Disgruntled Employee

- **Type**: ThreatActorType.DISGRUNTLED_EMPLOYEE
- **Capability Level**: CapabilityLevel.MEDIUM
- **Motivations**: Revenge
- **Resources**: ResourceLevel.LIMITED
- **Relevant**: Yes
- **Priority**: 4/10
- **Description**: A current or former employee with a grievance

### Privileged User

- **Type**: ThreatActorType.PRIVILEGED_USER
- **Capability Level**: CapabilityLevel.HIGH
- **Motivations**: Financial, Accidental
- **Resources**: ResourceLevel.MODERATE
- **Relevant**: Yes
- **Priority**: 4/10
- **Description**: A user with elevated privileges who may abuse them or make mistakes

### Third Party

- **Type**: ThreatActorType.THIRD_PARTY
- **Capability Level**: CapabilityLevel.MEDIUM
- **Motivations**: Financial, Accidental
- **Resources**: ResourceLevel.MODERATE
- **Relevant**: Yes
- **Priority**: 10/10
- **Description**: A vendor, partner, or service provider with access to the system

### Malicious API Consumer

- **Type**: ThreatActorType.EXTERNAL
- **Capability Level**: CapabilityLevel.MEDIUM
- **Motivations**: Financial, Curiosity
- **Resources**: ResourceLevel.LIMITED
- **Relevant**: Yes
- **Priority**: 1/10
- **Description**: A legitimate API consumer who crafts malicious prompts or tool calls to abuse PTC sandbox execution, web search/fetch, or exploit the proxy for unauthorized Bedrock access.

## Trust Boundaries

### Trust Zones

#### Internet

- **Trust Level**: TrustLevel.UNTRUSTED
- **Description**: The public internet, considered untrusted

#### DMZ

- **Trust Level**: TrustLevel.LOW
- **Description**: Demilitarized zone for public-facing services

#### Application

- **Trust Level**: TrustLevel.MEDIUM
- **Description**: Zone containing application servers and services

#### Data

- **Trust Level**: TrustLevel.HIGH
- **Description**: Zone containing databases and data storage

#### Admin

- **Trust Level**: TrustLevel.FULL
- **Description**: Administrative zone with highest privileges

#### Internet / Public Zone

- **Trust Level**: TrustLevel.UNTRUSTED
- **Description**: Public internet-facing components: ALB, client connections

#### Application Zone (ECS)

- **Trust Level**: TrustLevel.MEDIUM
- **Description**: Application services running in ECS: API Proxy, Admin Backend

#### Data Zone (AWS Managed)

- **Trust Level**: TrustLevel.HIGH
- **Description**: DynamoDB tables, Secrets Manager — persistent data stores

#### Sandbox Zone (Docker)

- **Trust Level**: TrustLevel.LOW
- **Description**: PTC Docker sandbox containers with restricted capabilities

#### External Services Zone

- **Trust Level**: TrustLevel.MEDIUM
- **Description**: External third-party services: Bedrock Runtime, Cognito, Tavily/Brave

### Trust Boundaries

#### Internet Boundary

- **Type**: BoundaryType.NETWORK
- **Controls**: Web Application Firewall, DDoS Protection, TLS Encryption
- **Description**: Boundary between the internet and internal systems

#### DMZ Boundary

- **Type**: BoundaryType.NETWORK
- **Controls**: Network Firewall, Intrusion Detection System, API Gateway
- **Description**: Boundary between public-facing services and internal applications

#### Data Boundary

- **Type**: BoundaryType.NETWORK
- **Controls**: Database Firewall, Encryption, Access Control Lists
- **Description**: Boundary protecting data storage systems

#### Admin Boundary

- **Type**: BoundaryType.NETWORK
- **Controls**: Privileged Access Management, Multi-Factor Authentication, Audit Logging
- **Description**: Boundary for administrative access

#### Internet-to-Application Boundary

- **Type**: BoundaryType.NETWORK
- **Controls**: API Key Validation, Rate Limiting, Input Validation (Pydantic)
- **Description**: Boundary between public internet and application services, protected by ALB, API key auth, and rate limiting

#### Application-to-Sandbox Boundary

- **Type**: BoundaryType.PROCESS
- **Controls**: Docker Socket Access, Network Isolation, Memory Limits, Execution Timeout
- **Description**: Boundary between application and PTC sandbox containers, controlled by Docker socket and container isolation

#### Application-to-Data Boundary

- **Type**: BoundaryType.NETWORK
- **Controls**: IAM Task Role, VPC Endpoints, Encryption in Transit
- **Description**: Boundary between application and AWS managed data services (DynamoDB, Secrets Manager)

#### Application-to-External Services Boundary

- **Type**: BoundaryType.NETWORK
- **Controls**: IAM Task Role, TLS Encryption, VPC Endpoints
- **Description**: Boundary between application and external AWS/third-party services (Bedrock, Cognito, search providers)

## Assets and Flows

### Assets

| ID | Name | Type | Classification | Sensitivity | Criticality | Owner |
|---|---|---|---|---|---|---|
| A001 | User Credentials | AssetType.CREDENTIAL | AssetClassification.CONFIDENTIAL | 5 | 5 | N/A |
| A002 | Personal Identifiable Information | AssetType.DATA | AssetClassification.CONFIDENTIAL | 4 | 4 | N/A |
| A003 | Session Token | AssetType.TOKEN | AssetClassification.CONFIDENTIAL | 5 | 5 | N/A |
| A004 | Configuration Data | AssetType.CONFIG | AssetClassification.INTERNAL | 3 | 4 | N/A |
| A005 | Encryption Keys | AssetType.KEY | AssetClassification.RESTRICTED | 5 | 5 | N/A |
| A006 | Public Content | AssetType.DATA | AssetClassification.PUBLIC | 1 | 2 | N/A |
| A007 | Audit Logs | AssetType.DATA | AssetClassification.INTERNAL | 3 | 4 | N/A |
| A008 | API Keys | AssetType.CREDENTIAL | AssetClassification.CONFIDENTIAL | 5 | 5 | N/A |
| A009 | Provider API Keys | AssetType.CREDENTIAL | AssetClassification.CONFIDENTIAL | 5 | 5 | N/A |
| A010 | User Prompts and Model Responses | AssetType.DATA | AssetClassification.CONFIDENTIAL | 4 | 4 | N/A |
| A011 | Usage and Billing Data | AssetType.DATA | AssetClassification.INTERNAL | 3 | 3 | N/A |
| A012 | AWS Credentials and Encryption Secrets | AssetType.CREDENTIAL | AssetClassification.CONFIDENTIAL | 5 | 5 | N/A |
| A013 | Routing and Failover Configuration | AssetType.DATA | AssetClassification.INTERNAL | 3 | 4 | N/A |
| A014 | PTC Code Execution Data | AssetType.DATA | AssetClassification.CONFIDENTIAL | 4 | 3 | N/A |

### Asset Flows

| ID | Asset | Source | Destination | Protocol | Encrypted | Risk Level |
|---|---|---|---|---|---|---|
| F001 | User Credentials | C001 | C002 | HTTPS | Yes | 4 |
| F002 | Session Token | C002 | C001 | HTTPS | Yes | 3 |
| F003 | Personal Identifiable Information | C003 | C004 | TLS | Yes | 3 |
| F004 | Audit Logs | C003 | C005 | TLS | Yes | 2 |
| F005 | API Keys | C001 | C002 | HTTP | No | 4 |
| F006 | User Prompts and Model Responses | C002 | C003 | HTTPS | Yes | 3 |
| F007 | Provider API Keys | C004 | C002 | HTTPS | Yes | 4 |
| F008 | PTC Code Execution Data | C002 | C009 | TCP | No | 5 |

## Threats

### Identified Threats

#### T1: External Attacker

**Statement**: A External Attacker with network access to the ALB can intercept API keys transmitted over unencrypted HTTP between ALB and ECS tasks, which leads to exposure of API keys enabling unauthorized access to the proxy and Bedrock models

- **Prerequisites**: with network access to the ALB
- **Action**: intercept API keys transmitted over unencrypted HTTP between ALB and ECS tasks
- **Impact**: exposure of API keys enabling unauthorized access to the proxy and Bedrock models
- **Impacted Assets**: A008
- **Tags**: STRIDE-I, Network, API-Key

#### T2: External Attacker

**Statement**: A External Attacker with a stolen or brute-forced API key can impersonate a legitimate user by replaying a stolen API key, which leads to unauthorized access to Bedrock models, usage charged to victim's account, budget exhaustion

- **Prerequisites**: with a stolen or brute-forced API key
- **Action**: impersonate a legitimate user by replaying a stolen API key
- **Impact**: unauthorized access to Bedrock models, usage charged to victim's account, budget exhaustion
- **Impacted Assets**: A008
- **Tags**: STRIDE-S, Authentication

#### T3: External Attacker

**Statement**: A External Attacker with knowledge of the master API key can use the master API key to bypass all rate limiting and access controls, which leads to unrestricted access to all proxy functionality, no rate limiting, full admin capabilities

- **Prerequisites**: with knowledge of the master API key
- **Action**: use the master API key to bypass all rate limiting and access controls
- **Impact**: unrestricted access to all proxy functionality, no rate limiting, full admin capabilities
- **Impacted Assets**: A008
- **Tags**: STRIDE-S, Authentication, Master-Key

#### T4: Malicious API Consumer

**Statement**: A Malicious API Consumer with ability to submit code for PTC execution can escape the Docker sandbox container to gain access to the host EC2 instance via Docker socket exploitation, which leads to full host compromise, access to other containers, AWS credentials, and DynamoDB data

- **Prerequisites**: with ability to submit code for PTC execution
- **Action**: escape the Docker sandbox container to gain access to the host EC2 instance via Docker socket exploitation
- **Impact**: full host compromise, access to other containers, AWS credentials, and DynamoDB data
- **Impacted Assets**: A014
- **Tags**: STRIDE-E, Container-Escape, PTC

#### T5: External Attacker

**Statement**: A External Attacker with network access to the public ALB can flood the proxy with requests to exhaust rate limits, Bedrock quotas, or DynamoDB capacity, which leads to service unavailability for legitimate users, increased AWS costs, Bedrock throttling

- **Prerequisites**: with network access to the public ALB
- **Action**: flood the proxy with requests to exhaust rate limits, Bedrock quotas, or DynamoDB capacity
- **Impact**: service unavailability for legitimate users, increased AWS costs, Bedrock throttling
- **Tags**: STRIDE-D, DDoS, Availability

#### T6: Insider

**Statement**: A Insider with access to application logs or monitoring systems can extract sensitive user prompts, model responses, or API keys from application logs or debug output, which leads to exposure of confidential user data, prompt content, and potentially API credentials

- **Prerequisites**: with access to application logs or monitoring systems
- **Action**: extract sensitive user prompts, model responses, or API keys from application logs or debug output
- **Impact**: exposure of confidential user data, prompt content, and potentially API credentials
- **Impacted Assets**: A010
- **Tags**: STRIDE-I, Logging, Data-Leakage

#### T7: Insider

**Statement**: A Insider with access to DynamoDB and knowledge of the Fernet encryption secret can decrypt provider API keys stored in DynamoDB by obtaining the Fernet encryption secret from environment variables, which leads to exposure of all third-party provider API keys, enabling unauthorized use of Bedrock, Tavily, Brave, and OpenAI services

- **Prerequisites**: with access to DynamoDB and knowledge of the Fernet encryption secret
- **Action**: decrypt provider API keys stored in DynamoDB by obtaining the Fernet encryption secret from environment variables
- **Impact**: exposure of all third-party provider API keys, enabling unauthorized use of Bedrock, Tavily, Brave, and OpenAI services
- **Impacted Assets**: A009
- **Tags**: STRIDE-I, Encryption, Provider-Keys

#### T8: External Attacker

**Statement**: A External Attacker with access to the admin portal (unauthenticated if Cognito not configured) can modify routing rules, failover chains, or model mappings to redirect traffic to attacker-controlled endpoints or models, which leads to traffic redirection, data exfiltration via malicious model endpoints, service disruption

- **Prerequisites**: with access to the admin portal (unauthenticated if Cognito not configured)
- **Action**: modify routing rules, failover chains, or model mappings to redirect traffic to attacker-controlled endpoints or models
- **Impact**: traffic redirection, data exfiltration via malicious model endpoints, service disruption
- **Impacted Assets**: A013
- **Tags**: STRIDE-T, Admin-Portal, Configuration

#### T9: Malicious API Consumer

**Statement**: A Malicious API Consumer with a valid API key can exploit budget rollover logic or race conditions to bypass monthly budget limits and consume resources beyond allocated budget, which leads to financial loss from excessive Bedrock usage, budget tracking integrity compromise

- **Prerequisites**: with a valid API key
- **Action**: exploit budget rollover logic or race conditions to bypass monthly budget limits and consume resources beyond allocated budget
- **Impact**: financial loss from excessive Bedrock usage, budget tracking integrity compromise
- **Impacted Assets**: A011
- **Tags**: STRIDE-T, Budget, Billing

#### T10: External Attacker

**Statement**: A External Attacker when Cognito is not configured in the deployment can access the admin portal without authentication due to development mode fallback when Cognito is not configured, which leads to full admin access to API key management, pricing, routing, and failover configuration without any authentication

- **Prerequisites**: when Cognito is not configured in the deployment
- **Action**: access the admin portal without authentication due to development mode fallback when Cognito is not configured
- **Impact**: full admin access to API key management, pricing, routing, and failover configuration without any authentication
- **Tags**: STRIDE-S, Admin-Portal, Authentication-Bypass

#### T11: Insider

**Statement**: A Insider with access to environment variables or container configuration can extract AWS credentials, master API key, or encryption secrets from environment variables or .env files, which leads to full AWS account compromise, access to all DynamoDB data, Bedrock, and Secrets Manager

- **Prerequisites**: with access to environment variables or container configuration
- **Action**: extract AWS credentials, master API key, or encryption secrets from environment variables or .env files
- **Impact**: full AWS account compromise, access to all DynamoDB data, Bedrock, and Secrets Manager
- **Impacted Assets**: A012
- **Tags**: STRIDE-I, Credentials, Environment

#### T12: Malicious API Consumer

**Statement**: A Malicious API Consumer with a valid API key and PTC enabled can spawn excessive PTC sandbox containers to exhaust host resources (memory, CPU, disk), which leads to host resource exhaustion, service degradation for all users, potential container runtime failure

- **Prerequisites**: with a valid API key and PTC enabled
- **Action**: spawn excessive PTC sandbox containers to exhaust host resources (memory, CPU, disk)
- **Impact**: host resource exhaustion, service degradation for all users, potential container runtime failure
- **Tags**: STRIDE-D, PTC, Resource-Exhaustion

#### T13: Malicious API Consumer

**Statement**: A Malicious API Consumer with a valid API key and web search/fetch enabled can abuse web fetch tool to perform server-side request forgery (SSRF) against internal AWS metadata endpoints or VPC resources, which leads to exposure of EC2 instance metadata, IAM credentials, or internal service data

- **Prerequisites**: with a valid API key and web search/fetch enabled
- **Action**: abuse web fetch tool to perform server-side request forgery (SSRF) against internal AWS metadata endpoints or VPC resources
- **Impact**: exposure of EC2 instance metadata, IAM credentials, or internal service data
- **Impacted Assets**: A010
- **Tags**: STRIDE-I, SSRF, Web-Search

#### T14: Malicious API Consumer

**Statement**: A Malicious API Consumer with a valid API key can deny having made specific API requests due to insufficient audit logging or shared API keys, which leads to inability to attribute actions to specific users, compliance issues, dispute resolution failures

- **Prerequisites**: with a valid API key
- **Action**: deny having made specific API requests due to insufficient audit logging or shared API keys
- **Impact**: inability to attribute actions to specific users, compliance issues, dispute resolution failures
- **Tags**: STRIDE-R, Logging, Audit

## Mitigations

### Identified Mitigations

#### M1: Enforce HTTPS/TLS termination at ALB and enable TLS between ALB and ECS tasks. Configure ALB to redirect HTTP to HTTPS. Use ACM certificates.

**Addresses Threats**: T1

#### M2: Implement API key rotation policy, add key expiration dates, and support key revocation. Add anomaly detection for unusual usage patterns per key.

**Addresses Threats**: T2

#### M4: Run PTC sandbox containers with --no-new-privileges, drop all capabilities, use read-only root filesystem, and avoid mounting Docker socket directly. Use a container orchestration sidecar or gVisor/Firecracker for stronger isolation.

**Addresses Threats**: T4

#### M5: Deploy AWS WAF with rate-based rules, AWS Shield for DDoS protection, and implement IP-based throttling at ALB level. Configure auto-scaling policies for ECS tasks.

**Addresses Threats**: T5

#### M7: Store Fernet encryption secret in AWS Secrets Manager instead of environment variables. Use AWS KMS for key wrapping. Implement secret rotation and access auditing via CloudTrail.

**Addresses Threats**: T7

#### M8: Remove development mode fallback from CognitoAuthMiddleware. Require Cognito configuration for all deployments. Add startup validation that fails if COGNITO_USER_POOL_ID and COGNITO_CLIENT_ID are not set.

**Addresses Threats**: T10

#### M9: Implement URL allowlist/blocklist for web fetch tool. Block requests to RFC 1918 private IP ranges, link-local addresses (169.254.x.x), and AWS metadata endpoint (169.254.169.254). Use IMDSv2 with hop limit of 1.

**Addresses Threats**: T13

#### M10: Implement per-user concurrent sandbox limits, global sandbox count limits, and disk usage quotas. Add container cleanup on timeout. Monitor container count via CloudWatch metrics.

**Addresses Threats**: T12

#### M11: Implement input validation for routing rules and model mappings in admin portal. Restrict target models/endpoints to an allowlist. Add change audit logging for all configuration modifications.

**Addresses Threats**: T8

#### M12: Implement atomic budget updates using DynamoDB conditional writes to prevent race conditions. Add server-side budget enforcement before Bedrock calls. Log budget threshold alerts.

**Addresses Threats**: T9

### In Progress Mitigations

#### M3: Store master API key exclusively in AWS Secrets Manager with automatic rotation. Restrict access via IAM policies. Never pass as plain environment variable.

**Addresses Threats**: T3

#### M6: Implement structured logging with PII/prompt redaction. Mask API keys in all log output (already partially implemented). Disable debug logging in production. Use log levels appropriately.

**Addresses Threats**: T6

#### M13: Implement comprehensive audit logging with request IDs, timestamps, API key hashes, user IDs, and action details. Store audit logs in a tamper-evident manner (CloudWatch Logs with retention policy).

**Addresses Threats**: T14

#### M14: Use IAM task roles exclusively for ECS deployments instead of static AWS credentials. Remove AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY from environment variables in production. Use ECS task role with least-privilege IAM policies.

**Addresses Threats**: T11

## Assumptions

### A001: Authentication

**Description**: API key authentication is the primary auth mechanism for the proxy API. Master API key provides admin-level access with no rate limiting.

- **Impact**: Compromise of master API key grants unrestricted access to all proxy functionality
- **Rationale**: The system uses x-api-key header-based auth validated against DynamoDB, with a master key bypass for admin operations

### A002: Network

**Description**: The ALB is internet-facing and accepts HTTP/HTTPS traffic from any IP address. No WAF or IP allowlisting is configured by default.

- **Impact**: The proxy is exposed to the public internet, increasing the attack surface for DDoS, brute force, and reconnaissance attacks
- **Rationale**: The CDK network stack configures ALB security group to allow inbound from 0.0.0.0/0 on ports 80 and 443

### A003: AWS Services

**Description**: AWS credentials (access key, secret key, session token) may be passed via environment variables or IAM task roles depending on deployment mode.

- **Impact**: Credential exposure through environment variables or container compromise could grant access to Bedrock and DynamoDB
- **Rationale**: The config.py accepts AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, and AWS_SESSION_TOKEN as environment variables, though ECS task roles are preferred in production

### A004: Authentication

**Description**: The admin portal falls back to development mode (no authentication) when Cognito is not configured, granting full admin access.

- **Impact**: If deployed without Cognito configuration, the admin portal is completely unauthenticated
- **Rationale**: CognitoAuthMiddleware checks is_configured and allows all requests with a dev-user identity when Cognito env vars are empty

### A005: Network

**Description**: PTC Docker sandbox containers are created on the same EC2 host via Docker socket mount, with network disabled by default and memory limits enforced.

- **Impact**: Docker socket access from the API container is a privileged operation; container escape could compromise the host
- **Rationale**: PTC requires mounting /var/run/docker.sock into the API container to spawn sandbox containers for code execution

## Phase Progress

| Phase | Name | Completion |
|---|---|---|
| 1 | Business Context Analysis | 100% ✅ |
| 2 | Architecture Analysis | 100% ✅ |
| 3 | Threat Actor Analysis | 100% ✅ |
| 4 | Trust Boundary Analysis | 100% ✅ |
| 5 | Asset Flow Analysis | 100% ✅ |
| 6 | Threat Identification | 100% ✅ |
| 7 | Mitigation Planning | 100% ✅ |
| 7.5 | Code Validation Analysis | 100% ✅ |
| 8 | Residual Risk Analysis | 100% ✅ |
| 9 | Output Generation and Documentation | 100% ✅ |

---

*This threat model report was generated automatically by the Threat Modeling MCP Server.*
