# Blog Design: 在 Amazon Bedrock 上实现 Anthropic Web Search 与 Web Fetch

**日期**: 2026-03-03
**类型**: AWS 技术博客
**前作**: [使用Amazon Bedrock + 自建ECS Docker Sandbox实现Agent 程序化工具调用Programmatic Tool Calling](https://aws.amazon.com/cn/blogs/china/programmatic-tool-calling-agent-using-bedrock-and-ecs-docker-sandbox/)
**目标读者**: AWS 开发者/架构师
**语言**: 中文，8,000-12,000 字
**图表格式**: Mermaid
**代码风格**: 核心片段 + 注释，长逻辑用伪代码
**侧重**: 实现原理 + 对比验证并重

---

## 源材料

1. 架构设计文档: `docs/architecture/web-search-implementation.md`
2. 对比测试报告:
   - `tests/reports/web_search_dynamic_comparison_2026-03-03.md`
   - `tests/reports/web_fetch_dynamic_comparison_2026-03-03.md`
3. Anthropic 官方博客: https://claude.com/blog/improved-web-search-with-dynamic-filtering

---

## 博客结构（方案 A：按功能模块拆分）

### 第一节：前言 + 背景（~1,200 字）

**前言**（~400 字）:
- 衔接前作：上一篇介绍了 PTC，本篇继续扩展 Server-Managed Tools
- 引出问题：Anthropic API 的 web_search 和 web_fetch 是服务端工具，Bedrock InvokeModel API 不支持
- 本文介绍如何在 Proxy 层实现，客户端使用 Anthropic Python SDK 无需修改

**背景**（~800 字）:
- Web Search 简介：引用 Anthropic 官方博客内容
- Dynamic Filtering：准确率 +11%、token 效率 +24%（Anthropic 基准数据）
- Web Fetch 简介：与 Web Search 对比（URL vs 关键词，单页内容 vs 多条摘要）
- Bedrock 的局限：不支持 server-managed tool 声明

### 第二节：整体架构概览（~800 字）

- 核心思路一句话：拦截 → 替换 → Agentic Loop → 组装
- **Mermaid 架构总览图**：Client → Proxy → Bedrock + Search/Fetch Provider
- 关键设计决策：
  - 客户端透明
  - 工具替换策略
  - 混合流式（内部非流式，对客户端 SSE）
- 支持的工具版本表格（4 个版本）
- 简化源码目录树

### 第三节：Web Search 实现（~1,900 字）

**3.1 工具替换**（~400 字）:
- Before/After 对比表格
- web_search_20250305 → 标准 tool definition

**3.2 Agentic Loop 核心编排**（~800 字）:
- **Mermaid 流程图**：循环过程
- 伪代码（~15 行）
- 循环终止条件表格

**3.3 搜索提供商**（~400 字）:
- SearchProvider ABC → Tavily / Brave
- 域名过滤机制

**3.4 引用系统**（~500 字）:
- 三步机制：提示注入 → 编号注册 → 后处理转换
- **Mermaid 流程图**
- citation 格式示例（web_search_result_location）

**3.5 Tool ID 转换**（~200 字）:
- toolu_ → srvtoolu_bdrk_ 前缀转换

### 第四节：Web Fetch 实现（~1,400 字）

设计思路：强调复用，只讲差异点

**4.1 工具替换差异**（~200 字）

**4.2 Fetch 提供商**（~400 字）:
- HttpxFetchProvider（默认，无需 API Key）/ TavilyFetchProvider
- 核心流程：URL 验证 → HTTP GET → Content-Type → 转换 → Token 截断

**4.3 结果格式差异**（~300 字）:
- 对比表格 + 精简 JSON 示例

**4.4 域名检查差异**（~150 字）:
- Web Fetch 增加前置域名检查

**4.5 引用类型差异**（~150 字）:
- web_search_result_location vs char_location

### 第五节：Dynamic Filtering —— 代码沙箱执行（~1,000 字）

**5.1 什么是 Dynamic Filtering**（~300 字）:
- Anthropic 基准数据
- 核心思想
- web_search_20260209 和 web_fetch_20260209 共享

**5.2 实现机制**（~500 字）:
- 追加 bash_code_execution 工具
- **Mermaid 流程图**
- 引用前作沙箱实现
- 与 PTC 沙箱的差异：一次性执行 vs 暂停/恢复

**5.3 响应格式**（~200 字）:
- 精简 JSON 示例

### 第六节：对比验证（~1,400 字）

**6.1 测试方法**（~200 字）

**6.2 Web Search Dynamic Filtering 对比**（~600 字）:
- 测试场景：AAPL vs GOOGL P/E 比率
- 基本指标对比表格
- Dynamic Filtering 计算结果完全一致
- 格式兼容性表格

**6.3 Web Fetch Dynamic Filtering 对比**（~400 字）:
- 测试场景：统计 "hammer" 出现次数
- 3 轮迭代一致，抓取内容逐字节一致
- JSON 结构兼容性表格

**6.4 对比结论**（~200 字）:
- 汇总表格
- Proxy 与官方 API 完全等价

### 第七节：部署与配置（~500 字）

- 环境变量表格
- 搜索提供商选择指南（Tavily vs Brave）
- Docker 沙箱要求（引用前作部署方案）
- 客户端使用示例（5-10 行 Python）

### 第八节：总结与展望（~300 字）

- 回顾本文内容
- 系列回顾（PTC + Web Search + Web Fetch）
- 已知限制
- 展望

---

## 篇幅估算

| 章节 | 预估字数 |
|------|---------|
| 1. 前言 + 背景 | ~1,200 |
| 2. 整体架构概览 | ~800 |
| 3. Web Search 实现 | ~1,900 |
| 4. Web Fetch 实现 | ~1,400 |
| 5. Dynamic Filtering | ~1,000 |
| 6. 对比验证 | ~1,400 |
| 7. 部署与配置 | ~500 |
| 8. 总结与展望 | ~300 |
| **合计** | **~8,500** |
