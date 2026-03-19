# Server-Managed Tools 架构设计说明

> 本文档详细介绍 Anthropic-Bedrock API Proxy 中 Server-Managed Tools（Web Search、Web Fetch）的实现架构。

---

# Part 1: Web Search Tool

## 1. 背景与动机

### 1.1 问题

Anthropic 官方 API 支持 `web_search_20250305` 和 `web_search_20260209` 两种服务端搜索工具（server-managed tools）。这类工具由 Anthropic 服务端执行，客户端只需在请求中声明即可使用。

然而，AWS Bedrock 的 InvokeModel API **不支持** web search 这类服务端工具。请求中如果包含 `web_search_20250305` 类型的 tool，Bedrock 会返回错误。

### 1.2 解决方案

Proxy 在中间层拦截 web search 请求，自行编排一个 **Agentic Loop**（代理循环），模拟 Anthropic 官方 API 的行为：

1. 将 `web_search` 服务端工具替换为一个普通的自定义 tool
2. 循环调用 Bedrock — 当 Claude 调用 `web_search` 时，Proxy 执行搜索并将结果注入对话
3. 重复直到 Claude 不再需要搜索，输出最终回答
4. 将整个过程的输出组装为与 Anthropic 官方格式完全一致的响应

**对客户端完全透明**：使用 Anthropic Python SDK 的客户端无需任何修改。

---

## 2. 支持的工具版本

| 工具类型 | Beta Header | 特性 |
|---------|-------------|------|
| `web_search_20250305` | `web-search-2025-03-05` | 标准 Web 搜索 + 引用 |
| `web_search_20260209` | `web-search-2026-02-09` | 标准搜索 + **Dynamic Filtering**（bash 代码执行） |

Dynamic Filtering 允许 Claude 编写 Python/Bash 代码来处理、过滤、分析搜索结果，然后基于处理后的数据生成回答。

---

## 3. 源码结构

```
app/
├── api/
│   └── messages.py                    # API 路由：检测并分发 web search 请求
├── converters/
│   └── anthropic_to_bedrock.py        # 多轮对话中 web_search_tool_result 的转换
├── core/
│   └── config.py                      # Web search 配置项
├── schemas/
│   └── web_search.py                  # Pydantic 数据模型（80 行）
└── services/
    ├── web_search_service.py          # 核心服务：Agentic Loop 编排（1,447 行）
    └── web_search/
        ├── __init__.py                # 模块导出
        ├── providers.py               # 搜索提供商：Tavily / Brave（215 行）
        └── domain_filter.py           # 域名过滤器（84 行）
```

总代码量：约 **1,836 行**。

---

## 4. 整体架构

### 4.1 请求处理流程

```
                    ┌──────────────┐
                    │   Client     │
                    │ (Anthropic   │
                    │   SDK)       │
                    └──────┬───────┘
                           │
           tools: [{type: "web_search_20250305", ...}]
           anthropic-beta: "web-search-2025-03-05"
                           │
                    ┌──────▼───────┐
                    │  API Layer   │
                    │ messages.py  │
                    └──────┬───────┘
                           │
              is_web_search_request() → True
                           │
                    ┌──────▼───────────────┐
                    │  WebSearchService    │
                    │  (Agentic Loop)      │
                    │                      │
                    │  ┌─────────────┐     │
                    │  │ Iteration 1 │     │
                    │  │  Bedrock ←──┼──── │ ── invoke_model()
                    │  │  Claude     │     │
                    │  │  ↓ tool_use │     │
                    │  │  web_search │     │
                    │  └──────┬──────┘     │
                    │         │            │
                    │  ┌──────▼──────┐     │
                    │  │ Search API  │     │     ┌───────────────┐
                    │  │ (Tavily /   │◄────┼────►│ Brave/Tavily  │
                    │  │  Brave)     │     │     │ Search API    │
                    │  └──────┬──────┘     │     └───────────────┘
                    │         │            │
                    │  ┌──────▼──────┐     │
                    │  │ Iteration 2 │     │
                    │  │  Bedrock ←──┼──── │ ── invoke_model() (with results)
                    │  │  Claude     │     │
                    │  │  ↓ text     │     │
                    │  │  (answer)   │     │
                    │  └──────┬──────┘     │
                    │         │            │
                    │  Post-Process        │
                    │  Citations           │
                    └──────┬───────────────┘
                           │
              MessageResponse (Anthropic 格式)
                           │
                    ┌──────▼───────┐
                    │   Client     │
                    └──────────────┘
```

### 4.2 组件职责

| 组件 | 职责 |
|------|------|
| **API Layer** (`messages.py`) | 检测 web search 请求，分流到 WebSearchService，包装 usage 追踪 |
| **WebSearchService** (`web_search_service.py`) | 核心编排：Agentic Loop、工具替换、搜索执行、引用处理、响应组装 |
| **SearchProvider** (`providers.py`) | 搜索引擎抽象层：Tavily / Brave 实现 |
| **DomainFilter** (`domain_filter.py`) | 搜索结果域名过滤（白名单/黑名单） |
| **Schemas** (`web_search.py`) | 数据模型定义：工具配置、搜索结果、引用 |
| **Converter** (`anthropic_to_bedrock.py`) | 多轮对话中 `web_search_tool_result` block 的格式转换 |

---

## 5. 核心机制详解

### 5.1 请求检测与配置提取

客户端发送的请求中包含 web search 工具声明：

```json
{
  "model": "claude-sonnet-4-6",
  "tools": [
    {
      "type": "web_search_20250305",
      "name": "web_search",
      "max_uses": 5,
      "allowed_domains": ["example.com"],
      "blocked_domains": ["spam.com"],
      "user_location": {"type": "approximate", "country": "US"}
    }
  ],
  "messages": [{"role": "user", "content": "..."}]
}
```

**检测逻辑** (`is_web_search_request()`):
1. 检查 `settings.enable_web_search` 是否为 `True`
2. 遍历 `tools` 列表，查找 `type` 在 `{"web_search_20250305", "web_search_20260209"}` 中的条目

**配置提取** (`extract_web_search_config()`):
- 解析为 `WebSearchToolDefinition` 对象
- 字段：`type`, `name`, `max_uses`, `allowed_domains`, `blocked_domains`, `user_location`
- `max_uses` 默认值来自配置 `WEB_SEARCH_DEFAULT_MAX_USES`（默认 10）

### 5.2 工具替换（Tool Substitution）

Bedrock 不认识 `web_search_20250305` 这种 type。Proxy 在调用 Bedrock 前，将工具列表做如下变换：

```
原始工具列表:                        替换后工具列表:
┌─────────────────────────┐          ┌─────────────────────────┐
│ {type: "web_search_     │          │ {name: "web_search",    │
│  20250305", ...}        │   ──►    │  description: "Search   │
│                         │          │  the web for current    │
│                         │          │  information...",       │
│                         │          │  input_schema: {        │
│                         │          │    query: string        │
│                         │          │  }}                     │
├─────────────────────────┤          ├─────────────────────────┤
│ {name: "get_weather",   │          │ {name: "get_weather",   │
│  ...}  (其他用户 tool)   │   ──►    │  ...}  (原样保留)        │
└─────────────────────────┘          └─────────────────────────┘
```

对于 `web_search_20260209`（dynamic filtering），额外追加一个 `bash_code_execution` 工具：

```json
{
  "name": "bash_code_execution",
  "description": "Execute a bash command to process or filter data...",
  "input_schema": {
    "type": "object",
    "properties": {
      "command": {"type": "string"},
      "restart": {"type": "boolean"}
    },
    "required": ["command"]
  }
}
```

### 5.3 Beta Header 过滤

Web search beta header（如 `web-search-2025-03-05`）是 Anthropic 特有的，Bedrock 不认识。Proxy 在调用 Bedrock 前过滤掉这些 header：

```
客户端发送:  anthropic-beta: "web-search-2025-03-05,tool-examples-2025-10-29"
过滤后传给 Bedrock: anthropic-beta: "tool-examples-2025-10-29"
```

过滤集合：`{"web-search-2025-03-05", "web-search-2026-02-09"}`

### 5.4 Agentic Loop（代理循环）

这是 Web Search 实现的核心。以非流式为例：

```python
# 简化伪代码
async def handle_request():
    messages = request.messages
    all_content = []
    result_registry = {}
    search_count = 0
    total_input_tokens = 0
    total_output_tokens = 0
    sandbox_session = None  # 仅 web_search_20260209

    # 为 dynamic filtering 创建沙箱
    if config.type == "web_search_20260209":
        sandbox_session = await standalone_service._get_or_create_session(None)

    try:
        for iteration in range(MAX_ITERATIONS):  # MAX_ITERATIONS = 25
            # 1. 构建工具列表（替换 web_search）
            tools = _build_tools_for_request(request.tools, config)

            # 2. 注入引用系统提示
            system = _inject_citation_system_prompt(request.system)

            # 3. 调用 Bedrock（始终非流式）
            response = await bedrock_service.invoke_model(
                model=model, messages=messages, tools=tools,
                system=system, anthropic_beta=filtered_beta, ...
            )

            # 4. 累积 token 计数
            total_input_tokens += response.usage.input_tokens
            total_output_tokens += response.usage.output_tokens

            # 5. 检查是否有 web_search / bash 工具调用
            tool_uses = _find_all_intercepted_tool_uses(response.content)

            if not tool_uses or response.stop_reason != "tool_use":
                # 没有工具调用 → 循环结束
                all_content.extend(_convert_to_server_tool_use(response.content))
                break

            # 6. 执行工具调用
            tool_results = []
            for tu in tool_uses:
                if tu["name"] == "web_search":
                    if search_count < max_uses:
                        results = await _execute_search(tu["input"]["query"], config)
                        tool_results.append(_build_web_search_tool_result(tu["id"], results))
                        search_count += 1
                    else:
                        tool_results.append(_build_web_search_error(tu["id"], "max_uses_exceeded"))
                elif tu["name"] == "bash_code_execution":
                    tool_results.append(await _execute_bash_tool(tu, sandbox_session))

            # 7. 转换为 server_tool_use 并累积
            all_content.extend(_convert_to_server_tool_use(response.content))
            all_content.extend(tool_results)

            # 8. 构建续接消息
            messages = _build_continuation_messages(messages, response.content, tool_results)
    finally:
        # 清理沙箱
        if sandbox_session:
            await standalone_service.sandbox_executor.close_session(session_id)

    # 9. 后处理引用
    all_content = _post_process_citations(all_content, result_registry)

    # 10. 组装最终响应
    return MessageResponse(
        id=request_id, content=all_content,
        usage=Usage(
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            server_tool_use={"web_search_requests": search_count}
        ),
        stop_reason="end_turn"
    )
```

#### 循环终止条件

| 条件 | 行为 |
|------|------|
| `stop_reason != "tool_use"` | Claude 给出最终回答，循环结束 |
| 达到 `MAX_ITERATIONS`（25） | 强制终止，使用最后一次响应 |
| `search_count >= max_uses` | 搜索次数耗尽，返回 `max_uses_exceeded` 错误给 Claude |

### 5.5 Tool ID 转换

Anthropic 官方 API 中 server-managed tool 使用 `srvtoolu_` 前缀，而 Bedrock 返回的是 `toolu_` 前缀。Proxy 做双向转换：

```
Bedrock 返回:   type: "tool_use",        id: "toolu_01Abc..."
客户端看到:     type: "server_tool_use", id: "srvtoolu_bdrk_01Abc..."

Bedrock 续接:   type: "tool_result",     tool_use_id: "toolu_01Abc..."  (内部使用原始 ID)
```

转换方法 `_convert_to_server_tool_use()`:
- `tool_use` → `server_tool_use`
- `toolu_XXX` → `srvtoolu_bdrk_XXX`
- 非 `web_search` / `bash_code_execution` 的 tool_use 不做转换（透传给客户端）

### 5.6 搜索执行

#### 搜索提供商架构

```
                SearchProvider (ABC)
                       │
           ┌───────────┴───────────┐
           │                       │
  TavilySearchProvider    BraveSearchProvider
  (tavily-python SDK)     (httpx → Brave API)
```

**接口定义**:

```python
class SearchProvider(ABC):
    async def search(
        self, query: str, max_results: int = 5,
        allowed_domains: List[str] = None,
        blocked_domains: List[str] = None,
        user_location: dict = None,
    ) -> List[SearchResult]:
        ...
```

**Tavily 提供商**:
- 使用 `tavily-python` SDK
- 同步 SDK → `asyncio.run_in_executor()` 包装为异步
- `search_depth: "advanced"` 获取更完整的内容
- 原生支持 `include_domains` / `exclude_domains`

**Brave 提供商**:
- 使用 `httpx.AsyncClient` 直接调用 Brave Search API
- 域名白名单通过 `site:` 前缀注入查询（`(site:a.com OR site:b.com) query`）
- 域名黑名单通过后处理 `DomainFilter` 过滤

**工厂函数**:

```python
def create_search_provider(provider=None, api_key=None) -> SearchProvider:
    provider = provider or settings.web_search_provider  # 默认 "tavily"
    api_key = api_key or settings.web_search_api_key
    if provider == "tavily":
        return TavilySearchProvider(api_key=api_key)
    elif provider == "brave":
        return BraveSearchProvider(api_key=api_key)
```

#### 域名过滤

搜索执行后，额外经过 `DomainFilter` 二次过滤：

```python
domain_filter = DomainFilter(
    allowed_domains=config.allowed_domains,
    blocked_domains=config.blocked_domains,
)
results = domain_filter.filter_results(results)
```

支持子域名匹配：`docs.example.com` 匹配 `example.com`。

#### 搜索结果格式

搜索结果被封装为 `web_search_tool_result` content block：

```json
{
  "type": "web_search_tool_result",
  "tool_use_id": "srvtoolu_bdrk_01Abc...",
  "content": [
    {
      "type": "web_search_result",
      "url": "https://example.com/article",
      "title": "Example Article",
      "encrypted_content": "VGhpcyBpcyB0aGUg..."
    }
  ]
}
```

`encrypted_content` 是页面内容的 Base64 编码（模拟官方 API 的加密内容字段）。

### 5.7 引用系统（Citations）

Proxy 通过 **提示注入 + 后处理** 两步实现引用功能。

#### Step 1: 系统提示注入

在每次 Bedrock 调用前，向 system prompt 追加引用指令：

```
When you use web search results to answer questions, you MUST cite sources
using numbered references in square brackets. The search results are numbered
[Result 1], [Result 2], etc. After each factual claim based on a search result,
append the result number like this: 'Python 3.13 was released in October 2024 [1].'
Multiple sources can be combined: 'This is widely used [1][3].'
Every claim from search results MUST have at least one [N] citation.
Do NOT omit citations.
```

#### Step 2: 结果编号与注册

搜索结果在传给 Claude 的 `tool_result` 消息中被编号：

```
[Result 1]
Title: Example Article
URL: https://example.com/article
Content: (decoded content)

---

[Result 2]
Title: Another Article
...
```

同时注册到 `result_registry`:

```python
result_registry[1] = {
    "url": "https://example.com/article",
    "title": "Example Article",
    "content": "decoded content...",
    "encrypted_index": base64("1")  # "MQ=="
}
```

#### Step 3: 后处理引用标记

Claude 的回答中会包含 `[1]`、`[3]` 等标记。`_post_process_citations()` 将这些标记转换为正式的 citation 对象：

```
输入 (Claude 原始文本):
  "Python 3.13 was released in October 2024 [1]. It supports new features [1][3]."

输出 (拆分后的 text blocks):
  [{
    "type": "text",
    "text": "Python 3.13 was released in October 2024",
    "citations": [{
      "type": "web_search_result_location",
      "url": "https://...",
      "title": "...",
      "encrypted_index": "MQ==",
      "cited_text": "first 150 chars of content..."
    }]
  }, {
    "type": "text",
    "text": ". It supports new features",
    "citations": [
      {"type": "web_search_result_location", ...},  // [1]
      {"type": "web_search_result_location", ...}   // [3]
    ]
  }, {
    "type": "text",
    "text": "."
  }]
```

这与 Anthropic 官方 API 的 citation 格式完全一致。

### 5.8 Dynamic Filtering（`web_search_20260209`）

Dynamic Filtering 是 `web_search_20260209` 独有的特性，允许 Claude 编写代码来处理搜索结果。

#### 工作流程

```
1. Claude 搜索 → 获得搜索结果
2. Claude 认为需要进一步处理
3. Claude 调用 bash_code_execution:
   {
     "name": "bash_code_execution",
     "input": {
       "command": "python3 -c \"import json; data = {...}; print(json.dumps(filtered))\""
     }
   }
4. Proxy 在 Docker 沙箱中执行代码
5. 执行结果返回给 Claude
6. Claude 基于处理后的数据生成最终回答
```

#### 沙箱执行

- 复用 Proxy 已有的 `StandaloneCodeExecutionService`
- Docker 容器内执行，网络隔离
- 每个请求独立的 session
- 请求结束后自动清理

#### 响应中的 bash 相关 block

```json
{
  "type": "server_tool_use",
  "id": "srvtoolu_bdrk_01Xyz...",
  "name": "bash_code_execution",
  "input": {"command": "python3 -c \"...\""}
}
```

```json
{
  "type": "bash_code_execution_tool_result",
  "tool_use_id": "srvtoolu_bdrk_01Xyz...",
  "content": {
    "type": "bash_code_execution_result",
    "stdout": "===== AAPL vs GOOGL =====\n...",
    "stderr": "",
    "return_code": 0
  }
}
```

---

## 6. 流式响应（Streaming）

### 6.1 混合流式架构

Web search 使用 **混合流式** 方案：

- **Bedrock 调用**: 始终使用非流式 `invoke_model()`
- **客户端响应**: 通过 SSE（Server-Sent Events）逐步发送

**原因**: Agentic Loop 需要获取完整的 Bedrock 响应来判断是否有工具调用。使用非流式调用简化了编排逻辑。

### 6.2 SSE 事件序列

```
event: message_start
data: {"type": "message_start", "message": {"id": "...", "usage": {"input_tokens": N}}}

# --- Iteration 1: Claude calls web_search ---

event: content_block_start
data: {"type": "content_block_start", "index": 0,
       "content_block": {"type": "server_tool_use", "id": "srvtoolu_...", "name": "web_search"}}

event: content_block_delta
data: {"type": "content_block_delta", "index": 0,
       "delta": {"type": "input_json_delta", "partial_json": "{\"query\":\"...\"}"}}

event: content_block_stop
data: {"type": "content_block_stop", "index": 0}

event: content_block_start
data: {"type": "content_block_start", "index": 1,
       "content_block": {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_...",
                         "content": [...]}}

event: content_block_stop
data: {"type": "content_block_stop", "index": 1}

# --- Iteration 2: Claude generates final answer ---

event: content_block_start
data: {"type": "content_block_start", "index": 2,
       "content_block": {"type": "text", "text": ""}}

event: content_block_delta
data: {"type": "content_block_delta", "index": 2,
       "delta": {"type": "text_delta", "text": "According to..."}}

event: content_block_stop
data: {"type": "content_block_stop", "index": 2}

event: message_delta
data: {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
       "usage": {"output_tokens": N}}

event: message_stop
data: {"type": "message_stop"}
```

### 6.3 Usage 追踪

流式模式下的 token 追踪在 API 层完成：

```python
async def _web_search_stream_with_usage():
    accumulated = {"input": 0, "output": 0}
    try:
        async for sse_event in web_search_service.handle_request_streaming(...):
            # 从 SSE 事件中提取 token 数
            if "message_start" in event:
                accumulated["input"] = usage.input_tokens
            elif "message_delta" in event:
                accumulated["output"] = usage.output_tokens
            yield sse_event
    finally:
        usage_tracker.record_usage(
            input_tokens=accumulated["input"],
            output_tokens=accumulated["output"], ...
        )
```

---

## 7. 多轮对话支持

当客户端在后续轮次中发送包含 `web_search_tool_result` 的消息时（例如多轮对话历史），converter 需要将其转换为 Bedrock 能理解的格式。

### 转换逻辑 (`anthropic_to_bedrock.py`)

```python
# 客户端发送的 web_search_tool_result block
elif block_type == "web_search_tool_result":
    ws_content = block.get("content", [])
    if isinstance(ws_content, list):
        # 成功结果：提取每个 web_search_result 的内容
        text_parts = []
        for item in ws_content:
            title = item.get("title", "")
            url = item.get("url", "")
            enc = item.get("encrypted_content", "")
            # 尝试 base64 解码
            try:
                page_content = base64.b64decode(enc).decode("utf-8")
            except:
                page_content = enc
            text_parts.append(f"Title: {title}\nURL: {url}\nContent: {page_content}")
        result_text = "\n\n---\n\n".join(text_parts)
    elif ws_content.get("type") == "web_search_tool_result_error":
        # 错误结果
        result_text = f"Error: {ws_content.get('error_code', 'unknown')}"

    # 转换为 Bedrock toolResult 格式
    bedrock_block = {
        "toolResult": {
            "toolUseId": tool_use_id,
            "content": [{"text": result_text}],
            "status": "success"
        }
    }
```

---

## 8. 配置项

### 8.1 环境变量

| 变量 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `ENABLE_WEB_SEARCH` | bool | `True` | Web search 功能总开关 |
| `WEB_SEARCH_PROVIDER` | str | `"tavily"` | 搜索提供商：`tavily` 或 `brave` |
| `WEB_SEARCH_API_KEY` | str | (必填) | 搜索提供商 API Key |
| `WEB_SEARCH_MAX_RESULTS` | int | `5` | 每次搜索返回结果数 |
| `WEB_SEARCH_DEFAULT_MAX_USES` | int | `10` | 默认最大搜索次数（客户端可通过 `max_uses` 覆盖） |

### 8.2 客户端配置（通过 tool 定义）

| 字段 | 类型 | 说明 |
|------|------|------|
| `max_uses` | int | 单次请求最大搜索次数 |
| `allowed_domains` | list[str] | 域名白名单 |
| `blocked_domains` | list[str] | 域名黑名单 |
| `user_location` | object | 用户位置信息（country, city, region, timezone） |

### 8.3 内部常量

| 常量 | 值 | 说明 |
|------|-----|------|
| `MAX_ITERATIONS` | 25 | Agentic Loop 最大迭代次数 |
| `WEB_SEARCH_TOOL_TYPES` | `{"web_search_20250305", "web_search_20260209"}` | 支持的工具类型 |
| `WEB_SEARCH_BETA_HEADERS` | `{"web-search-2025-03-05", "web-search-2026-02-09"}` | 需要过滤的 beta header |
| `BASH_TOOL_NAME` | `"bash_code_execution"` | Dynamic filtering 工具名 |

---

## 9. 错误处理

### 9.1 错误类型

| 错误码 | 触发条件 | 行为 |
|--------|---------|------|
| `max_uses_exceeded` | 搜索次数超过 `max_uses` | 返回错误 block 给 Claude，Claude 基于已有结果回答 |
| `unavailable` | 搜索 API 调用失败 | 返回错误 block 给 Claude |
| `too_many_requests` | 搜索 API 限流 | 返回错误 block 给 Claude |
| `invalid_input` | 查询格式错误 | 返回错误 block 给 Claude |
| `query_too_long` | 查询过长 | 返回错误 block 给 Claude |

### 9.2 错误传播策略

- 搜索失败 → 生成 `web_search_tool_result_error` block → Claude 收到错误信息，可能重试或基于已有结果回答
- 沙箱创建失败 → `ValueError` → HTTP 400 返回客户端
- Bedrock 调用失败 → 异常上抛 → HTTP 500/429 返回客户端
- 循环超过 MAX_ITERATIONS → 日志告警 → 使用最后一次响应

---

## 10. 响应格式

### 10.1 非流式响应

```json
{
  "id": "msg-xxx",
  "type": "message",
  "role": "assistant",
  "model": "claude-sonnet-4-6",
  "stop_reason": "end_turn",
  "usage": {
    "input_tokens": 18426,
    "output_tokens": 1420,
    "server_tool_use": {
      "web_search_requests": 2
    }
  },
  "content": [
    {"type": "server_tool_use", "id": "srvtoolu_bdrk_...", "name": "web_search", "input": {"query": "..."}},
    {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_bdrk_...", "content": [...]},
    {"type": "server_tool_use", "id": "srvtoolu_bdrk_...", "name": "bash_code_execution", "input": {...}},
    {"type": "bash_code_execution_tool_result", "tool_use_id": "srvtoolu_bdrk_...", "content": {...}},
    {"type": "text", "text": "According to...", "citations": [
      {"type": "web_search_result_location", "url": "...", "title": "...", "encrypted_index": "MQ==", "cited_text": "..."}
    ]},
    {"type": "text", "text": " more text without citations"}
  ]
}
```

### 10.2 与 Anthropic 官方 API 的兼容性

经过对比测试验证，Proxy 的 web search 响应与官方 API 在以下方面**完全兼容**：

| 特性 | 兼容性 |
|------|--------|
| Content block 类型 (`server_tool_use`, `web_search_tool_result`, `text`) | 完全一致 |
| Tool ID 前缀 (`srvtoolu_bdrk_`) | 一致 |
| Citation 格式 (`web_search_result_location`) | 完全一致 |
| `encrypted_content` 编码（Base64） | 一致 |
| `usage.server_tool_use.web_search_requests` | 一致 |
| Dynamic filtering blocks (`bash_code_execution_tool_result`) | 一致 |
| Token 消耗 | 差异 <5%（自然波动） |

---

## 11. 性能特征

| 指标 | 预估值 | 说明 |
|------|--------|------|
| 单次迭代延迟 | 1-2s | 主要来自 Bedrock API 调用 + 搜索 API |
| 工具替换开销 | <1ms | 纯内存操作 |
| 引用后处理 | <50ms | 正则匹配 + 字符串拆分 |
| 总请求延迟 | 2-8s | 取决于迭代次数（通常 2-3 次） |
| 内存占用 | ~50KB/请求 | result_registry + messages 累积 |

---

## 12. 限制与已知问题

1. **搜索结果质量取决于提供商**: Tavily 和 Brave 的搜索结果可能与 Anthropic 自有搜索引擎有差异
2. **引用基于提示工程**: 引用标记 `[N]` 依赖 Claude 遵循系统提示，极少数情况下可能缺失或格式不正确
3. **非流式 Bedrock 调用**: 内部始终使用非流式调用，在多次迭代的场景下第一个 token 的延迟较高
4. **沙箱依赖 Docker**: Dynamic filtering 需要 Docker 运行环境，ECS Fargate 不支持
5. **MAX_ITERATIONS 硬限制**: 25 次迭代上限，极端复杂的搜索场景可能不够

---
---

# Part 2: Web Fetch Tool

## 13. 概述

Web Fetch 是 Anthropic API 提供的另一种 server-managed tool，允许 Claude 主动抓取指定 URL 的完整页面内容。与 Web Search（搜索关键词获取摘要列表）不同，Web Fetch 针对单个 URL 获取完整文档。

| 对比维度 | Web Search | Web Fetch |
|---------|-----------|-----------|
| **输入** | 搜索关键词（query） | 具体 URL |
| **输出** | 多条搜索结果摘要 | 单个 URL 的完整页面内容 |
| **结果格式** | `web_search_tool_result` + `web_search_result[]` | `web_fetch_tool_result` + `web_fetch_result` |
| **内容编码** | `encrypted_content`（Base64） | `source.data`（纯文本或 Base64） |
| **引用类型** | `web_search_result_location` | `char_location` |
| **典型场景** | "搜索 Python 最新版本" | "读取 https://docs.python.org/3/whatsnew.html 的内容" |

### 13.1 支持的工具版本

| 工具类型 | Beta Header | 特性 |
|---------|-------------|------|
| `web_fetch_20250910` | `web-fetch-2025-09-10` | 标准 URL 抓取 + 引用 |
| `web_fetch_20260209` | `web-fetch-2026-02-09` | URL 抓取 + **Dynamic Filtering**（bash 代码执行） |

---

## 14. 源码结构

```
app/
├── schemas/
│   └── web_fetch.py                   # Pydantic 数据模型（86 行）
└── services/
    ├── web_fetch_service.py           # 核心服务：Agentic Loop 编排（~1,500 行）
    └── web_fetch/
        ├── __init__.py                # 模块导出
        └── providers.py               # Fetch 提供商：Httpx / Tavily（313 行）
```

复用了 Web Search 的以下模块：
- `app/services/web_search/domain_filter.py` — 域名过滤
- `app/services/standalone_code_execution_service.py` — Dynamic filtering 沙箱执行

---

## 15. 与 Web Search 的架构关系

Web Fetch 的实现**完全遵循 Web Search 的 Agentic Loop 模式**，核心架构一致：

```
               共享模式                         差异点
  ┌───────────────────────────┐    ┌─────────────────────────────┐
  │ • Agentic Loop 编排       │    │ • 工具名: web_fetch vs       │
  │ • Tool Substitution       │    │   web_search                │
  │ • ID 转换 (srvtoolu_)    │    │ • 输入: url vs query        │
  │ • Beta Header 过滤        │    │ • Provider: HTTP fetch vs   │
  │ • Citation 系统           │    │   Search API                │
  │ • Dynamic Filtering       │    │ • 结果格式: document block  │
  │ • 混合流式架构            │    │   vs result array           │
  │ • 错误处理模式            │    │ • 引用类型: char_location   │
  │ • 多轮对话转换            │    │   vs web_search_result_     │
  └───────────────────────────┘    │   location                  │
                                   │ • 额外字段: retrieved_at,   │
                                   │   media_type, max_content_  │
                                   │   tokens                    │
                                   └─────────────────────────────┘
```

---

## 16. Fetch 提供商

### 16.1 提供商架构

```
                FetchProvider (ABC)
                       │
           ┌───────────┴───────────┐
           │                       │
  HttpxFetchProvider        TavilyFetchProvider
  (默认，无需 API Key)     (需要 Tavily 付费计划)
```

### 16.2 HttpxFetchProvider（默认）

直接通过 `httpx.AsyncClient` 抓取 URL，内置 HTML → 纯文本转换：

```python
class HttpxFetchProvider(FetchProvider):
    async def fetch(self, url: str, max_content_tokens: int = None) -> FetchResult:
        # 1. 验证 URL
        _validate_url(url)  # http(s)://, 长度 < 250

        # 2. HTTP GET 请求（支持重定向）
        response = await self.client.get(url)  # follow_redirects=True

        # 3. 根据 Content-Type 处理
        if is_pdf:
            content = base64.b64encode(response.content)  # PDF → base64
        elif is_html:
            title = _extract_title(raw_html)     # 提取 <title>
            content = _html_to_text(raw_html)    # HTML → 纯文本
        elif is_text:
            content = raw_text                   # JSON/CSV/plain → 直接返回
        else:
            raise FetchError("unsupported_content_type")

        # 4. Token 限制截断
        content = _apply_token_limit(content, max_content_tokens)

        return FetchResult(url=final_url, title=title, content=content, media_type=...)
```

**HTML → 纯文本转换**（`_html_to_text()`）：
- 移除 `<script>`, `<style>`, HTML 注释
- 将块级元素 (`<p>`, `<div>`, `<h1-6>`, `<li>`) 转换为换行
- 移除所有 HTML 标签
- 解码 HTML 实体（`&amp;` → `&`）
- 折叠多余空白

**优势**：无需任何外部 API Key，零依赖。

### 16.3 TavilyFetchProvider

使用 Tavily Extract API，需要付费 Tavily 计划：

```python
class TavilyFetchProvider(FetchProvider):
    async def fetch(self, url, max_content_tokens=None) -> FetchResult:
        response = await loop.run_in_executor(
            None, lambda: self.client.extract(urls=[url])
        )
        content = response["results"][0]["raw_content"]
        return FetchResult(url=url, title=title, content=content, ...)
```

### 16.4 工厂函数

```python
def create_fetch_provider(provider=None) -> FetchProvider:
    provider = provider or getattr(settings, 'web_fetch_provider', 'httpx')
    if provider == "tavily":
        return TavilyFetchProvider(api_key=settings.web_search_api_key)
    return HttpxFetchProvider()  # 默认
```

---

## 17. 数据模型

### 17.1 工具定义（客户端请求）

```json
{
  "type": "web_fetch_20250910",
  "name": "web_fetch",
  "max_uses": 5,
  "allowed_domains": ["docs.python.org"],
  "blocked_domains": ["spam.com"],
  "citations": {"enabled": true},
  "max_content_tokens": 100000
}
```

与 Web Search 的区别：
- 新增 `citations`：明确控制是否启用引用（Web Search 默认启用）
- 新增 `max_content_tokens`：限制单次抓取的最大内容量
- 无 `user_location` 字段（URL 抓取无需地理位置）

### 17.2 结果格式（`web_fetch_tool_result`）

```json
{
  "type": "web_fetch_tool_result",
  "tool_use_id": "srvtoolu_bdrk_01Abc...",
  "content": {
    "type": "web_fetch_result",
    "url": "https://docs.python.org/3/whatsnew/3.13.html",
    "retrieved_at": "2026-03-03T08:30:00Z",
    "content": {
      "type": "document",
      "source": {
        "type": "text",
        "media_type": "text/plain",
        "data": "What's New In Python 3.13\n..."
      },
      "title": "What's New in Python 3.13"
    }
  }
}
```

**与 Web Search 结果格式的关键差异**：

| 特性 | Web Search | Web Fetch |
|------|-----------|-----------|
| **content 类型** | `List[web_search_result]` （数组） | `web_fetch_result`（单个对象） |
| **内容字段** | `encrypted_content`（Base64） | `source.data`（纯文本或 Base64） |
| **额外元数据** | `page_age` | `retrieved_at`, `source.media_type` |
| **文档嵌套** | 扁平结构 | `content.source` 嵌套结构（document block） |

### 17.3 错误格式

```json
{
  "type": "web_fetch_tool_result",
  "tool_use_id": "srvtoolu_bdrk_01Abc...",
  "content": {
    "type": "web_fetch_tool_error",
    "error_code": "url_not_accessible"
  }
}
```

错误码：

| 错误码 | 说明 |
|--------|------|
| `invalid_input` | URL 格式无效 |
| `url_too_long` | URL 超过 250 字符 |
| `url_not_allowed` | URL 被 `blocked_domains` 拦截 |
| `url_not_accessible` | HTTP 请求失败（404/500/超时等） |
| `too_many_requests` | 目标站点限流（429） |
| `unsupported_content_type` | 不支持的内容类型（如视频） |
| `max_uses_exceeded` | 抓取次数超过 `max_uses` |

---

## 18. Agentic Loop 流程差异

Web Fetch 的 Agentic Loop 与 Web Search 完全相同（见 [5.4 节](#54-agentic-loop代理循环)），仅在工具执行步骤有差异：

### Web Search 的工具执行
```
Claude 调用: tool_use(web_search, input={query: "Python 3.13"})
Proxy 执行:  search_provider.search(query="Python 3.13")
返回结果:    web_search_tool_result → content: [result1, result2, ...]  (多条)
```

### Web Fetch 的工具执行
```
Claude 调用: tool_use(web_fetch, input={url: "https://..."})
Proxy 执行:
  1. 域名检查: _check_domain_allowed(url, config)
  2. 内容抓取: fetch_provider.fetch(url, max_content_tokens)
  3. 构建结果: web_fetch_tool_result → content: {web_fetch_result}  (单条)
返回结果:    含 document block（source + title + retrieved_at）
```

### 域名检查

Web Fetch 在执行抓取前额外检查 URL 域名：

```python
def _check_domain_allowed(self, url: str, config: WebFetchToolDefinition) -> Optional[str]:
    """Check URL against allowed/blocked domain lists.
    Returns error_code if blocked, None if allowed."""
    domain = urlparse(url).netloc.lower()

    if config.blocked_domains:
        if DomainFilter._matches_any(domain, config.blocked_domains):
            return "url_not_allowed"

    if config.allowed_domains:
        if not DomainFilter._matches_any(domain, config.allowed_domains):
            return "url_not_allowed"

    return None  # URL is allowed
```

这比 Web Search 多了一层**前置域名检查**（Web Search 只在搜索后做后处理过滤），因为 Web Fetch 是直接访问用户指定的 URL。

---

## 19. 引用系统差异

### Web Search 引用
```json
{
  "type": "web_search_result_location",
  "url": "https://...",
  "title": "...",
  "encrypted_index": "MQ==",
  "cited_text": "first 150 chars..."
}
```

### Web Fetch 引用
```json
{
  "type": "char_location",
  "document_index": 0,
  "start_char_index": 42,
  "end_char_index": 180,
  "document_title": "What's New in Python 3.13"
}
```

两者都使用相同的 **提示注入 + `[N]` 标记后处理** 机制，但最终输出的 citation 对象格式不同。

> 注：当前实现中，Web Fetch 的引用后处理复用了 `web_search_result_location` 格式，与官方 `char_location` 格式有细微差异。这是已知的兼容性 trade-off。

---

## 20. 多轮对话中的 `web_fetch_tool_result` 转换

当客户端在后续轮次发送包含 `web_fetch_tool_result` 的对话历史时，converter 需要将其转换为 Bedrock 的 `toolResult` 格式：

```python
# app/converters/anthropic_to_bedrock.py
elif block_type == "web_fetch_tool_result":
    wf_content = block.get("content", {})
    if isinstance(wf_content, dict):
        wf_type = wf_content.get("type", "")
        if wf_type == "web_fetch_result":
            doc = wf_content.get("content", {})     # document block
            source = doc.get("source", {})
            data = source.get("data", "")            # 文本内容
            title = doc.get("title", "")
            url = wf_content.get("url", "")
            result_text = f"Title: {title}\nURL: {url}\nContent: {data}"
        elif wf_type == "web_fetch_tool_error":
            result_text = f"Error: {wf_content.get('error_code', 'unknown')}"

    # → Bedrock toolResult 格式
    bedrock_block = {
        "toolResult": {
            "toolUseId": tool_use_id,
            "content": [{"text": result_text}],
            "status": "success"
        }
    }
```

核心逻辑：从嵌套的 `content.source.data` 中提取文本内容，拼接为纯文本传给 Bedrock。

---

## 21. 配置项

| 变量 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `ENABLE_WEB_FETCH` | bool | `True` | Web fetch 功能总开关 |
| `WEB_FETCH_DEFAULT_MAX_USES` | int | `20` | 默认最大抓取次数 |
| `WEB_FETCH_DEFAULT_MAX_CONTENT_TOKENS` | int | `100000` | 默认最大内容 token 数 |

> Web Fetch 默认使用 HttpxFetchProvider，**无需额外 API Key**。如需使用 Tavily，设置 `web_fetch_provider=tavily` 并配置 `WEB_SEARCH_API_KEY`。

---

## 22. Web Search vs Web Fetch 完整对比

| 维度 | Web Search | Web Fetch |
|------|-----------|-----------|
| **工具类型** | `web_search_20250305`, `web_search_20260209` | `web_fetch_20250910`, `web_fetch_20260209` |
| **Beta Header** | `web-search-2025-03-05`, `web-search-2026-02-09` | `web-fetch-2025-09-10`, `web-fetch-2026-02-09` |
| **输入参数** | `query`（搜索关键词） | `url`（具体 URL） |
| **Provider** | Tavily Search / Brave Search（需 API Key） | HttpxFetchProvider（默认，无需 Key）/ Tavily Extract |
| **结果数量** | 每次搜索 5 条（可配置） | 每次抓取 1 条 |
| **结果格式** | `web_search_tool_result` + `[web_search_result, ...]` | `web_fetch_tool_result` + `web_fetch_result` |
| **内容编码** | `encrypted_content`（Base64） | `source.data`（纯文本 / Base64 PDF） |
| **引用类型** | `web_search_result_location` | `char_location` |
| **额外元数据** | `page_age` | `retrieved_at`, `media_type`, `title` |
| **域名检查** | 后处理过滤 | 前置域名检查 + 后处理过滤 |
| **PDF 支持** | 无 | 有（base64 传递） |
| **默认 max_uses** | 10 | 20 |
| **Dynamic Filtering** | `web_search_20260209` | `web_fetch_20260209` |
| **源码行数** | ~1,836 行 | ~1,900 行 |
| **Agentic Loop** | 完全相同的编排模式 | 完全相同的编排模式 |
