<div align="center">

# 🔄 Anthropic-Bedrock API Proxy

**零代码迁移，让 Anthropic SDK 无缝对接 AWS Bedrock**

[![License](https://img.shields.io/badge/license-MIT--0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12+-green.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688.svg)](https://fastapi.tiangolo.com)
[![AWS](https://img.shields.io/badge/AWS-Bedrock-FF9900.svg)](https://aws.amazon.com/bedrock/)

<p>
  <a href="./README.md"><img src="https://img.shields.io/badge/文档-中文-red.svg" alt="中文文档"></a>
  <a href="./README_EN.md"><img src="https://img.shields.io/badge/Docs-English-blue.svg" alt="English Docs"></a>
  <a href="https://mp.weixin.qq.com/s/mW1RNem5zbAlyvLixSFWOw"><img src="https://img.shields.io/badge/📚-技术博客-purple.svg" alt="技术博客"></a>
  <a href="https://aws.amazon.com/cn/blogs/china/programmatic-tool-calling-agent-using-bedrock-and-ecs-docker-sandbox/"><img src="https://img.shields.io/badge/📝-AWS_Blog-FF9900.svg" alt="AWS Blog"></a>
  <a href="./cdk/DEPLOYMENT.md"><img src="https://img.shields.io/badge/🚀-部署指南-orange.svg" alt="部署指南"></a>
</p>

---

</div>

## 项目简介

> ⚠️ **免责声明**：本项目仅作为示例代码，用于演示和学习目的，**不适用于生产环境**。请在部署到任何生产环境之前，自行进行充分的安全审查、测试和加固。使用本项目所产生的风险由使用者自行承担。

这是一个轻量级的 API 转换服务，让你无需修改代码即可在 Anthropic SDK 中使用 AWS Bedrock 上的各种大语言模型。通过简单的环境变量配置，即可在 Claude Code、Claude Agent SDK 等工具中切换使用 Qwen3、DeepSeek 等不同模型。

> 📝 **AWS 官方博客**：[基于 Amazon Bedrock 与自建 ECS Docker Sandbox 实现 Agent 编程式工具调用（Programmatic Tool Calling）](https://aws.amazon.com/cn/blogs/china/programmatic-tool-calling-agent-using-bedrock-and-ecs-docker-sandbox/)

![最新公众号文章](./assets/weixin.png)
**核心优势：**
- 🔄 **零代码迁移** - 完全兼容 Anthropic API，无需修改现有代码
- 🚀 **开箱即用** - 支持流式/非流式响应、工具调用、多模态等所有高级特性
- 🤖 **Programmatic Tool Calling** - 业界首个在 Bedrock 上实现 Anthropic 兼容 PTC API 的代理服务
- 🔍 **Dynamic Web Search** - 支持 Anthropic `web_search_20250305` / `web_search_20260209`，Claude 可动态编写代码过滤搜索结果
- 🌐 **Web Fetch** - 支持 Anthropic `web_fetch_20250910` / `web_fetch_20260209`，无需额外 API Key 即可抓取网页与 PDF 内容
- 💰 **成本优化** - 灵活使用 Bedrock 上的开源模型，显著降低推理成本
- 🔐 **企业级** - 内置 API 密钥管理、速率限制、使用追踪和监控指标
- ☁️ **云原生** - 一键部署到 AWS ECS，自动扩展，高可用架构
- 🎯 **场景广泛** - 适用于开发工具代理、应用集成、模型评测等多种场景

**典型应用：** 在**Claude Code** 中使用Bedrock 托管的 Qwen3-Coder-480B 进行代码生成，或在使用**Claude Agent SDK**构建生产应用中混合使用不同模型以平衡性能和成本。

## 功能特性
### Claude Code/Agent SDK 伪装适配
- **Claude Code/Agent SDK** 会识别是否直连Bedrock，会丢弃很多beta header，导致效果和行为跟使用A\官方API版本可能有所不同，例如出现(max token自动裁剪问题)[https://github.com/anthropics/claude-code/issues/8756]
该Proxy可以通过更改Base URL和模型 ID映射进行请求伪装，尽可能的还原A\官方版本行为
### 核心功能
- **Anthropic API 兼容性**：完全支持 Anthropic Messages API 格式
- **双向格式转换**：在 Anthropic 和 Bedrock 格式之间无缝转换
- **流式传输支持**：支持服务器发送事件 (SSE) 实时流式响应
- **非流式支持**：传统的请求-响应模式

### 高级功能
- **工具使用（函数调用）**：转换并执行工具定义
- **Programmatic Tool Calling (PTC)**：完整实现 Anthropic PTC API，支持 Claude 生成并执行 Python 代码来调用工具
  - 与 Anthropic API 完全兼容的 PTC 接口（`anthropic-beta: advanced-tool-use-2025-11-20`）
  - 安全的 Docker Sandbox 代码执行环境
  - 客户端工具执行模式（工具由客户端执行，结果返回给代理）
  - 支持多轮代码执行和工具调用
  - 支持 `asyncio.gather` 并行工具调用
  - 会话管理与容器复用，提升性能
- **扩展思考**：支持响应中的思考块
- **多模态内容**：支持文本、图像和文档
- **提示词缓存与 1 小时 TTL**：支持 Anthropic `cache_control` 提示词缓存，可配置缓存 TTL（`5m` / `1h`）
  - 支持 1 小时缓存 TTL（`ttl: "1h"`），降低高频重复请求成本
  - 三级优先级：API Key 强制覆盖 > 客户端请求 > 代理默认值（`DEFAULT_CACHE_TTL`）
  - 每个 API Key 可单独配置 `cache_ttl`，在 Admin Portal 中管理
  - TTL 感知计费：5m 写入按 1.25x 输入价格计费，1h 写入按 2x 输入价格计费
- **Beta Header 映射**：自动将 Anthropic beta headers 映射到 Bedrock beta headers（如 `advanced-tool-use-2025-11-20` → `tool-examples-2025-10-29`）
- **工具输入示例**：支持 `input_examples` 参数，为工具提供示例输入以帮助模型更好地理解工具用法
- **Web 搜索工具**：支持 Anthropic 的 `web_search_20250305` 和 `web_search_20260209` 工具类型
  - 代理端服务器工具实现（Bedrock 不原生支持 Web 搜索，由代理拦截并执行）
  - 可插拔搜索提供商：支持 Tavily（推荐，专为 AI 优化）和 Brave Search
  - 域名过滤：支持 `allowed_domains` 和 `blocked_domains` 配置
  - 搜索次数限制：通过 `max_uses` 控制每次请求的最大搜索次数
  - 用户位置本地化：支持基于地理位置的搜索结果优化
  - 动态过滤（`web_search_20260209`）：Claude 可编写代码过滤搜索结果（依赖 Docker sandbox 代码执行，**ECS 部署需使用 EC2 启动类型**）
  - 支持流式和非流式响应
- **Web 抓取工具**：支持 Anthropic 的 `web_fetch_20250910` 和 `web_fetch_20260209` 工具类型
  - 代理端服务器工具实现（Bedrock 不原生支持 Web Fetch，由代理拦截并执行）
  - 默认使用 httpx 直接抓取（**无需额外 API Key**），内置 HTML 转纯文本
  - 支持 PDF 文档抓取（base64 传递）
  - 域名过滤：支持 `allowed_domains` 和 `blocked_domains` 配置
  - 抓取次数限制：通过 `max_uses` 控制；内容长度限制：通过 `max_content_tokens` 控制
  - 动态过滤（`web_fetch_20260209`）：Claude 可编写代码处理抓取内容（依赖 Docker sandbox，**ECS 部署需使用 EC2 启动类型**）
  - 支持流式和非流式响应
- **OpenAI 兼容 API（Bedrock Mantle）**：非 Claude 模型可选择通过 Bedrock 的 OpenAI Chat Completions API（bedrock-mantle 端点）进行请求，替代 Converse API
  - 通过 `ENABLE_OPENAI_COMPAT` 环境变量控制，默认关闭
  - 需要配置 `OPENAI_API_KEY`（Bedrock API Key）和 `OPENAI_BASE_URL`（如 `https://bedrock-mantle.us-east-1.api.aws/v1`）
  - 自动将 Anthropic `thinking` 配置映射为 OpenAI `reasoning`（`budget_tokens` → `effort: high/medium/low`）
  - 支持流式和非流式响应、工具调用、多模态内容
  - Claude 模型不受影响，仍使用 InvokeModel API

### 基础设施
- **身份验证**：基于 API 密钥的身份验证，使用 DynamoDB 存储
- **速率限制**：每个 API 密钥的令牌桶算法
- **使用跟踪**：全面的分析和令牌使用跟踪
- **服务层级**：支持 Bedrock Service Tier 配置，平衡成本和延迟
- **OpenTelemetry 分布式追踪**：支持将 LLM 调用追踪数据导出到任何 OTEL 兼容后端（Langfuse、Jaeger、Grafana Tempo 等）
  - 遵循 [OTEL GenAI 语义规范](https://opentelemetry.io/docs/specs/semconv/gen-ai/)，记录模型、Token 用量、延迟等
  - 支持会话级追踪，通过 `x-session-id` header 关联同一对话的所有请求
  - 流式和非流式响应均支持完整的 Token 统计
  - 零开销设计：未启用时所有追踪函数为 no-op
- **Admin Portal**：Web 管理界面，支持 API 密钥管理、用量监控、预算控制
  - Cognito 认证保护，支持用户密码和 SRP 认证
  - 实时查看 API 密钥使用统计（输入/输出/缓存 Token）
  - 模型定价配置和成本追踪
  - 预算限制与自动停用功能

### 支持的模型
- Claude 4.5/4.6
- Claude 4.5 Haiku
- Qwen3-coder-480b
- Qwen3-235b-instruct
- Kimi 2.5
- minimax2.1
- 任何其他支持 Converse API 或 OpenAI Chat Completions API 的 Bedrock 模型
- 支持 Bedrock **应用推理配置（application inference profile）ARN**；代理会将其解析为底层基础模型，以保证路由、beta headers 和计费的准确性。

## 使用场景

### 作为 Claude Code 的模型代理
* 例如，您可以在启动 `claude` 之前设置以下环境变量，然后就可以在 `claude code` 中使用 Bedrock 中的模型
#### 1. 编辑或新增 `.claude.json` 文件
```json 
# MacOS & Linux 为 `~/.claude.json`
# Windows 为`用户目录/.claude.json`
# 新增 `hasCompletedOnboarding` 参数
{
  "hasCompletedOnboarding": true
}
```

#### 2. 编辑或新增 `settings.json` 文件
```json
# 编辑或新增 `settings.json` 文件
# MacOS & Linux 为 `~/.claude/settings.json`
# Windows 为`用户目录/.claude/settings.json`
# 新增或修改里面的 env 字段
# 注意替换里面的 `your_api_key` 为您上一步获取到的 API Key
# 替换ANTHROPIC_BASE_URL为部署之后的base url，如果启用了CloudFront，则是cloudfront 地址，否则是ALB地址
{
  "env": {
    "ANTHROPIC_API_KEY": "your_api_key",
    "ANTHROPIC_BASE_URL": "base url"
  }
}
```
默认已经做了官网Claude 系列模型ID与Bedrock Claude模型ID映射，无需再添加模型环境，如果使用非Claude模型，则还需要继续在`settings.json` 文件"env"增加.  
```json
{
  "env": {
    "ANTHROPIC_API_KEY": "your_api_key",
    "ANTHROPIC_BASE_URL": "base url",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "mooonshotai.kimi-k2.5",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "mooonshotai.kimi-k2.5",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "mooonshotai.kimi-k2.5"
  }
}
```


### 作为 Claude Agent SDK 的模型代理
- 相同的设置也适用于 Claude Agent SDK
例如在AgentCore Runtime中使用在Dockerfile，[参考项目链接](https://github.com/xiehust/agentcore_demo/tree/main/00-claudecode_agent).

```Dockerfile
FROM --platform=linux/arm64 ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

# Install system dependencies including Node.js for playwright-mcp
RUN apt-get update && apt-get install -y \
    git \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs zip \
    && rm -rf /var/lib/apt/lists/*
RUN npm install -g @anthropic-ai/claude-code
# Copy entire project (respecting .dockerignore)
COPY . .
RUN mkdir -p workspace
RUN uv sync

# Signal that this is running in Docker for host binding logic
ENV DOCKER_CONTAINER=1
ENV CLAUDE_CODE_USE_BEDROCK=0
ENV ANTHROPIC_BASE_URL=http://anthropic-proxy-prod-alb-xxxx.elb.amazonaws.com
ENV export ANTHROPIC_API_KEY=sk-xxxx

EXPOSE 8080

CMD [".venv/bin/python3", "claude_code_agent.py"]
```

## 服务层级（Service Tier）

Bedrock Service Tier 功能允许您在成本和延迟之间进行权衡选择。本代理服务完整支持该特性，并提供灵活的配置方式。

### 可用层级

| 层级 | 描述 | 延迟 | 成本 | Claude 支持 |
|------|------|------|------|------------|
| `default` | 标准服务层级 | 标准 | 标准 | ✅ |
| `flex` | 灵活层级，适合批处理任务 | 更高（最长24小时） | 更低 | ❌ |
| `priority` | 优先级层级，适合实时应用 | 更低 | 更高 | ❌ |
| `reserved` | 预留容量层级 | 稳定 | 预付费 | ✅ |

### 配置方式
#### 1. 按 API Key 配置

系统默认值`defaul`, 可以为不同用户或用途创建具有不同服务层级的 API Key：

```bash
# 创建使用 flex 层级的 API Key（适合非实时批处理任务）
./scripts/create-api-key.sh -u batch-user -n "Batch Processing Key" -t flex

# 创建使用 priority 层级的 API Key（适合实时应用）
./scripts/create-api-key.sh -u realtime-user -n "Realtime App Key" -t priority
```

#### 2. 优先级规则

服务层级按以下优先级确定：
1. **API Key 配置**（最高优先级）- 如果 API Key 有指定的服务层级
3. **系统默认值** - `default`

### 自动降级机制

当指定的服务层级不被目标模型支持时，代理服务会**自动降级**到 `default` 层级并重试请求：

```
请求 (flex tier) → Claude 模型 → 不支持 flex → 自动降级到 default → 成功
```

这确保了即使配置了不兼容的服务层级，请求也不会失败。

### 使用建议

| 场景 | 推荐层级 | 说明 |
|------|---------|------|
| 实时对话/聊天 | `default` 或 `priority` | 需要低延迟响应 |
| 批量数据处理 | `flex` | 可接受较高延迟，节省成本 |
| 代码生成/开发工具 | `default` | 平衡延迟和成本 |
| 生产环境关键应用 | `reserved` | 需要稳定的容量保证 |

### 模型兼容性

| 模型 | default | flex | priority | reserved |
|------|---------|------|----------|----------|
| Claude 系列 | ✅ | ❌ | ❌ | ✅ |
| Qwen 系列 | ✅ | ✅ | ✅ | ✅ |
| DeepSeek 系列 | ✅ | ✅ | ✅ | ✅ |
| Nova 系列 | ✅ | ✅ | ✅ | ✅ |
| MimiMax 系列 | ✅ | ✅ | ✅ | ✅ |

> **注意**：具体模型对服务层级的支持可能会随 AWS Bedrock 更新而变化，请参考 [AWS 官方文档](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-service-tiers.html) 获取最新信息。

## Beta Header 映射与工具输入示例

### Beta Header 映射

代理服务支持将 Anthropic beta headers 自动映射到 Bedrock beta headers，使您可以在使用 Bedrock 时访问 Anthropic 的 beta 功能。

**默认映射：**

| Anthropic Beta Header | Bedrock Beta Headers |
|----------------------|---------------------|
| `advanced-tool-use-2025-11-20` | `tool-examples-2025-10-29`, `tool-search-tool-2025-10-19` |

**支持的模型：**
- Claude Opus 4.5 (`claude-opus-4-5-20251101`)

**使用示例：**

```python
from anthropic import Anthropic

client = Anthropic(
    api_key="sk-your-api-key",
    base_url="http://localhost:8000"
)

# 使用 beta header
message = client.beta.messages.create(
    model="claude-opus-4-5-20251101",
    max_tokens=1024,
    betas=["advanced-tool-use-2025-11-20"],
    messages=[{"role": "user", "content": "Hello!"}]
)
```

### 工具输入示例 (input_examples)

`input_examples` 参数允许您为工具定义提供示例输入，帮助模型更好地理解如何使用该工具。

**使用示例：**

```python
message = client.messages.create(
    model="claude-opus-4-5-20251101",
    max_tokens=1024,
    tools=[
        {
            "name": "get_weather",
            "description": "获取指定位置的天气信息",
            "input_schema": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "城市名称"},
                    "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]}
                },
                "required": ["location"]
            },
            "input_examples": [
                {"location": "北京", "unit": "celsius"},
                {"location": "San Francisco, CA", "unit": "fahrenheit"},
                {"location": "东京"}  # unit 是可选的
            ]
        }
    ],
    messages=[{"role": "user", "content": "今天北京天气怎么样？"}]
)
```

### 配置扩展

**添加新的 beta header 映射：**
在 `.env` 或 `app/core/config.py` 中修改 `BETA_HEADER_MAPPING`。

**为更多模型启用 beta header 映射：**
向 `BETA_HEADER_SUPPORTED_MODELS` 添加关键字（对模型 ID 做不区分大小写的子串匹配）。默认值 `["claude"]` 已覆盖所有 Claude 变体。

## 提示词缓存 TTL（1 小时缓存）

代理服务支持 Anthropic 的 `cache_control` 提示词缓存特性，并扩展了 TTL（缓存生存时间）配置能力。Bedrock 上的 Claude 模型默认缓存 TTL 为 5 分钟，本代理支持将其延长至 **1 小时**，显著降低高频重复请求的成本。

### TTL 优先级

| 优先级 | 来源 | 说明 |
|--------|------|------|
| 1（最高） | API Key `cache_ttl` | DynamoDB 中为 API Key 配置的强制覆盖值，所有 `cache_control` 块都会被重写 |
| 2 | 客户端请求 `cache_control.ttl` | 客户端在请求中指定的 TTL，无 API Key 覆盖时保留 |
| 3 | `DEFAULT_CACHE_TTL` 环境变量 | 代理级默认值，填充有 `cache_control` 但未指定 TTL 的块 |
| 4（最低） | 无 TTL | 使用 Anthropic/Bedrock 默认值（5 分钟） |

### 使用示例

```python
from anthropic import Anthropic

client = Anthropic(
    api_key="sk-your-api-key",
    base_url="http://localhost:8000"
)

# 客户端指定 1 小时缓存 TTL
message = client.messages.create(
    model="claude-sonnet-4-5-20250929",
    max_tokens=1024,
    system=[
        {
            "type": "text",
            "text": "你是一个专业的软件工程师...",  # 长系统提示
            "cache_control": {"type": "ephemeral", "ttl": "1h"}
        }
    ],
    messages=[{"role": "user", "content": "你好！"}]
)
```

### 配置

```bash
# 代理级默认缓存 TTL（可选，不设置则使用 Anthropic 默认 5m）
DEFAULT_CACHE_TTL=1h

# 每个 API Key 可在 Admin Portal 或 DynamoDB 中配置 cache_ttl
# 值：'5m' 或 '1h'，设置后会强制覆盖所有请求的缓存 TTL
```

### 计费说明

缓存写入价格因 TTL 不同而异：

| TTL | 缓存写入价格 | 说明 |
|-----|------------|------|
| 5m（默认） | 1.25x 输入价格 | 标准缓存写入费率 |
| 1h | 2.0x 输入价格 | 延长缓存需要更高写入成本 |

系统会自动根据每次请求的实际 TTL 计算正确的缓存写入费用。

## OpenTelemetry 分布式追踪（LLM Observability）

代理服务内置 OpenTelemetry 追踪支持，可将 LLM 调用的详细信息导出到任何 OTEL 兼容的可观测性后端，实现：

- **Token 用量追踪**：每次请求的 input/output/cache tokens 统计
- **延迟分析**：端到端延迟、Bedrock API 调用延迟、流式响应持续时间
- **会话关联**：通过 `x-session-id` header 将同一对话的多次请求关联在一起
- **工具调用追踪**：记录每次工具调用的名称和 ID
- **PTC 代码执行追踪**：记录 Programmatic Tool Calling 的执行过程
- **错误诊断**：自动记录异常信息和错误状态

### Span 层级结构（基于 Turn 的 Agent Loop 追踪）

```
Trace "chat claude-sonnet-4-5-20250929"
  ├── Turn 1 (input=用户消息, output=助手回复)
  │     ├── gen_ai.chat (模型, Token 用量, 延迟)
  │     ├── Tool: Read (input=工具输入)
  │     └── Tool: Edit (input=工具输入)
  ├── Turn 2
  │     ├── gen_ai.chat
  │     └── Tool: Bash
  └── Turn 3
        └── gen_ai.chat (最终文本响应，无工具调用)
```

每个 Agent Loop 中的 HTTP 请求映射为一个 **Turn** span，包含：
- `gen_ai.chat` 生成 span（记录模型、Token 用量、延迟）
- 响应中每个 tool_use 块对应一个 Tool span
- 结构化的 input/output 属性（Langfuse UI 自动渲染为 JSON 对象）

### 记录的关键属性

| 属性 | 说明 | 示例 |
|------|------|------|
| `gen_ai.request.model` | 请求模型 | `claude-sonnet-4-5-20250929` |
| `gen_ai.usage.input_tokens` | 输入 Token 数 | `1500` |
| `gen_ai.usage.output_tokens` | 输出 Token 数 | `350` |
| `gen_ai.response.finish_reasons` | 停止原因 | `["end_turn"]` |
| `gen_ai.conversation.id` | 会话 ID | `session-abc123` |
| `langfuse.observation.usage_details` | 完整用量 JSON（含缓存 Token） | `{"input":1500,"output":350,"cache_read_input_tokens":800}` |
| `proxy.api_key_hash` | API Key 哈希（隐私安全） | `a1b2c3d4...` |

### 连接到 Langfuse Cloud

[Langfuse](https://langfuse.com) 是一个开源的 LLM 可观测性平台，原生支持 OTEL 协议。以下是连接步骤：

**1. 获取 Langfuse 凭证**

登录 [Langfuse Cloud](https://us.cloud.langfuse.com)，在项目 Settings → API Keys 中获取 Public Key 和 Secret Key。

**2. 生成 Base64 认证字符串**

```bash
echo -n "your-public-key:your-secret-key" | base64
```

**3. 配置环境变量**

```bash
ENABLE_TRACING=true
OTEL_EXPORTER_OTLP_ENDPOINT=https://us.cloud.langfuse.com/api/public/otel
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic <上一步生成的 base64 字符串>
OTEL_SERVICE_NAME=anthropic-bedrock-proxy
OTEL_TRACE_CONTENT=true
```

**4. 启动服务并发送请求**

```bash
# 启动服务
uv run uvicorn app.main:app --reload

# 发送请求（带 session ID 用于会话关联）
curl http://localhost:8000/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: sk-your-key" \
  -H "x-session-id: my-test-session" \
  -d '{
    "model": "claude-sonnet-4-5-20250929",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```
![alt text](image-12.png)
**5. 在 Langfuse 中查看追踪**

登录 Langfuse Cloud，在 Traces 页面即可看到追踪数据，包括：
- 请求完整的 Span 层级和时间线
- Token 用量和缓存命中情况
- 按 Session ID 分组查看对话流程
- 模型、延迟、成本等统计信息

### 连接到其他 OTEL 后端

**Jaeger（本地调试）：**

```bash
# 启动 Jaeger
docker run -d -p 4318:4318 -p 16686:16686 jaegertracing/all-in-one

# 配置代理
ENABLE_TRACING=true
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
OTEL_SERVICE_NAME=anthropic-bedrock-proxy

# 查看追踪：http://localhost:16686
```

**Grafana Tempo：**

```bash
ENABLE_TRACING=true
OTEL_EXPORTER_OTLP_ENDPOINT=https://your-tempo-endpoint
OTEL_EXPORTER_OTLP_PROTOCOL=grpc
OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic <credentials>
```

### 内容追踪（可选）

默认情况下，追踪**不记录**请求和响应的实际内容（因为可能包含敏感信息）。如需启用内容追踪用于调试：

```bash
# 启用内容追踪（会记录 prompt 和 completion 内容，注意 PII 风险）
OTEL_TRACE_CONTENT=true
```

启用后，追踪数据中将包含：
- Trace 级别 Input：结构化 JSON 对象（system prompt、tools 含 input_schema、用户消息）
- Turn 级别 Input/Output：当前轮次的用户消息和助手回复
- gen_ai.chat 的 prompt：仅包含当前轮次的消息（不包含历史消息）
- 响应文本和工具调用详情

### CDK 部署开启追踪

通过 CDK 部署到 ECS 时，可以通过环境变量在部署时开启追踪，**无需修改代码**：

```bash
# 以 Langfuse 为例
ENABLE_TRACING=true \
OTEL_EXPORTER_OTLP_ENDPOINT=https://us.cloud.langfuse.com/api/public/otel \
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf \
OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic $(echo -n 'pk-xxx:sk-xxx' | base64)" \
OTEL_SERVICE_NAME=anthropic-bedrock-proxy-prod \
OTEL_TRACE_CONTENT=true \
OTEL_TRACE_SAMPLING_RATIO=1.0 \
./scripts/deploy.sh -e prod -r us-west-2 -p arm64 -l ec2
```

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| `ENABLE_TRACING` | 开启追踪 | `false` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP 导出端点 | 无 |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | 协议 (`http/protobuf` / `grpc`) | `http/protobuf` |
| `OTEL_EXPORTER_OTLP_HEADERS` | 认证 Headers | 无 |
| `OTEL_SERVICE_NAME` | 服务名称 | 无 |
| `OTEL_TRACE_CONTENT` | 记录 prompt/completion 内容 | `false` |
| `OTEL_TRACE_SAMPLING_RATIO` | 采样率 (0.0-1.0) | `1.0` |

> **优先级**：环境变量 > `cdk/config/config.ts` 中的配置 > 默认值

### 采样配置

对于高流量场景，可以通过采样率控制追踪数据量：

```bash
# 50% 采样（每 2 个请求采样 1 个）
OTEL_TRACE_SAMPLING_RATIO=0.5

# 10% 采样（适合高流量生产环境）
OTEL_TRACE_SAMPLING_RATIO=0.1

# 全量采样（默认，适合开发和低流量环境）
OTEL_TRACE_SAMPLING_RATIO=1.0
```

## 架构

```
+----------------------------------------------------------+
|              客户端应用程序                               |
|           (Anthropic Python SDK)                         |
+---------------------------+------------------------------+
                            |
                            | HTTP/HTTPS (Anthropic 格式)
                            |
                            v
+----------------------------------------------------------+
|          FastAPI API 代理服务                             |
|                                                           |
|  +----------+  +-----------+  +----------------+         |
|  |   认证   |  |   速率    |  |   格式         |         |
|  |  中间件  |->|   限制    |->|   转换         |         |
|  +----------+  +-----------+  +----------------+         |
+-------+---------------+---------------+------------------+
        |               |               |
        v               v               v
  +----------+    +----------+    +----------+
  | DynamoDB |    |   AWS    |    |CloudWatch|
  |          |    | Bedrock  |    |   日志/  |
  | API 密钥 |    | Runtime  |    |   指标   |
  |  使用量  |    | Converse |    |          |
  |  缓存    |    |          |    |          |
  +----------+    +----------+    +----------+
```

### 组件概述

- **FastAPI 应用程序**：异步 Web 框架，自动生成 OpenAPI 文档
- **格式转换器**：在 Anthropic 和 Bedrock 格式之间进行双向转换
- **身份验证中间件**：使用 DynamoDB 进行 API 密钥验证
- **速率限制中间件**：令牌桶算法，可配置限制
- **Bedrock 服务**：AWS Bedrock Converse/ConverseStream API 接口
- **DynamoDB 存储**：API 密钥、使用跟踪、缓存、模型映射
- **指标收集**：Prometheus 兼容的监控指标

### AWS ECS Fargate 生产部署架构

![ECS Architecture](assets/ecs-architecture.png)

**架构说明：**

| 组件 | 说明 |
|------|------|
| **VPC** | 跨多可用区部署，包含公有/私有子网，CIDR: 10.x.0.0/16 |
| **Application Load Balancer** | 位于公有子网，接收外部 HTTP/HTTPS 流量 |
| **ECS Fargate Cluster** | 位于私有子网，运行容器化的代理服务 |
| **NAT Gateway** | 为私有子网提供出站互联网访问（开发环境 1 个，生产环境多 AZ） |
| **VPC Endpoints** | 生产环境配置 Bedrock、DynamoDB、ECR、CloudWatch 私有端点，优化成本和安全性 |
| **Auto Scaling** | 基于 CPU/内存利用率和请求数自动扩缩容（最小 2，最大 10） |
| **DynamoDB Tables** | API Keys、Usage、Model Mapping 三张表，PAY_PER_REQUEST 计费 |
| **Secrets Manager** | 安全存储 Master API Key |
| **CloudWatch Logs** | 集中式日志管理，生产环境启用 Container Insights |

## 部署选项快速入门

### 克隆仓库：
```bash
git clone <repository-url>
cd anthropic_api_converter
```

### 选项 1. AWS ECS 部署（推荐）

#### 启动类型选择

| 特性 | Fargate（默认） | EC2 |
|------|----------------|-----|
| **PTC 支持** | 否 | 是 |
| **管理复杂度** | 零（无服务器） | 需要管理 ASG |
| **成本模式** | 按使用量付费 | 按实例付费 |
| **扩展速度** | 快（秒级） | 较慢（分钟级） |
| **Docker 访问** | 否 | 是（挂载 socket） |
| **推荐场景** | 标准 API 代理 | 需要 PTC 功能 |

#### 1. 安装依赖

```bash
cd cdk
npm install
```

#### 2. 部署到生产环境

**Fargate 部署（默认，适合不需要 PTC 的场景）：**

```bash
# ⚠️ -p 参数需要根据当前的编译平台更改成 amd64 或 arm64
# ARM64（AWS Graviton、Apple Silicon）
./scripts/deploy.sh -e prod -r us-west-2 -p arm64

# AMD64（Intel/AMD 服务器）
./scripts/deploy.sh -e prod -r us-west-2 -p amd64
```

**EC2 部署（启用 PTC 功能）：**

```bash
# 使用 -l ec2 参数启用 EC2 启动类型，自动启用 PTC
./scripts/deploy.sh -e prod -r us-west-2 -p arm64 -l ec2

# 开发环境（使用 Spot 实例节省成本）
./scripts/deploy.sh -e dev -r us-west-2 -p arm64 -l ec2
```

**EC2 启动类型配置：**

| 环境 | 实例类型 | Spot 实例 | Docker Socket |
|------|---------|----------|---------------|
| dev + ARM64 | t4g.medium | 是 | 已挂载 |
| dev + AMD64 | t3.medium | 是 | 已挂载 |
| prod + ARM64 | t4g.large | 否 | 已挂载 |
| prod + AMD64 | t3.large | 否 | 已挂载 |

**启用 Web Search 和 Cache TTL（通过环境变量）：**

```bash
# Fargate 模式开启 Web Search（仅支持 web_search_20250305）
ENABLE_WEB_SEARCH=true \
WEB_SEARCH_PROVIDER=tavily \
WEB_SEARCH_API_KEY=tvly-your-api-key \
./scripts/deploy.sh -e prod -r us-west-2 -p arm64

# 启用 web_search_20260209 动态过滤（需要 EC2 启动类型以支持 Docker 代码执行）
ENABLE_WEB_SEARCH=true \
WEB_SEARCH_PROVIDER=tavily \
WEB_SEARCH_API_KEY=tvly-your-api-key \
./scripts/deploy.sh -e prod -r us-west-2 -p arm64 -l ec2

# Web Fetch 默认启用，无需额外 API Key（使用 httpx 直接抓取）
# 如需关闭：ENABLE_WEB_FETCH=false
```

这将部署：
- DynamoDB 表
- 带有 NAT 网关的 VPC
- ECS Fargate/EC2 集群和服务
- 应用程序负载均衡器
- （EC2 模式）Auto Scaling Group 和容量提供程序

部署大约需要 **15-20 分钟**。

#### 3. 部署输出

部署完成后，您将看到以下输出信息：

```text
Access URLs:
  API Proxy: http://anthropic-proxy-prod-alb-xxxx.us-west-2.elb.amazonaws.com
  Admin Portal: http://anthropic-proxy-prod-alb-xxxx.us-west-2.elb.amazonaws.com/admin/

Cognito (Admin Portal Authentication):
  User Pool ID: us-west-2_xxxxxxxxx
  Client ID: xxxxxxxxxxxxxxxxxxxxxxxxxx
  Region: us-west-2

Master API Key Secret:
  Secret Name: anthropic-proxy-prod-master-api-key
  Retrieve with: aws secretsmanager get-secret-value --secret-id anthropic-proxy-prod-master-api-key --region us-west-2

Next Steps:
  1. Create API keys using: ./scripts/create-api-key.sh
  2. Test the health endpoint: curl http://<alb-dns>/health
  3. Create admin user: ./scripts/create-admin-user.sh -e prod -r us-west-2 --email <admin@example.com>
```

#### 4. 创建Admin portal登陆账号和临时密码
- 在cdk/目录下
```shell
./scripts/create-admin-user.sh -e prod -r us-west-2 --email <admin@example.com>
```

#### 5. 使用上面的用户名和临时密码访问管理界面
首次登陆需要提示更改密码
Admin Portal: http://anthropic-proxy-prod-alb-xxxx.us-west-2.elb.amazonaws.com/admin/

#### 6. 在界面创建 API 密钥，设置价格，budget等信息
![alt text](./admin_portal/image_admin1.png)

**手动运行脚本创建 API 密钥示例：**

```bash
# 进入 CDK 目录
cd cdk

# 基本用法 - 创建默认 API 密钥
./scripts/create-api-key.sh -u user123 -n "My API Key"

# 指定服务层级 - 使用 flex tier（更低成本，更高延迟）
./scripts/create-api-key.sh -u user123 -n "Flex Key" -t flex

# 指定服务层级 - 使用 priority tier（更低延迟，更高成本）
./scripts/create-api-key.sh -u user123 -n "Priority Key" -t priority

# 同时设置自定义速率限制和服务层级
./scripts/create-api-key.sh -u user123 -n "Custom Key" -r 500 -t reserved

# 查看帮助
./scripts/create-api-key.sh -h
```

> **注意**: Claude 模型仅支持 `default` 和 `reserved` 服务层级，不支持 `flex`。如果使用 `flex` 层级调用 Claude 模型，代理会自动降级到 `default`。

#### 更多详情请参见 [CDK 部署文档](cdk/DEPLOYMENT.md)

### 选项 2. 运行 Docker

#### 2.1 构建主代理服务镜像

```bash
# 基本构建（使用当前平台架构）
docker build -t anthropic-bedrock-proxy:latest .

# 指定平台构建（用于跨平台部署）
# ARM64 架构（如 AWS Graviton、Apple Silicon）
docker build --platform linux/arm64 -t anthropic-bedrock-proxy:arm64 .

# AMD64 架构（如 Intel/AMD 服务器）
docker build --platform linux/amd64 -t anthropic-bedrock-proxy:amd64 .
```

#### 2.2 构建 PTC Sandbox 镜像（可选）

如果需要在 PTC 中使用数据分析包（pandas、numpy、scipy 等），需要构建自定义 sandbox 镜像：

```bash
cd docker/ptc-sandbox

# 构建数据科学版本（包含 pandas, numpy, scipy, matplotlib, scikit-learn）
./build.sh

# 或构建最小版本（仅 pandas, numpy，镜像更小）
./build.sh minimal

# 构建所有版本
./build.sh all
```

**镜像对比：**

| 镜像 | 大小 | 包含的包 |
|------|------|---------|
| `python:3.11-slim`（默认） | ~50MB | 仅 Python 标准库 |
| `ptc-sandbox:minimal` | ~200MB | numpy, pandas, requests, httpx |
| `ptc-sandbox:datascience` | ~800MB | numpy, pandas, scipy, matplotlib, scikit-learn, statsmodels |
| `public.ecr.aws/f8g1z3n8/bedrock-proxy-sandbox:datascience.0.1` | ~800MB | numpy, pandas, scipy, matplotlib, scikit-learn, statsmodels |

详细说明请参见 [PTC Sandbox 自定义镜像文档](docker/ptc-sandbox/README.md)

#### 2.3 运行容器

```bash
# 基本运行（无 PTC 支持）
docker run -d \
  -p 8000:8000 \
  -e AWS_REGION=us-east-1 \
  -e AWS_ACCESS_KEY_ID=your-key \
  -e AWS_SECRET_ACCESS_KEY=your-secret \
  -e MASTER_API_KEY=your-master-key \
  --name api-proxy \
  anthropic-bedrock-proxy:latest

# 启用 PTC 支持（需要挂载 Docker socket）
docker run -d \
  -p 8000:8000 \
  -e AWS_REGION=us-east-1 \
  -e AWS_ACCESS_KEY_ID=your-key \
  -e AWS_SECRET_ACCESS_KEY=your-secret \
  -e MASTER_API_KEY=your-master-key \
  -e ENABLE_PROGRAMMATIC_TOOL_CALLING=true \
  -e PTC_SANDBOX_IMAGE=ptc-sandbox:datascience \
  -v /var/run/docker.sock:/var/run/docker.sock \
  --name api-proxy \
  anthropic-bedrock-proxy:latest
```

#### 2.4 使用 Docker Compose（推荐本地开发）

```bash
# 启动所有服务（包括 DynamoDB Local、Prometheus、Grafana）
docker-compose up -d

# 查看日志
docker-compose logs -f api-proxy

# 停止服务
docker-compose down
```

## 选项 3. 本地启动

### 前置要求

- Python 3.12+
- 具有 Bedrock 访问权限的 AWS 账户
- 配置好的 AWS 凭证
- DynamoDB 访问权限
- **Docker**（仅 PTC 功能需要）- 如需使用 Programmatic Tool Calling 功能

### 安装

1. **使用 uv 安装依赖**：
```bash
# 如果尚未安装 uv，请先安装
pip install uv

# 安装依赖
uv sync
```

2. **配置环境**：
```bash
cp .env.example .env
# 编辑 .env 文件配置您的设置
```

3. **设置 DynamoDB 表**：
```bash
uv run scripts/setup_tables.py
```

4. **创建 API 密钥**：
```bash
# 创建基本 API 密钥（使用默认服务层级）
uv run python scripts/create_api_key.py --user-id dev-user --name "Development Key"

# 创建带有 flex 服务层级的 API 密钥（适用于 Qwen、DeepSeek 等非 Claude 模型）
uv run python scripts/create_api_key.py --user-id dev-user --name "Flex Key" --service-tier flex

# 创建带有自定义速率限制的 API 密钥
uv run python scripts/create_api_key.py --user-id dev-user --name "Limited Key" --rate-limit 100

# 查看所有选项
uv run python scripts/create_api_key.py --help
```

**服务层级选项：**
| 层级 | 说明 | 支持的模型 |
|------|------|-----------|
| `default` | 标准服务层级（默认） | 所有模型 |
| `flex` | 更低成本，更高延迟 | Qwen、DeepSeek、Nova（不支持 Claude） |
| `priority` | 更低延迟，更高成本 | 大部分模型 |
| `reserved` | 预留容量 | Claude 及大部分模型 |

**注意：** Claude 模型仅支持 `default` 和 `reserved` 层级。如果对 Claude 使用 `flex`，系统会自动回退到 `default`。

5. **（可选）设置 PTC Docker Sandbox**：

如果需要使用 Programmatic Tool Calling (PTC) 功能，需要准备 Docker 环境：

```bash
# 1. 确保 Docker 已安装并运行
docker --version
docker ps

# 2. 预先拉取 sandbox 镜像（可选，首次使用时会自动拉取）
docker pull python:3.11-slim

# 3. 验证 PTC 功能就绪
# 启动服务后，检查 PTC 健康状态
curl http://localhost:8000/health/ptc
# 预期返回: {"status": "healthy", "docker": "connected", ...}
```

**说明：**
- PTC sandbox 使用标准 Docker Hub 镜像 `python:3.11-slim`，**无需自行构建**
- 首次使用 PTC 时会自动拉取镜像（约 50MB），预先拉取可避免首次请求延迟
- 如需使用自定义镜像，设置环境变量 `PTC_SANDBOX_IMAGE=your-image:tag`
- Docker daemon 必须运行，用户需要有 Docker socket 访问权限

**自定义 Sandbox 镜像（包含数据分析包）：**

如果需要在 sandbox 中使用 pandas、numpy、scipy 等数据分析包，请构建自定义镜像：

```bash
# 进入 sandbox 镜像目录
cd docker/ptc-sandbox

# 构建包含数据科学包的镜像（pandas, numpy, scipy, matplotlib, scikit-learn）
./build.sh

# 或构建最小版本（仅 pandas, numpy）
./build.sh minimal

# 配置使用自定义镜像
echo "PTC_SANDBOX_IMAGE=ptc-sandbox:datascience" >> .env
```

详细说明请参见 [PTC Sandbox 自定义镜像文档](docker/ptc-sandbox/README.md)

6. **运行服务**：
```bash
uv run uvicorn app.main:app --reload --port 8000
```

服务将在 `http://localhost:8000` 上可用。

## 配置

### 环境变量

配置通过环境变量管理。所有选项请参见 `.env.example`。

#### 应用程序设置
```bash
APP_NAME=Anthropic-Bedrock API Proxy
ENVIRONMENT=development  # development, staging, production
LOG_LEVEL=INFO
```

#### AWS 设置
```bash
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key
```

#### 身份验证
```bash
REQUIRE_API_KEY=True
MASTER_API_KEY=sk-your-master-key
API_KEY_HEADER=x-api-key
```

#### 速率限制
```bash
RATE_LIMIT_ENABLED=True
RATE_LIMIT_REQUESTS=1000  # 每个时间窗口的请求数
RATE_LIMIT_WINDOW=60     # 时间窗口（秒）
```

#### 功能开关
```bash
ENABLE_TOOL_USE=True
ENABLE_EXTENDED_THINKING=True
ENABLE_DOCUMENT_SUPPORT=True
PROMPT_CACHING_ENABLED=False
ENABLE_PROGRAMMATIC_TOOL_CALLING=True  # 需要 Docker
ENABLE_WEB_SEARCH=True                # 需要搜索提供商 API 密钥
ENABLE_OPENAI_COMPAT=False           # 使用 OpenAI Chat Completions API（非 Claude 模型）
DEFAULT_CACHE_TTL=1h                  # 代理默认缓存 TTL（可选：'5m' 或 '1h'）
```

#### OpenAI 兼容 API 配置
```bash
# 启用 OpenAI 兼容 API（仅对非 Claude 模型生效）
ENABLE_OPENAI_COMPAT=False

# Bedrock Mantle API Key
OPENAI_API_KEY=your-bedrock-api-key

# Bedrock Mantle 端点 URL
OPENAI_BASE_URL=https://bedrock-mantle.us-east-1.api.aws/v1

# thinking → reasoning 映射阈值
OPENAI_COMPAT_THINKING_HIGH_THRESHOLD=10000    # budget_tokens >= 此值 → effort=high
OPENAI_COMPAT_THINKING_MEDIUM_THRESHOLD=4000   # budget_tokens >= 此值 → effort=medium
```

#### Web 搜索配置
```bash
# Web 搜索功能开关
ENABLE_WEB_SEARCH=True

# 搜索提供商：'tavily'（推荐）或 'brave'
WEB_SEARCH_PROVIDER=tavily

# 搜索提供商 API 密钥（Tavily 或 Brave）
WEB_SEARCH_API_KEY=tvly-your-api-key

# 每次搜索返回的最大结果数（默认 5）
WEB_SEARCH_MAX_RESULTS=5

# 每次请求的默认最大搜索次数（默认 10）
WEB_SEARCH_DEFAULT_MAX_USES=10
```

**使用示例：**

```python
from anthropic import Anthropic

client = Anthropic(
    api_key="sk-your-api-key",
    base_url="http://localhost:8000"
)

# 使用 web_search 工具
message = client.messages.create(
    model="claude-sonnet-4-5-20250929",
    max_tokens=4096,
    tools=[
        {
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 5,
            "allowed_domains": ["python.org", "docs.python.org"],
        }
    ],
    messages=[{"role": "user", "content": "Python 3.13 有哪些新特性？"}]
)
```

**搜索提供商对比：**

| 提供商 | 特点 | API 密钥获取 |
|--------|------|-------------|
| **Tavily**（推荐） | 专为 AI 优化，返回结构化内容 | [tavily.com](https://tavily.com) |
| **Brave Search** | 通用搜索 API | [brave.com/search/api](https://brave.com/search/api/) |

**工具类型对比：**

| 工具类型 | 说明 | 需要 Docker |
|---------|------|------------|
| `web_search_20250305` | 基础 Web 搜索 | 否 |
| `web_search_20260209` | 动态过滤（Claude 可编写代码过滤搜索结果） | **是**（依赖 Docker sandbox 执行代码，ECS 需使用 EC2 启动类型） |

**健康检查：**
```bash
curl http://localhost:8000/health/web-search
# 返回: {"status": "healthy", "provider": "tavily", "enabled": true, ...}
```

#### Web Fetch 配置

Web Fetch 工具允许 Claude 主动抓取指定 URL 的完整页面内容（与 Web Search 搜索关键词不同）。

```bash
# Web Fetch 默认启用，使用 httpx 直接抓取（无需 API Key）
ENABLE_WEB_FETCH=True

# 默认最大抓取次数
WEB_FETCH_DEFAULT_MAX_USES=20

# 默认最大内容 token 数
WEB_FETCH_DEFAULT_MAX_CONTENT_TOKENS=100000
```

**使用示例：**

```python
from anthropic import Anthropic

client = Anthropic(
    api_key="sk-your-api-key",
    base_url="http://localhost:8000"
)

# 使用 web_fetch 工具
message = client.messages.create(
    model="claude-sonnet-4-5-20250929",
    max_tokens=4096,
    tools=[
        {
            "type": "web_fetch_20250910",
            "name": "web_fetch",
            "max_uses": 5,
            "max_content_tokens": 50000,
        }
    ],
    messages=[{"role": "user", "content": "请抓取 https://docs.python.org/3/whatsnew/3.13.html 并总结主要新特性"}],
    extra_headers={"anthropic-beta": "web-fetch-2025-09-10"},
)
```

**Web Search vs Web Fetch 对比：**

| 维度 | Web Search | Web Fetch |
|------|-----------|-----------|
| **输入** | 搜索关键词（`query`） | 具体 URL（`url`） |
| **输出** | 多条搜索结果摘要 | 单个 URL 的完整页面内容 |
| **Provider** | Tavily / Brave（需 API Key） | httpx 直接抓取（默认，**无需 Key**） |
| **PDF 支持** | 无 | 有（base64 传递） |
| **默认 max_uses** | 10 | 20 |

**工具类型对比：**

| 工具类型 | 说明 | 需要 Docker |
|---------|------|------------|
| `web_fetch_20250910` | 基础 URL 抓取 | 否 |
| `web_fetch_20260209` | 动态过滤（Claude 可编写代码处理抓取内容） | **是**（依赖 Docker sandbox，ECS 需使用 EC2 启动类型） |

#### Programmatic Tool Calling (PTC) 配置
```bash
# PTC 功能开关（需要 Docker）
ENABLE_PROGRAMMATIC_TOOL_CALLING=True

# Docker sandbox 镜像（默认使用官方 Python 镜像，无需构建）
PTC_SANDBOX_IMAGE=python:3.11-slim

# 会话超时（秒），默认 270 秒（4.5 分钟）
PTC_SESSION_TIMEOUT=270

# 代码执行超时（秒）
PTC_EXECUTION_TIMEOUT=60

# 容器内存限制
PTC_MEMORY_LIMIT=256m

# 禁用容器网络访问（安全考虑，默认禁用）
PTC_NETWORK_DISABLED=True
```

#### Bedrock 服务层级（Service Tier）
```bash
# 默认服务层级：'default', 'flex', 'priority', 'reserved'
DEFAULT_SERVICE_TIER=default
```

**服务层级说明：**
- `default` - 标准服务层级（默认）
- `flex` - Flex 层级，提供更优惠的价格，但可能有更高的延迟
- `priority` - 优先级层级，提供更低的延迟
- `reserved` - 预留容量层级

**注意事项：**
- Claude 模型**仅支持** `default` 和 `reserved` 层级，**不支持** `flex` 层级
- 如果指定的服务层级不被模型支持，系统会自动回退到 `default` 层级
- 可以在创建 API 密钥时为每个密钥单独配置服务层级

#### OpenTelemetry 分布式追踪
```bash
# 启用追踪（默认关闭）
ENABLE_TRACING=true

# OTLP 导出端点
OTEL_EXPORTER_OTLP_ENDPOINT=https://your-otel-endpoint

# 导出协议：http/protobuf（默认）或 grpc
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf

# 导出认证 headers（格式：key1=value1,key2=value2）
OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic xxxxx

# 服务名称（用于区分不同环境的追踪数据）
OTEL_SERVICE_NAME=anthropic-bedrock-proxy

# 是否记录请求/响应内容（包含 PII，默认关闭）
OTEL_TRACE_CONTENT=false

# 采样率（0.0-1.0，默认 1.0 即全量采样）
OTEL_TRACE_SAMPLING_RATIO=1.0
```

## API 文档

### 端点

#### POST /v1/messages

创建消息（Anthropic 兼容）。

**请求体**：
```bash
curl http://localhost:8000/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: sk-xxx" \
  -d '{
    "model": "qwen.qwen3-coder-480b-a35b-v1:0",
    "max_tokens": 1024,
    "messages": [
      {"role": "user", "content": "你好！"}
    ]
  }'
```

```bash
curl http://localhost:8000/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: sk-xxx" \
  -d '{
    "model": "claude-sonnet-4-5-20250929",
    "max_tokens": 1024,
    "stream": true,
    "messages": [
      {"role": "user", "content": "写一首关于夏天的十四行诗"}
    ]
  }'
```

#### GET /v1/models

列出可用的 Bedrock 模型。

**请求**：
```bash
curl http://localhost:8000/v1/models \
  -H "x-api-key: sk-xxxx"
```

### 使用 Anthropic SDK

```python
from anthropic import Anthropic

# 使用自定义基础 URL 初始化客户端
client = Anthropic(
    api_key="sk-your-api-key",
    base_url="http://localhost:8000"
)

# 正常使用
message = client.messages.create(
    model="qwen.qwen3-coder-480b-a35b-v1:0",
    max_tokens=1024,
    messages=[
        {"role": "user", "content": "你好，Claude！"}
    ]
)

print(message.content[0].text)
```

### 流式传输示例

```python
with client.messages.stream(
    model="qwen.qwen3-coder-480b-a35b-v1:0",
    max_tokens=1024,
    messages=[
        {"role": "user", "content": "给我讲个故事"}
    ]
) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)
```

### 工具使用示例

```python
message = client.messages.create(
    model="qwen.qwen3-coder-480b-a35b-v1:0",
    max_tokens=1024,
    tools=[
        {
            "name": "get_weather",
            "description": "获取某个位置的天气",
            "input_schema": {
                "type": "object",
                "properties": {
                    "location": {"type": "string"}
                },
                "required": ["location"]
            }
        }
    ],
    messages=[
        {"role": "user", "content": "旧金山的天气怎么样？"}
    ]
)
```

## 安全

### 最佳实践

1. **API 密钥管理**：
   - 永远不要将 API 密钥提交到版本控制
   - 使用环境变量或密钥管理器
   - 定期轮换密钥
   - 为不同环境使用单独的密钥

2. **AWS 凭证**：
   - 在 AWS 上运行时使用 IAM 角色（ECS、Lambda）
   - 应用最小权限原则
   - 启用 CloudTrail 日志记录

3. **网络安全**：
   - 在生产环境中使用 HTTPS
   - 适当配置 CORS
   - 为 AWS 服务使用 VPC 端点
   - 实施 WAF 规则

4. **速率限制**：
   - 为每个 API 密钥配置适当的限制
   - 监控滥用模式
   - 实施指数退避

### 所需的 IAM 权限

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
      "Resource": [
        "arn:aws:dynamodb:*:*:table/anthropic-proxy-*"
      ]
    }
  ]
}
```

## 开发

### 项目结构

```
anthropic_api_proxy/
├── app/
│   ├── api/              # API 路由处理器
│   │   ├── health.py     # 健康检查端点
│   │   ├── messages.py   # 消息 API
│   │   └── models.py     # 模型 API
│   ├── converters/       # 格式转换器
│   │   ├── anthropic_to_bedrock.py   # Anthropic → Bedrock Converse
│   │   ├── bedrock_to_anthropic.py   # Bedrock Converse → Anthropic
│   │   ├── anthropic_to_openai.py    # Anthropic → OpenAI Chat Completions
│   │   └── openai_to_anthropic.py    # OpenAI Chat Completions → Anthropic
│   ├── core/             # 核心功能
│   │   ├── config.py     # 配置管理
│   │   ├── logging.py    # 日志设置
│   │   └── metrics.py    # 指标收集
│   ├── db/               # 数据库客户端
│   │   └── dynamodb.py   # DynamoDB 操作
│   ├── middleware/       # 中间件组件
│   │   ├── auth.py       # 身份验证
│   │   └── rate_limit.py # 速率限制
│   ├── schemas/          # Pydantic 模型
│   │   ├── anthropic.py  # Anthropic API 模式
│   │   ├── bedrock.py    # Bedrock API 模式
│   │   ├── web_search.py # Web 搜索工具模型
│   │   └── web_fetch.py  # Web 抓取工具模型
│   ├── services/         # 业务逻辑
│   │   ├── bedrock_service.py
│   │   ├── openai_compat_service.py  # OpenAI 兼容 API 服务（Bedrock Mantle）
│   │   ├── web_search_service.py  # Web 搜索编排服务
│   │   ├── web_search/            # 搜索提供商模块
│   │   │   ├── providers.py       # Tavily/Brave 搜索实现
│   │   │   └── domain_filter.py   # 域名过滤
│   │   ├── web_fetch_service.py   # Web 抓取编排服务
│   │   └── web_fetch/             # 抓取提供商模块
│   │       └── providers.py       # Httpx/Tavily 抓取实现
│   ├── tracing/          # OpenTelemetry 分布式追踪
│   │   ├── provider.py   # TracerProvider 初始化和导出器配置
│   │   ├── middleware.py  # 请求根 Span 中间件
│   │   ├── spans.py      # Span 创建辅助函数
│   │   ├── streaming.py  # 流式响应 Token 累积器
│   │   ├── attributes.py # OTEL GenAI 语义规范常量
│   │   └── context.py    # 会话 ID 提取和线程上下文传播
│   └── main.py           # 应用程序入口点
├── tests/
│   ├── unit/             # 单元测试
│   └── integration/      # 集成测试
├── scripts/              # 实用脚本
├── config/               # 配置文件
├── Dockerfile            # Docker 镜像定义
├── docker-compose.yml    # 本地开发堆栈
├── pyproject.toml        # 项目依赖
└── README.md             # 此文件
```

### 运行测试

```bash
# 运行所有测试
pytest

# 带覆盖率运行
pytest --cov=app --cov-report=html

# 运行特定测试文件
pytest tests/unit/test_converters.py

# 带详细输出运行
pytest -v
```

### 代码质量

```bash
# 格式化代码
black app tests

# 检查代码
ruff check app tests

# 类型检查
mypy app
```

## 测试

### 手动测试

```bash
# 健康检查
curl http://localhost:8000/health

# 列出模型
curl http://localhost:8000/v1/models \
  -H "x-api-key: sk-your-api-key"

# 创建消息
curl http://localhost:8000/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: sk-your-api-key" \
  -d '{
    "model": "claude-sonnet-4-5-20250929",
    "max_tokens": 1024,
    "messages": [
      {"role": "user", "content": "你好！"}
    ]
  }'

# 流式消息
curl http://localhost:8000/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: sk-your-api-key" \
  -d '{
    "model": "claude-sonnet-4-5-20250929",
    "max_tokens": 1024,
    "stream": true,
    "messages": [
      {"role": "user", "content": "数到 10"}
    ]
  }'
```

## 贡献

欢迎贡献！请：

1. Fork 仓库
2. 创建功能分支
3. 进行更改
4. 添加测试
5. 提交拉取请求

## 许可证

MIT-0
