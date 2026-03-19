# Web Search & Web Fetch Blog Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Write an 8,000-12,000 字中文 AWS 技术博客，介绍 Proxy 如何在 Amazon Bedrock 上实现 Anthropic 的 Web Search 和 Web Fetch server-managed tools。

**Architecture:** 按功能模块拆分为 8 节：前言背景 → 整体架构 → Web Search → Web Fetch → Dynamic Filtering → 对比验证 → 部署配置 → 总结。代码以核心片段+注释为主，架构图用 Mermaid。

**Tech Stack:** Markdown, Mermaid diagrams

**Source Materials:**
- Architecture doc: `docs/architecture/web-search-implementation.md`
- Test report (Web Search): `tests/reports/web_search_dynamic_comparison_2026-03-03.md`
- Test report (Web Fetch): `tests/reports/web_fetch_dynamic_comparison_2026-03-03.md`
- Anthropic blog: https://claude.com/blog/improved-web-search-with-dynamic-filtering
- Previous blog (PTC): https://aws.amazon.com/cn/blogs/china/programmatic-tool-calling-agent-using-bedrock-and-ecs-docker-sandbox/

**Output file:** `docs/blog/web-search-fetch-on-bedrock.md`

---

### Task 1: Create blog file with front matter and Section 1 (前言 + 背景)

**Files:**
- Create: `docs/blog/web-search-fetch-on-bedrock.md`
- Reference: `docs/architecture/web-search-implementation.md:1-30` (背景与动机)
- Reference: Anthropic blog (Dynamic Filtering benchmarks: accuracy +11%, token efficiency +24%)

**Step 1: Create blog file with title and Section 1**

Write the following content to `docs/blog/web-search-fetch-on-bedrock.md`:

- **标题**: 在 Amazon Bedrock 上实现 Anthropic Web Search 与 Web Fetch —— Server-Managed Tools 的自建 Proxy 方案
- **前言**（~400 字）:
  - 衔接前作 PTC 博客（附链接），介绍本文是系列第二篇
  - 引出问题：Anthropic API 的 web_search 和 web_fetch 是服务端工具，Bedrock 不支持
  - 预告本文内容：实现原理 + 对比验证
- **背景**（~800 字）:
  - **Web Search 简介**：让 Claude 搜索互联网获取实时信息。两个版本：`web_search_20250305`（标准）、`web_search_20260209`（Dynamic Filtering）
  - **Dynamic Filtering**：引用 Anthropic 博客数据（BrowseComp: Sonnet 33.3%→46.6%, Opus 45.3%→61.6%；平均准确率 +11%、token 效率 +24%）。核心思想：Claude 自动编写代码过滤搜索结果
  - **Web Fetch 简介**：与 Web Search 对比表格（输入 URL vs 关键词，单页完整内容 vs 多条摘要）
  - **Bedrock 的局限**：InvokeModel API 不支持 server-managed tool 声明

**Step 2: Verify Section 1**

Review: 确认前作链接正确，Anthropic 博客数据准确引用，Web Search/Fetch 对比清晰。

**Step 3: Commit**

```bash
git add docs/blog/web-search-fetch-on-bedrock.md
git commit -m "docs(blog): add Section 1 - introduction and background"
```

---

### Task 2: Section 2 (整体架构概览)

**Files:**
- Modify: `docs/blog/web-search-fetch-on-bedrock.md`
- Reference: `docs/architecture/web-search-implementation.md:66-133` (整体架构、组件职责)

**Step 1: Write Section 2**

追加以下内容：

- **核心思路**（一句话）: Proxy 拦截 server-managed tool → 替换为普通工具 → Agentic Loop 编排执行 → 组装官方格式响应
- **Mermaid 架构总览图**: 使用 `graph LR` 或 `graph TD` 展示：
  ```
  Client(Anthropic SDK) → Proxy API Layer → [判断分支: Web Search / Web Fetch]
  Web Search → WebSearchService(Agentic Loop) → Bedrock + Tavily/Brave
  Web Fetch → WebFetchService(Agentic Loop) → Bedrock + Httpx/Tavily
  ```
- **关键设计决策**（3 点）:
  1. 客户端透明：使用 Anthropic SDK 无需修改
  2. 工具替换：server-managed tool → 标准 tool definition
  3. 混合流式：内部非流式调用 Bedrock，对客户端 SSE 流式输出
- **支持的工具版本表格**: 4 行（web_search x2 + web_fetch x2），列：工具类型、Beta Header、特性
- **简化源码目录树**: 核心文件 + 行数

**Step 2: Verify Section 2**

Review: Mermaid 图语法正确，工具版本信息与架构文档一致。

**Step 3: Commit**

```bash
git add docs/blog/web-search-fetch-on-bedrock.md
git commit -m "docs(blog): add Section 2 - architecture overview"
```

---

### Task 3: Section 3 (Web Search 实现)

**Files:**
- Modify: `docs/blog/web-search-fetch-on-bedrock.md`
- Reference: `docs/architecture/web-search-implementation.md:136-485` (工具替换、Agentic Loop、搜索提供商、引用系统、Tool ID)

**Step 1: Write Section 3.1 - 工具替换**

- Before/After 对比表格: 原始 `{type: "web_search_20250305"}` → 替换后 `{name: "web_search", input_schema: {query: string}}`
- 简要说明：其他用户定义的 tools 原样保留

**Step 2: Write Section 3.2 - Agentic Loop 核心编排**

- **Mermaid 流程图** (`flowchart TD`):
  ```
  Start → 构建工具列表 → 调用Bedrock → 检查stop_reason
  stop_reason == tool_use? → Yes → 执行web_search → 注入结果 → 回到调用Bedrock
  stop_reason == tool_use? → No → 后处理引用 → 组装响应 → End
  ```
- 伪代码（~15 行）: 参考架构文档 5.4 节，精简为核心循环逻辑，添加中文注释
- 循环终止条件表格: 3 行（stop_reason != tool_use / MAX_ITERATIONS=25 / max_uses 耗尽）

**Step 3: Write Section 3.3 - 搜索提供商**

- 抽象层: SearchProvider ABC → TavilySearchProvider / BraveSearchProvider
- Tavily 为默认选择的原因（advanced search depth、原生域名过滤）
- 域名过滤: allowed_domains / blocked_domains，支持子域名匹配

**Step 4: Write Section 3.4 - 引用系统**

- 三步机制的 **Mermaid 流程图**:
  ```
  系统提示注入 → Claude回答带[N]标记 → 后处理转换为citation对象
  ```
- 系统提示内容摘要（英文原文）
- 最终输出的 citation JSON 示例（`web_search_result_location` 格式）

**Step 5: Write Section 3.5 - Tool ID 转换**

- `toolu_XXX` → `srvtoolu_bdrk_XXX`
- `tool_use` → `server_tool_use`

**Step 6: Verify Section 3**

Review: Mermaid 图语法正确，伪代码逻辑与架构文档一致，引用系统三步机制完整。

**Step 7: Commit**

```bash
git add docs/blog/web-search-fetch-on-bedrock.md
git commit -m "docs(blog): add Section 3 - Web Search implementation"
```

---

### Task 4: Section 4 (Web Fetch 实现)

**Files:**
- Modify: `docs/blog/web-search-fetch-on-bedrock.md`
- Reference: `docs/architecture/web-search-implementation.md:788-1155` (Web Fetch 部分)

**Step 1: Write Section 4**

- **设计思路**（~200 字）: 强调复用 Web Search 的 Agentic Loop、Tool ID 转换、引用系统、Dynamic Filtering 沙箱，本节只讲差异
- **4.1 工具替换差异**: `{name: "web_fetch", input_schema: {url: string}}`
- **4.2 Fetch 提供商**:
  - HttpxFetchProvider（默认，零依赖）: URL 验证 → HTTP GET → Content-Type 判断 → HTML-to-text / PDF base64 / 纯文本 → Token 截断
  - TavilyFetchProvider（需付费 API Key）
- **4.3 结果格式差异**: 对比表格（Web Search 数组 vs Web Fetch 单对象 + document block）+ 精简 JSON
- **4.4 域名检查差异**: Web Fetch 增加前置域名检查
- **4.5 引用类型差异**: `web_search_result_location` vs `char_location`

**Step 2: Verify Section 4**

Review: 差异点与架构文档一致，没有重复 Web Search 已讲的内容。

**Step 3: Commit**

```bash
git add docs/blog/web-search-fetch-on-bedrock.md
git commit -m "docs(blog): add Section 4 - Web Fetch implementation"
```

---

### Task 5: Section 5 (Dynamic Filtering)

**Files:**
- Modify: `docs/blog/web-search-fetch-on-bedrock.md`
- Reference: `docs/architecture/web-search-implementation.md:488-538` (Dynamic Filtering)
- Reference: PTC blog (沙箱实现)

**Step 1: Write Section 5**

- **5.1 什么是 Dynamic Filtering**（~300 字）:
  - 引用 Anthropic 基准数据
  - 核心思想：搜索/抓取后，Claude 编写 Python 代码过滤分析结果
  - web_search_20260209 和 web_fetch_20260209 共享此特性
- **5.2 实现机制**（~500 字）:
  - 工具替换时追加 `bash_code_execution` 工具
  - **Mermaid 流程图** (`sequenceDiagram`):
    ```
    Claude → Proxy: web_search(query)
    Proxy → Tavily: search
    Tavily → Proxy: results
    Proxy → Claude: tool_result(搜索结果)
    Claude → Proxy: bash_code_execution(python3 -c "...")
    Proxy → Docker Sandbox: execute code
    Docker Sandbox → Proxy: stdout/stderr
    Proxy → Claude: tool_result(执行结果)
    Claude → Proxy: final text answer
    ```
  - 引用前作：沙箱复用 PTC 的 StandaloneCodeExecutionService（附前作链接）
  - PTC 沙箱 vs Dynamic Filtering 沙箱的差异：暂停/恢复 vs 一次性执行
- **5.3 响应格式**（~200 字）:
  - `server_tool_use`(bash_code_execution) + `bash_code_execution_tool_result` 精简 JSON

**Step 2: Verify Section 5**

Review: Mermaid sequence diagram 语法正确，前作引用链接有效。

**Step 3: Commit**

```bash
git add docs/blog/web-search-fetch-on-bedrock.md
git commit -m "docs(blog): add Section 5 - Dynamic Filtering"
```

---

### Task 6: Section 6 (对比验证)

**Files:**
- Modify: `docs/blog/web-search-fetch-on-bedrock.md`
- Reference: `tests/reports/web_search_dynamic_comparison_2026-03-03.md`
- Reference: `tests/reports/web_fetch_dynamic_comparison_2026-03-03.md`

**Step 1: Write Section 6**

- **6.1 测试方法**（~200 字）: 相同 prompt，分别通过 Proxy 和官方 API 调用，对比格式和内容。模型 claude-sonnet-4-6
- **6.2 Web Search Dynamic Filtering 对比**（~600 字）:
  - 测试场景：AAPL vs GOOGL 股价与 P/E 比率
  - 基本指标表格: input_tokens 差异 <1%, output_tokens 差异 <4%, web_search_requests 相同
  - Dynamic Filtering 计算结果表格: AAPL P/E 33.42, GOOGL P/E 28.10（完全一致）
  - 格式兼容性表格: block types、ID 前缀、citation、usage 全部一致
- **6.3 Web Fetch Dynamic Filtering 对比**（~400 字）:
  - 测试场景：抓取 httpbin.org/html 统计 "hammer" 次数
  - 3 轮迭代行为完全一致: fetch → bash → final answer
  - 内容逐字节一致（3,602 chars），结果相同（3 次）
  - JSON 结构兼容性表格
- **6.4 对比结论**（~200 字）:
  - 汇总表格: 6 个维度（Token、搜索行为、Dynamic Filtering、格式、数据准确性、功能完整性）
  - 结论：完全等价，差异仅为搜索结果自然随机性和模型措辞变化

**Step 2: Verify Section 6**

Review: 所有数据与测试报告原文一致，表格数据准确无误。

**Step 3: Commit**

```bash
git add docs/blog/web-search-fetch-on-bedrock.md
git commit -m "docs(blog): add Section 6 - comparison verification"
```

---

### Task 7: Section 7 + 8 (部署配置 + 总结)

**Files:**
- Modify: `docs/blog/web-search-fetch-on-bedrock.md`
- Reference: `docs/architecture/web-search-implementation.md:665-693` (配置项)

**Step 1: Write Section 7 - 部署与配置**

- **环境变量表格**: ENABLE_WEB_SEARCH, WEB_SEARCH_PROVIDER, WEB_SEARCH_API_KEY, WEB_SEARCH_MAX_RESULTS, ENABLE_WEB_FETCH, WEB_FETCH_DEFAULT_MAX_USES, WEB_FETCH_DEFAULT_MAX_CONTENT_TOKENS
- **搜索提供商选择**: Tavily（推荐，原生域名过滤）vs Brave（需 httpx 后处理过滤）
- **Docker 沙箱**: Dynamic Filtering 需要 Docker，ECS 须 EC2 launch type（引用前作）
- **客户端使用示例**: Anthropic Python SDK 调用 web_search 的精简代码（~8 行）

**Step 2: Write Section 8 - 总结与展望**

- 回顾：Proxy 实现了 Web Search + Web Fetch，对客户端完全透明
- 系列回顾：PTC（前作）+ Web Search + Web Fetch = Anthropic API 三大服务端特性
- 已知限制：搜索质量取决于第三方、引用基于提示工程、Dynamic Filtering 需 Docker
- 展望：流式 Agentic Loop 优化、更多搜索提供商

**Step 3: Verify Sections 7-8**

Review: 配置项与架构文档一致，客户端代码示例语法正确。

**Step 4: Commit**

```bash
git add docs/blog/web-search-fetch-on-bedrock.md
git commit -m "docs(blog): add Sections 7-8 - deployment and conclusion"
```

---

### Task 8: Final review and polish

**Files:**
- Modify: `docs/blog/web-search-fetch-on-bedrock.md`

**Step 1: Full review**

通读全文检查：
- 各节过渡是否自然
- Mermaid 图语法是否正确（可用 Mermaid live editor 验证）
- 所有表格数据是否与源材料一致
- 前作引用链接是否正确
- 字数是否在 8,000-12,000 范围内
- 代码片段语法高亮是否正确

**Step 2: Polish and fix issues**

修复发现的问题。

**Step 3: Final commit**

```bash
git add docs/blog/web-search-fetch-on-bedrock.md
git commit -m "docs(blog): final review and polish"
```
