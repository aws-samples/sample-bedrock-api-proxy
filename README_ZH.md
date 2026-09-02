<div align="center">

# 🔄 Bedrock API Proxy

**零代码迁移，让 Claude Code/Codex无缝对接 AWS Bedrock**

[![License](https://img.shields.io/badge/license-MIT--0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12+-green.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688.svg)](https://fastapi.tiangolo.com)
[![AWS](https://img.shields.io/badge/AWS-Bedrock-FF9900.svg)](https://aws.amazon.com/bedrock/)

<p>
  <a href="./README_ZH.md"><img src="https://img.shields.io/badge/文档-中文-red.svg" alt="中文文档"></a>
  <a href="./README.md"><img src="https://img.shields.io/badge/Docs-English-blue.svg" alt="English Docs"></a>
  <a href="./cdk/DEPLOYMENT.md"><img src="https://img.shields.io/badge/🚀-部署指南-orange.svg" alt="部署指南"></a>
  <a href="https://aws.amazon.com/cn/blogs/china/programmatic-tool-calling-agent-using-bedrock-and-ecs-docker-sandbox/"><img src="https://img.shields.io/badge/📝-AWS_Blog_1-FF9900.svg" alt="AWS Blog-1 PTC"></a>
  <a href="https://aws.amazon.com/cn/blogs/china/based-on-amazon-bedrock-implement-dynamic-filtering-web-search-web-fetch/"><img src="https://img.shields.io/badge/📝-AWS_Blog_2-FF9900.svg" alt="AWS Blog-2 Web Search"></a>
</p>

---

</div>

## 项目简介

> ⚠️ **免责声明**：本项目仅作为示例代码，用于演示和学习目的，**不适用于生产环境**。请在部署到任何生产环境之前，自行进行充分的安全审查、测试和加固。

是一个轻量级的 API 转换服务，让你无需修改代码即可在 Anthropic SDK 中使用 AWS Bedrock 上的各种大语言模型，并提供Anthopic兼容的Code Execution/Dynamic Web Search/ PTC等服务端功能。 主要为Claude Code/Claude Agent SDK提供Proxy转接，并带可视化管理web实现api key分发，用量监控，限额管理等管理功能。现已经全面支持GPT on Bedrock，为Codex提供转接服务。

> 📝 **亚马逊云科技 全球英文博客**：[Implementing programmatic tool calling on Amazon Bedrock](https://aws.amazon.com/blogs/machine-learning/implementing-programmatic-tool-calling-on-amazon-bedrock)
>
> 📝 **亚马逊云科技 官方中文博客**：[基于 Amazon Bedrock 与自建 ECS Docker Sandbox 实现 Agent 编程式工具调用](https://aws.amazon.com/cn/blogs/china/programmatic-tool-calling-agent-using-bedrock-and-ecs-docker-sandbox/)
>
> 📝 **亚马逊云科技 官方中文博客**：[基于 Amazon Bedrock 实现动态过滤 Web Search 与 Web Fetch](https://aws.amazon.com/cn/blogs/china/based-on-amazon-bedrock-implement-dynamic-filtering-web-search-web-fetch/)



**核心优势：**
- 🔄 **零代码迁移** - 完全兼容 Anthropic API，无需修改现有代码
- 🚀 **开箱即用** - 支持流式/非流式响应、工具调用、多模态等所有高级特性
- 🤖 **Programmatic Tool Calling** - 业界首个在 Bedrock 上实现 Anthropic 兼容 PTC API 的代理服务
- 🔍 **Dynamic Web Search** - 支持 `web_search_20250305` / `web_search_20260209`，Claude 可动态编写代码过滤搜索结果
- 🌐 **Web Fetch** - 支持 `web_fetch_20250910` / `web_fetch_20260209`，无需额外 API Key 即可抓取网页与 PDF
- 🧠 **GPT 模型代理** - OpenAI Responses API 与 Chat Completions API 透传，支持代理端 web search
- 💰 **成本优化** - 灵活使用 Bedrock 上的开源模型，显著降低推理成本
- 🔐 **企业级** - 内置 API 密钥管理、速率限制、使用追踪和监控
- 🔒 **HTTPS 加密** - 内置 CloudFront HTTPS 终端，无需自定义域名
- ☁️ **云原生** - 一键部署到 AWS ECS，自动扩展，高可用架构

**典型应用：** 在 **Claude Code** 中使用 Qwen3-Coder-480B 进行代码生成，或在 **Claude Agent SDK** 构建的应用中混合使用不同模型以平衡性能和成本。

## 功能特性

### 核心功能
- 完全兼容 Anthropic Messages API，双向格式转换
- 流式 (SSE) 和非流式响应
- 工具使用（函数调用）格式转换
- 扩展思考 (Extended Thinking) 支持
- 多模态内容（文本、图像、文档）

### 高级功能
- **Programmatic Tool Calling (PTC)**：Claude 在 Docker Sandbox 中生成并执行 Python 代码调用工具。支持多轮执行、`asyncio.gather` 并行调用、会话复用。
- **Web 搜索**：代理端 `web_search_20250305`/`web_search_20260209`，支持 Tavily，Brave 或Bedrock AgentCore Gateway WebSearch。域名过滤、搜索次数限制、用户位置。动态过滤版本需要 Docker。
- **[AgentCore Search MCP Server](agentcore-search-mcp/README_ZH.md)**：独立的 MCP server（[PyPI](https://pypi.org/project/agentcore-search-mcp/)：`uvx agentcore-search-mcp`），把 Amazon Bedrock AgentCore Gateway WebSearch 暴露给任意 MCP 客户端（Claude Code、Codex、Cursor）——本地 stdio 桥，补上 gateway 要求的 SigV4 签名。与代理本体相互独立，自带 gateway 一键部署脚本。
- **Web 抓取**：代理端 `web_fetch_20250910`/`web_fetch_20260209`，使用 httpx（无需 API Key）。支持 PDF。动态过滤版本需要 Docker。
- **提示词缓存 TTL**：扩展 `cache_control` 支持 1 小时 TTL。三级优先级：API Key → 请求 → 环境变量默认值。
- **Beta Header 映射**：自动将 Anthropic beta headers 映射到 Bedrock beta headers。
- **工具输入示例**：`input_examples` 参数帮助模型理解工具用法。
- **OpenAI 兼容 API**：非 Claude 模型可通过 Bedrock Mantle 使用 OpenAI Chat Completions API。自动映射 `thinking` → `reasoning`。
- **OpenAI Passthrough**：`/openai/v1/*` 端点转发 OpenAI SDK 请求到 Bedrock Mantle。支持 Responses API web search 与有状态 `previous_response_id`。
- **服务层级**：每个 API Key 可配置 Bedrock 服务层级（`default`/`flex`/`priority`/`reserved`），支持自动回退。

### 基础设施
- 基于 DynamoDB 的 API 密钥认证
- 每个 API Key 的令牌桶速率限制
- 使用量追踪与分析
- [OpenTelemetry 分布式追踪](docs/otel-tracing.md)（Langfuse、Jaeger、Grafana Tempo）
- [Admin Portal](admin_portal/) 管理界面（Cognito 认证，密钥/用量/定价管理）
- [CloudFront HTTPS](docs/cloudfront.md) 加密（可选）

### 支持的模型
- Claude 4.5/4.6/4.7/4.8、Claude 4.5 Haiku
- GPT-5.4/5.5
- Qwen3-coder-480b、Qwen3-235b-instruct
- Kimi 2.5、MiniMax 2.5、GLM 4.7/5
- 任何支持 Converse API 或 OpenAI Chat Completions API 的 Bedrock 模型
- 支持 Bedrock **应用推理配置 (inference profile) ARN**

可在 Admin Portal 中创建模型 ID 别名映射，或直接使用 ARN。

![模型映射](./assets/screenshot-20260420-183419.png)

## 快速开始

### Claude Code 配置

#### 1. 创建 `~/.claude.json`
```json
{
  "hasCompletedOnboarding": true
}
```

#### 2. 创建 `~/.claude/settings.json`
```json
{
  "env": {
    "ANTHROPIC_API_KEY": "your_api_key",
    "ANTHROPIC_BASE_URL": "https://your-proxy-url"
  }
}
```

使用非 Claude 模型时，添加模型环境变量：
```json
{
  "env": {
    "ANTHROPIC_API_KEY": "your_api_key",
    "ANTHROPIC_BASE_URL": "https://your-proxy-url",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "mooonshotai.kimi-k2.5",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "mooonshotai.kimi-k2.5",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "mooonshotai.kimi-k2.5"
  }
}
```

> **说明**：Claude Code/Agent SDK 会识别直连 Bedrock 并丢弃 beta headers。本代理通过伪装连接来保留官方 API 行为。

### Claude Agent SDK

配置方式相同。Dockerfile 示例参见 [AgentCore Demo](https://github.com/xiehust/agentcore_demo/tree/main/00-claudecode_agent)。

## 部署方式

### 方式一：AWS ECS（推荐）

| 特性 | Fargate（默认） | EC2 |
|------|----------------|-----|
| **PTC 支持** | 否 | 是 |
| **管理方式** | 无服务器 | 需管理 ASG |
| **Docker 访问** | 否 | 是（挂载 socket） |
| **适用场景** | 标准 API 代理 | PTC / Web Search 动态过滤 |

```bash
cd cdk && npm install

# Fargate（ARM64）
./scripts/deploy.sh -e prod -r us-west-2 -p arm64

# EC2（启用 PTC + 动态过滤）
./scripts/deploy.sh -e prod -r us-west-2 -p arm64 -l ec2

# 启用所有功能
ENABLE_CLOUDFRONT=true \
ENABLE_WEB_SEARCH=true \
WEB_SEARCH_PROVIDER=tavily \
WEB_SEARCH_API_KEY=tvly-your-key \
ENABLE_OPENAI_COMPAT=true \
BEDROCK_API_KEY=your-bedrock-key \
MANTLE_ENDPOINT_URL=https://bedrock-mantle.us-east-2.api.aws/openai/v1 \
./scripts/deploy.sh -e prod -r us-west-2 -p arm64 -l ec2
```

部署约需 15-20 分钟。完整说明参见 [CDK 部署指南](cdk/DEPLOYMENT.md)。如使用 AgentCore web search，请先在 `us-east-1` 运行 `AWS_REGION=us-east-1 uv run bash scripts/create_agentcore.sh`，然后用 `WEB_SEARCH_PROVIDER=agentcore` 和 `AGENTCORE_GATEWAY_URL=<gateway-mcp-url>` 部署，不需要 `WEB_SEARCH_API_KEY`。

#### 部署后操作

```bash
# 创建管理员账户
./scripts/create-admin-user.sh -e prod -r us-west-2 --email admin@example.com
```

访问 https://xxx.cloudfront.net/admin/ Admin portal 去配置API Key

### 方式二：本地开发

```bash
# 安装（model-mappings/ 子模块保存默认模型映射的离线快照）
git clone --recurse-submodules https://github.com/xiehust/sample-bedrock-api-proxy.git
cd sample-bedrock-api-proxy   # 已经 clone 过？执行：git submodule update --init
pip install uv && uv sync
cp env.example .env  # 配置环境变量

# 初始化 DynamoDB 表并创建 API Key
uv run scripts/setup_tables.py
uv run scripts/create_api_key.py --user-id dev-user --name "Dev Key"

# 启动
uv run uvicorn app.main:app --reload --port 8000
```

## API 使用

### Anthropic SDK

```python
from anthropic import Anthropic

client = Anthropic(
    api_key="sk-your-api-key",
    base_url="http://localhost:8000"
)

# 非流式
message = client.messages.create(
    model="claude-opus-4-7",
    max_tokens=1024,
    messages=[{"role": "user", "content": "你好！"}]
)
print(message.content[0].text)

# 流式
with client.messages.stream(
    model="claude-opus-4-7",
    max_tokens=1024,
    messages=[{"role": "user", "content": "讲个故事"}]
) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)
```

### curl

```bash
# 非流式
curl http://localhost:8000/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: sk-xxx" \
  -d '{"model": "claude-sonnet-4-5-20250929", "max_tokens": 1024, "messages": [{"role": "user", "content": "你好！"}]}'

# 流式
curl http://localhost:8000/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: sk-xxx" \
  -d '{"model": "claude-sonnet-4-5-20250929", "max_tokens": 1024, "stream": true, "messages": [{"role": "user", "content": "你好！"}]}'

# 列出模型
curl http://localhost:8000/v1/models -H "x-api-key: sk-xxx"
```

### OpenAI SDK（`/openai/v1`）

需要在代理端启用 `ENABLE_OPENAI_PASSTHROUGH=True`。将 OpenAI SDK 的 `base_url` 指向 `<代理地址>/openai/v1`，并使用 **代理的 API Key**——代理会自动注入上游 Bedrock 凭证。Bedrock 原生模型 ID（如 `openai.gpt-oss-120b`）直接透传；Anthropic 风格的别名通过模型映射表解析。

#### Codex CLI / IDE

Codex 可以把该代理配置为自定义 Responses API 模型提供方。请把 provider 配置写在用户级 `~/.codex/config.toml` 中；Codex 会忽略项目本地 `.codex/config.toml` 里的模型 provider 配置。

```toml
model_provider = "bedrock-proxy"
model = "openai.gpt-5.5"
model_reasoning_effort = "high"

# 如果代理未配置 Tavily/Brave web search provider，建议禁用 Codex web search。
# Codex 默认的 cached web search 会发送 external_web_access=false，当前代理不支持该参数。
# 如果代理配置了 Tavily/Brave web search provider，则设置成 "live" 开启
web_search = "disabled"

[model_providers.bedrock-proxy]
name = "Bedrock API Proxy"
base_url = "https://your-proxy.example.com/openai/v1"
env_key = "OPENAI_API_KEY"
wire_api = "responses"
```

将 `OPENAI_API_KEY` 设置为代理 API Key，而不是 Bedrock API Key：

```bash
export OPENAI_API_KEY="sk-your-proxy-api-key"
```

如果希望 Codex 通过代理执行 web search，请先在代理服务端配置 `ENABLE_WEB_SEARCH=True` 以及 `WEB_SEARCH_PROVIDER`/`WEB_SEARCH_API_KEY`，然后设置：

```toml
web_search = "live"
```

#### Chat Completions API

```python
from openai import OpenAI

client = OpenAI(
    api_key="sk-your-api-key",
    base_url="http://localhost:8000/openai/v1",
)

# 非流式
resp = client.chat.completions.create(
    model="openai.gpt-oss-120b",
    messages=[{"role": "user", "content": "你好！"}],
)
print(resp.choices[0].message.content)

# 流式 — 通过 stream_options 捕获 usage
stream = client.chat.completions.create(
    model="openai.gpt-oss-120b",
    messages=[{"role": "user", "content": "讲一个故事"}],
    stream=True,
    stream_options={"include_usage": True},
)
for chunk in stream:
    if chunk.choices and chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
```

#### Responses API

支持通过 `previous_response_id` 进行有状态对话串联，以及代理托管的 `web_search` 工具调用。

```python
from openai import OpenAI

client = OpenAI(
    api_key="sk-your-api-key",
    base_url="http://localhost:8000/openai/v1",
)

# 基础调用
resp = client.responses.create(
    model="openai.gpt-oss-120b",
    input="法国的首都是哪里？",
)
print(resp.output_text)

# 通过 previous_response_id 实现有状态续问
followup = client.responses.create(
    model="openai.gpt-oss-120b",
    input="它的人口是多少？",
    previous_response_id=resp.id,
)
print(followup.output_text)

# 流式
stream = client.responses.create(
    model="openai.gpt-oss-120b",
    input="写一首关于 Bedrock 的俳句",
    stream=True,
)
for event in stream:
    if event.type == "response.output_text.delta":
        print(event.delta, end="", flush=True)

# Web 搜索（代理托管，使用 Tavily/Brave）
resp = client.responses.create(
    model="openai.gpt-oss-120b",
    input="本周有哪些重要的 AI 发布？",
    tools=[{"type": "web_search"}],
)
print(resp.output_text)
```

## 架构

```
+----------------------------------------------------------+
|              客户端应用                                    |
|           (Anthropic Python SDK)                         |
+---------------------------+------------------------------+
                            |
                            | HTTP/HTTPS (Anthropic 格式)
                            v
+----------------------------------------------------------+
|          FastAPI API 代理服务                              |
|                                                          |
|  +----------+  +-----------+  +----------------+         |
|  |   认证   |  |   速率    |  |    格式        |         |
|  |  中间件  |->|   限制    |->|    转换        |         |
|  +----------+  +-----------+  +----------------+         |
+-------+---------------+---------------+------------------+
        |               |               |
        v               v               v
  +----------+    +----------+    +----------+
  | DynamoDB |    |   AWS    |    |CloudWatch|
  |          |    | Bedrock  |    |   日志/  |
  | API Keys |    | Runtime  |    |   指标   |
  |  用量    |    | Converse |    |          |
  +----------+    +----------+    +----------+
```

### 路由逻辑
- 模型包含 "anthropic" 或 "claude" → **InvokeModel API**（原生格式）
- `ENABLE_OPENAI_COMPAT=true` → **OpenAI Chat Completions**（通过 bedrock-mantle）
- 其他 → **Converse API**（统一 Bedrock API）
- `/openai/v1/*` → **OpenAI Passthrough**（独立路由）

### ECS 生产架构

![ECS 架构](assets/ecs-architecture.png)

| 组件 | 说明 |
|------|------|
| **VPC** | 多可用区，公有/私有子网 |
| **ALB** | 接收外部 HTTP/HTTPS 流量 |
| **ECS 集群** | Fargate 或 EC2，位于私有子网 |
| **CloudFront** | 可选 HTTPS 终端 |
| **DynamoDB** | API Keys、Usage、Model Mapping（按需计费） |
| **Auto Scaling** | 基于 CPU/内存（最小 2，最大 10） |

## 文档索引

| 文档 | 说明 |
|------|------|
| [配置参考](docs/configuration.md) | 所有环境变量和设置 |
| [CDK 部署指南](cdk/DEPLOYMENT.md) | 完整 ECS 部署说明 |
| [CloudFront HTTPS](docs/cloudfront.md) | HTTPS 加密设置 |
| [OpenTelemetry 追踪](docs/otel-tracing.md) | Langfuse/Jaeger LLM 可观测性 |
| [服务层级](docs/service-tier.md) | 成本/延迟层级配置 |
| [架构详情](docs/architecture/detailed-flows.md) | 转换流程、流式传输、DynamoDB Schema |
| [功能详情](docs/architecture/features.md) | 各功能详细文档 |
| [故障排除](docs/troubleshooting.md) | 常见错误与调试 |
| [模型映射](docs/MODEL_MAPPING.md) | 模型 ID 映射参考 |
| [AgentCore Search MCP Server](agentcore-search-mcp/README_ZH.md) | AgentCore Gateway WebSearch 独立 MCP server（[English](agentcore-search-mcp/README.md)，[Agent 安装步骤](agentcore-search-mcp/INSTALL_FOR_AGENTS.md)） |

## 安全

### 最佳实践
- 使用环境变量或 Secrets Manager 管理 API 密钥
- 在 AWS 上使用 IAM 角色（ECS 任务角色）
- 启用 CloudFront 进行 HTTPS 加密
- 为每个 API Key 配置速率限制
- 生产环境使用 VPC 端点访问 AWS 服务

### 所需 IAM 权限

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
        "bedrock:ListFoundationModels",
        "bedrock:GetFoundationModel"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "dynamodb:PutItem",
        "dynamodb:GetItem",
        "dynamodb:UpdateItem",
        "dynamodb:Query",
        "dynamodb:Scan",
        "dynamodb:DeleteItem"
      ],
      "Resource": ["arn:aws:dynamodb:*:*:table/anthropic-proxy-*"]
    }
  ]
}
```

## 开发

```bash
# 测试
uv run pytest                              # 全部测试
uv run pytest --cov=app --cov-report=html  # 覆盖率
uv run pytest -m integration               # 集成测试

# 代码质量
black app tests && ruff check app tests && mypy app
```

## 贡献

欢迎贡献！请 Fork 仓库，创建功能分支，添加测试，然后提交 Pull Request。

## 许可证

MIT-0

---

⭐ 如果这个项目对你有帮助，欢迎给[本仓库](https://github.com/aws-samples/sample-bedrock-api-proxy)点个 star，让更多人发现它。
