# Agent工具调用新范式：逆向工程 Anthropic Claude Programmatic Tool Calling 功能

> 本文深入解析 Anthropic 最新发布的 Programmatic Tool Calling (PTC) 技术，并介绍如何通过自托管 Docker Sandbox 方案实现完全兼容的 PTC 功能，让任意大模型都能享受这一革命性的工具调用范式。

## 一、背景：传统 Tool Use 的瓶颈

在构建 AI Agent 时，工具调用（Tool Use / Function Calling）是连接模型与外部世界的桥梁。然而，传统的工具调用模式存在明显的效率瓶颈：

```
┌─────────────────────────────────────────────────────────────┐
│                    传统 Tool Use 流程                        │
├─────────────────────────────────────────────────────────────┤
│  用户请求 → 模型推理 → 工具调用1 → 模型推理 → 工具调用2      │
│          → 模型推理 → 工具调用3 → 模型推理 → 最终响应        │
│                                                             │
│  问题：N 次工具调用 = N+1 次模型推理                         │
│       所有中间结果都进入上下文，消耗大量 Token                │
└─────────────────────────────────────────────────────────────┘
```

以一个典型的业务场景为例：**"查询工程团队哪些成员的 Q3 差旅费用超标？"**

传统方式需要：
1. 获取团队成员列表 → 20 人
2. 为每人获取费用记录 → 20 次工具调用，每次返回 50-100 条明细
3. 获取预算标准 → 多次调用
4. **所有 2000+ 条费用明细都进入模型上下文**
5. 模型手动汇总、比对、筛选

这种方式导致：
- **Token 消耗巨大**：中间数据全部进入上下文
- **延迟累积**：每次工具调用都需要一次完整的模型推理
- **准确性下降**：模型需要在自然语言中处理大量数据，容易出错

## 二、Programmatic Tool Calling：代码编排工具调用

2024 年 11 月，Anthropic 发布了 **Programmatic Tool Calling (PTC)** 功能，从根本上改变了工具调用范式。

### 2.1 核心思想

PTC 的核心思想是：**让模型生成 Python 代码来编排工具调用，而不是逐个请求工具**。

```
┌─────────────────────────────────────────────────────────────┐
│                Programmatic Tool Calling 流程               │
├─────────────────────────────────────────────────────────────┤
│  用户请求 → 模型生成 Python 代码 → 沙箱执行代码              │
│                                    ↓                        │
│                              循环调用工具1,2,3...            │
│                              数据处理、过滤、聚合            │
│                                    ↓                        │
│                              返回 print() 输出              │
│          → 模型基于摘要输出给出最终响应                      │
│                                                             │
│  优势：1 次模型推理 + 1 次代码执行                          │
│       只有最终 print 输出进入上下文                         │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 官方 API 使用方式

根据 Anthropic 官方文档，启用 PTC 需要：

```python
# 1. 标记工具可被代码执行环境调用
tools = [
    {
        "type": "code_execution_20250825",
        "name": "code_execution"
    },
    {
        "name": "get_expenses",
        "description": "获取员工费用记录",
        "input_schema": {...},
        "allowed_callers": ["code_execution_20250825"]  # 关键配置
    }
]

# 2. 使用 beta API 调用
response = client.beta.messages.create(
    model="claude-sonnet-4-5-20250929",
    betas=["advanced-tool-use-2025-11-20"],
    tools=tools,
    messages=[{"role": "user", "content": "分析 Q3 费用超标情况"}]
)
```

### 2.3 模型生成的代码示例

当启用 PTC 后，Claude 会生成类似这样的代码来完成任务：

```python
import asyncio
import json

# 获取团队成员
team_json = await get_team_members(department="engineering")
team = json.loads(team_json)

# 并行获取所有成员的费用数据
expense_tasks = [
    get_expenses(employee_id=m["id"], quarter="Q3")
    for m in team
]
expenses_results = await asyncio.gather(*expense_tasks)

# 分析超标情况
exceeded = []
for member, exp_json in zip(team, expenses_results):
    expenses = json.loads(exp_json)
    # 只计算已批准的费用
    total = sum(e["amount"] for e in expenses if e["status"] == "approved")

    # 获取该员工的预算限额
    budget_json = await get_custom_budget(user_id=member["id"])
    budget = json.loads(budget_json)

    if total > budget["travel_budget"]:
        exceeded.append({
            "name": member["name"],
            "spent": total,
            "limit": budget["travel_budget"],
            "over": total - budget["travel_budget"]
        })

# 只输出超标人员摘要
print(json.dumps(exceeded, indent=2))
```

### 2.4 效率提升数据

根据 Anthropic 官方测试数据：

| 指标 | 传统 Tool Use | PTC | 提升 |
|------|--------------|-----|------|
| 平均 Token 消耗 | 43,588 | 27,297 | **37%↓** |
| 模型推理次数 | N+1 次 | 1-2 次 | **显著降低** |
| 知识检索准确率 | 25.6% | 28.5% | **11%↑** |
| GIA 基准测试 | 46.5% | 51.2% | **10%↑** |

## 三、自托管方案：Docker Sandbox 实现

官方 PTC 使用 Anthropic 托管的沙箱环境，但在某些场景下我们需要：
- 完全的控制权和自定义能力
- 支持非 Claude 模型
- 私有化部署
- 自定义依赖包

为此，我们实现了一个**完全兼容官方 PTC 机制**的自托管 Docker Sandbox 方案。

### 3.1 整体架构

```
┌──────────────────────────────────────────────────────────────────┐
│                         整体系统架构                              │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│   ┌─────────────┐    ┌──────────────────┐    ┌───────────────┐  │
│   │  用户应用    │───▶│   Orchestrator   │───▶│  Claude API   │  │
│   └─────────────┘    └────────┬─────────┘    └───────────────┘  │
│                               │                                  │
│                               ▼                                  │
│                      ┌────────────────┐                         │
│                      │  ToolRegistry  │                         │
│                      │  工具注册/执行  │                         │
│                      └────────┬───────┘                         │
│                               │                                  │
│                               ▼                                  │
│   ┌───────────────────────────────────────────────────────────┐ │
│   │                   SandboxExecutor                          │ │
│   │  ┌─────────────────────────────────────────────────────┐  │ │
│   │  │              Docker Container                        │  │ │
│   │  │  ┌─────────────────┐    ┌─────────────────────────┐ │  │ │
│   │  │  │  Runner Script  │◀──▶│    IPC 工具调用通道     │ │  │ │
│   │  │  │  执行用户代码    │    │  stdin/stdout/stderr   │ │  │ │
│   │  │  └─────────────────┘    └─────────────────────────┘ │  │ │
│   │  │                                                      │  │ │
│   │  │  安全限制: 无网络 | 只读文件系统 | 资源限制          │  │ │
│   │  └─────────────────────────────────────────────────────┘  │ │
│   └───────────────────────────────────────────────────────────┘ │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### 3.2 核心组件

#### 3.2.1 SandboxExecutor - Docker 沙箱执行器

`SandboxExecutor` 是核心组件，负责在隔离的 Docker 容器中执行代码：

```python
from sandboxed_ptc import SandboxExecutor, SandboxConfig, ToolRegistry

# 配置沙箱
config = SandboxConfig(
    image="python:3.11-slim",
    memory_limit="256m",
    cpu_quota=50000,           # 50% CPU
    timeout_seconds=60.0,
    network_disabled=True,     # 禁用网络
    read_only=True,            # 只读文件系统
    enable_session_reuse=True, # 启用容器复用
    session_timeout_seconds=270.0  # 会话超时 4.5 分钟
)

executor = SandboxExecutor(registry, config)

# 执行代码
result, session_id = await executor.execute(
    code="x = 10\nprint(x * 2)",
    reuse_session=True
)
# 输出: 20

# 复用会话，变量状态保持
result, session_id = await executor.execute(
    code="print(x + 5)",  # x 仍然存在
    session_id=session_id
)
# 输出: 15
```

#### 3.2.2 IPC 通信协议

容器内代码与主进程通过自定义 IPC 协议通信：

```
┌─────────────────┐                    ┌─────────────────┐
│  Docker 容器     │                    │    主进程       │
│  (用户代码)      │                    │ (SandboxExecutor)│
├─────────────────┤                    ├─────────────────┤
│                 │   工具调用请求       │                 │
│  await tool()  ─┼──── stderr ────────▶│  解析请求       │
│                 │                      │       │         │
│                 │                      │       ▼         │
│                 │                      │  ToolRegistry   │
│                 │                      │  执行真实工具    │
│                 │   工具调用结果       │       │         │
│  继续执行     ◀─┼──── stdin ─────────┼───────┘         │
│                 │                      │                 │
│  print(结果)    │   最终输出          │                 │
│                ─┼──── stdout ────────▶│  返回给模型     │
└─────────────────┘                    └─────────────────┘
```

IPC 消息格式：

```python
# 工具调用请求 (Container → Host, via stderr)
__PTC_TOOL_CALL__{"call_id": "uuid", "tool_name": "get_expenses",
                  "arguments": {"employee_id": "ENG001", "quarter": "Q3"}}__PTC_END_CALL__

# 工具调用结果 (Host → Container, via stdin)
__PTC_TOOL_RESULT__{"call_id": "uuid", "result": [...], "error": null}__PTC_END_RESULT__

# 最终输出 (Container → Host, via stdout)
__PTC_OUTPUT__{"success": true, "output": "分析结果...", "error": null}__PTC_END_OUTPUT__
```

#### 3.2.3 安全设计

Docker 容器运行时应用多层安全限制：

```yaml
security:
  network_disabled: true          # 禁用网络访问
  read_only: true                 # 只读文件系统
  user: sandbox (non-root)        # 非特权用户
  cap_drop: [ALL]                 # 移除所有 Linux capabilities
  security_opt: [no-new-privileges]  # 禁止提权
  mem_limit: 256m                 # 内存限制
  cpu_quota: 50000                # CPU 限制 (50%)
```

### 3.3 与官方 PTC 的对比

| 特性 | 官方 PTC | Docker Sandbox |
|------|---------|---------------|
| 沙箱环境 | Anthropic 托管 | 自托管 Docker |
| 控制权 | 有限 | **完全** |
| 自定义依赖 | 不支持 | **完全支持** |
| 网络访问 | 受限 | **可配置** |
| 会话持久化 | 支持 | 支持 |
| 调试能力 | 有限 | **完全** |
| 部署灵活性 | 受限 | **灵活** |
| 成本 | 按使用计费 | 本地资源 |
| **非 Claude 模型支持** | 不支持 | **支持** |

## 四、实战演示：多模型 PTC 效果

我们使用 `notebook3.ipynb` 中的示例来演示 PTC 在不同模型上的效果。

### 4.1 测试场景：企业差旅费用审计

#### 业务背景

我们模拟了一个**企业费用管理系统**，包含以下 Mock API 工具：

| 工具名称 | 功能描述 | 返回数据量 |
|---------|---------|-----------|
| `get_team_members(department)` | 获取部门团队成员列表 | 8 人（工程部） |
| `get_expenses(employee_id, quarter)` | 获取员工某季度所有费用明细 | 20-50 条/人 |
| `get_custom_budget(user_id)` | 查询员工自定义预算额度 | 1 条 |

#### 数据特点

**团队成员数据**：工程部门有 8 名成员，涵盖 junior、mid、senior、staff、principal 五个级别。

**费用明细数据**：每位员工每季度有 20-50 条费用记录，每条记录包含丰富的元数据：
- 基础信息：expense_id、date、amount、currency、status
- 分类信息：category（travel/lodging/meals/software/equipment/conference/office/internet）
- 审计信息：approved_by、receipt_url、payment_method、project_code、notes
- 商户信息：store_name、store_location

**预算规则**：
- 标准差旅预算：$5,000/季度
- 部分员工有自定义预算例外（如 Staff Engineer $8,000、Principal Engineer $12,000）
- **关键规则**：只有 `status="approved"` 的费用才计入预算

#### 测试任务

测试问题：
> "Which engineering team members exceeded their Q3 travel budget? Standard quarterly travel budget is $5,000. However, some employees have custom budget limits. For anyone who exceeded the $5,000 standard budget, check if they have a custom budget exception."

#### 为什么这是一个好的 PTC 测试用例？

1. **多步骤工具调用**：需要先获取团队成员 → 逐个获取费用明细 → 对超标者查询自定义预算
2. **大量中间数据**：8 人 × 20-50 条费用 = 160-400 条费用记录，传统模式下全部进入上下文
3. **复杂过滤逻辑**：只统计 approved 状态、travel/lodging 类别的费用
4. **条件分支判断**：先与 $5,000 比较，超标再查自定义预算，再次比较
5. **数据聚合输出**：最终只需输出超标人员名单，而非全部明细

### 4.2 测试代码

```python
import anthropic
from anthropic.types.beta import BetaTextBlock, BetaToolUseBlock

# 配置 PTC 工具
ptc_tools = [
    {
        "type": "code_execution_20250825",
        "name": "code_execution",
    },
    {
        "name": "get_team_members",
        "description": "获取部门团队成员列表",
        "input_schema": {...},
        "allowed_callers": ["code_execution_20250825"]
    },
    {
        "name": "get_expenses",
        "description": "获取员工费用记录",
        "input_schema": {...},
        "allowed_callers": ["code_execution_20250825"]
    },
    {
        "name": "get_custom_budget",
        "description": "获取员工自定义预算",
        "input_schema": {...},
        "allowed_callers": ["code_execution_20250825"]
    }
]

# 通过代理服务调用（支持多种模型）
client = anthropic.Anthropic(
    api_key=os.environ.get('API_KEY'),
    base_url=os.environ.get('BASE_URL')  # 代理服务地址
)

response = client.beta.messages.create(
    model=model_id,  # 可以是 Claude、Qwen、MiniMax 等
    betas=["advanced-tool-use-2025-11-20"],
    tools=ptc_tools,
    messages=[{"role": "user", "content": query}]
)
```

### 4.3 多模型测试结果

我们在以下模型上测试了相同的 PTC 任务：
> 该任务的正确答案是,如果人名或者数字金额不对，则判断为❌ 不准确 
1. **Alice Chen** 
   - Budget: $5,000.00 | Actual: $9,876.54 | **+$4,876.54 over**

2. **Emma Johnson** 
   - Budget: $5,000.00 | Actual: $5,266.02 | **+$266.02 over**

3. **Grace Taylor** 
   - Budget: $5,000.00 | Actual: $6,474.46 | **+$1,474.46 over**


| 模型 | API工具调用次数 | Token 消耗 | 执行时间 | 结果准确性 |
|------|-------------|-----------|---------|-----------|
| Claude Sonnet 4.5 | 4 | ~12.9k | 19.4s |  ✅ 准确 |
| Claude Opus 4.5 | 4 | ~13.2k | 25s | ✅ 准确 |
| MiniMax-M2 | 5 | ~18.9k | 61s | ❌ 不准确 |
| Qwen3-Coder-480B | 13 | ~10.3k | 12s | ✅ 准确 |
| Qwen3-235B-2507 | 7 | ~15.9k | 47s | ❌ 不准确 |
| Qwen3-Next-80B | 18 | ~9.9k | 23s | ✅ 准确 |

注：Qwen3-Coder-480B和Qwen3-Next-80B的API调用次数偏多，是因为没有按照指示生成并行执行代码，而是串行调用API工具，导致耗时增加。  

同样我在非PTC模式下也做了同样测试：
| 模型 | API工具调用次数 | Token 消耗 | 执行时间 | 结果准确性 |
|------|-------------|-----------|---------|-----------|
| Claude Sonnet 4.5 | 4 | ~123k | 29s |  ❌ 不准确  |
| Claude Opus 4.5 | 4 | ~122k | 26s | ❌ 不准确 |
| MiniMax-M2 | 5 | ~148.8k | 56s | ❌ 不准确 |
| Qwen3-Coder-480B | 12 | ~481.5k | 43s | ❌ 不准确 |
| Qwen3-235B-2507 | 7 | ~259.7k | 46s | ❌ 不准确 |
| Qwen3-Next-80B | 12 | ~750.7k | 50s | ❌ 不准确 |

#### PTC vs 非 PTC 模式对比分析

| 模型 | Token (PTC) | Token (非PTC) | Token 节省率 | PTC准确 | 非PTC准确 |
|------|------------|--------------|-------------|---------|----------|
| Claude Sonnet 4.5 | 12.9k | 123k | **89.5%** | ❌ | ❌ |
| Claude Opus 4.5 | 13.2k | 122k | **89.2%** | ✅ | ❌ |
| MiniMax-M2 | 18.9k | 148.8k | **87.3%** | ❌ | ❌ |
| Qwen3-Coder-480B | 10.3k | 481.5k | **97.9%** | ✅ | ❌ |
| Qwen3-235B-2507 | 15.9k | 259.7k | **93.9%** | ❌ | ❌ |
| Qwen3-Next-80B | 9.9k | 750.7k | **98.7%** | ✅ | ❌ |

**核心发现**：

1. **Token 消耗大幅降低**：所有模型在 PTC 模式下 Token 消耗均降低 **87%-99%**，平均节省约 **93%**
2. **PTC 模式准确率更高**：在 PTC 模式下有 4 个模型给出正确答案，而非 PTC 模式所有模型都无法给出100%正确答案
3. **Qwen3 系列表现亮眼**：Qwen3-Coder-480B 和 Qwen3-Next-80B 在 PTC 模式下表现优异，Token 节省率分别达 97.9% 和 98.7%
4. **PTC 提升推理质量**：通过代码编排工具调用，模型可以更精确地处理数据过滤、聚合逻辑，减少在自然语言中处理大量数据导致的错误

**关键发现**：通过我们的自托管 PTC 方案，**非 Claude 模型也能使用 PTC 范式**，显著提升工具调用效率。

### 4.4 模型生成的代码对比

**Claude Sonnet 4.5 生成的代码**：

```python
import asyncio
import json

# 获取工程团队成员
team_json = await get_team_members(department="engineering")
team = json.loads(team_json)

# 并行获取所有成员的 Q3 费用
expense_tasks = [get_expenses(employee_id=m["id"], quarter="Q3") for m in team]
all_expenses = await asyncio.gather(*expense_tasks)

exceeded_members = []
for member, exp_json in zip(team, all_expenses):
    expenses = json.loads(exp_json)
    total_travel = sum(
        e["amount"] for e in expenses
        if e["category"] == "travel" and e["status"] == "approved"
    )

    if total_travel > 5000:
        # 检查是否有自定义预算
        budget_json = await get_custom_budget(user_id=member["id"])
        budget = json.loads(budget_json)

        limit = budget["travel_budget"]
        if total_travel > limit:
            exceeded_members.append({
                "name": member["name"],
                "spent": total_travel,
                "limit": limit,
                "exceeded_by": total_travel - limit
            })

print(json.dumps(exceeded_members, indent=2, ensure_ascii=False))
```

**Qwen3-Next-80B 生成的代码**（风格略有不同但功能等效）：

```python
import json
import asyncio

team_data = await get_team_members(department="engineering")
members = json.loads(team_data)

results = []
for member in members:
    expenses_data = await get_expenses(employee_id=member["id"], quarter="Q3")
    expenses = json.loads(expenses_data)

    travel_total = sum(
        exp["amount"] for exp in expenses
        if exp["category"] == "travel" and exp["status"] == "approved"
    )

    if travel_total > 5000:
        budget_data = await get_custom_budget(user_id=member["id"])
        budget_info = json.loads(budget_data)
        actual_limit = budget_info["travel_budget"]

        if travel_total > actual_limit:
            results.append(f"{member['name']}: ${travel_total:.2f} (limit: ${actual_limit})")

print("Exceeded budget:\\n" + "\\n".join(results) if results else "No one exceeded budget")
```

## 五、PTC 方案的核心价值

### 5.1 不仅仅是 Claude

**我们方案的最大价值在于：PTC 范式不再是 Claude 专属**。

通过 Docker Sandbox + API 代理的组合，任何支持 Function Calling 的大模型都可以使用 PTC：

```python
# 支持的模型列表
supported_models = [
    "claude-sonnet-4-5-20250929",
    "claude-opus-4-5-20251101",
    "qwen.qwen3-coder-480b-a35b-v1:0",
    "qwen.qwen3-next-80b-a3b",
    "minimax.minimax-m2",
    "deepseek.deepseek-v3",
    # ... 任何支持 tool_use 的模型
]
```

### 5.2 适用场景

PTC 特别适合以下场景：

| 场景 | 传统方式问题 | PTC 优势 |
|------|------------|---------|
| **批量数据处理** | 中间结果撑爆上下文 | 只返回聚合摘要 |
| **多工具组合** | N 次推理延迟累积 | 1 次推理 + 代码执行 |
| **条件逻辑** | 每步都需模型决策 | 代码自动处理分支 |
| **并行调用** | 串行等待 | asyncio.gather 并行 |
| **数据过滤** | 模型手动筛选 | 代码高效过滤 |

### 5.3 成本对比示例（基于实际测试）

以本文 notebook3.ipynb 中的**企业差旅费用审计**测试为例：

**任务**：分析 8 名工程团队成员的 Q3 差旅费用是否超标

**数据规模**：
- 8 人 × 20-50 条费用明细 = **160-400 条费用记录**
- 每条记录包含 15+ 个字段（expense_id、date、amount、category、status、merchant...）

```
┌─────────────────────────────────────────────────────────────────┐
│                    实际测试数据对比                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  非 PTC 模式 (以 Claude Sonnet 4.5 为例):                       │
│  ├─ API 调用次数: 4 次                                          │
│  ├─ Token 消耗: ~123,000                                        │
│  ├─ 执行时间: 29s                                               │
│  └─ 所有费用明细进入上下文，模型手动筛选汇总                      │
│                                                                 │
│  PTC 模式 (以 Claude Sonnet 4.5 为例):                          │
│  ├─ API 调用次数: 4 次                                          │
│  ├─ Token 消耗: ~12,900                                         │
│  ├─ 执行时间: 19.4s                                             │
│  └─ 代码在沙箱中处理数据，只返回超标人员摘要                      │
│                                                                 │
│  ═══════════════════════════════════════════════════════════    │
│  Token 节省: 89.5%  |  时间节省: 33%  |  准确性: PTC✅ 非PTC❌    │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**成本估算**（以 Claude Sonnet 定价 $3/$15 per 1M tokens）：

| 模式 | Token 消耗 | 输入成本 | 输出成本 | 总成本 |
|------|-----------|---------|---------|--------|
| 非 PTC | ~123,000 | $0.369 | ~$0.15 | **~$0.52** |
| PTC | ~12,900 | $0.039 | ~$0.02 | **~$0.06** |
| **节省** | 89.5% | - | - | **~88%** |

如果是生产环境每天执行 1000 次类似查询：
- 非 PTC：$520/天 → **$15,600/月**
- PTC：$60/天 → **$1,800/月**
- **月节省：$13,800（88%）**

## 六、100% Anthropic API 兼容：anthropic_api_converter

如果你希望在 AWS Bedrock 上使用完全兼容 Anthropic API 的服务（包括 PTC），可以使用开源项目 [anthropic_api_converter](https://github.com/xiehust/anthropic_api_converter)。

### 6.1 项目简介

这是一个轻量级 API 转换代理服务，让你无需修改代码即可：

- 在 **Claude Code** 中使用 Bedrock 上的 Qwen3-Coder 等模型
- 在 **Claude Agent SDK** 中混合使用不同模型
- 完整支持 **Programmatic Tool Calling** API

### 6.2 核心特性

```
┌──────────────────────────────────────────────────────────────┐
│                    anthropic_api_converter                    │
├──────────────────────────────────────────────────────────────┤
│  ✅ 零代码迁移 - 完全兼容 Anthropic API 格式                  │
│  ✅ PTC 支持 - 业界首个在 Bedrock 上实现兼容 PTC 的代理       │
│  ✅ 多模型支持 - Claude/Qwen/DeepSeek/MiniMax...             │
│  ✅ 流式响应 - 支持 SSE 实时流式输出                         │
│  ✅ 工具调用 - 完整的 Function Calling 支持                  │
│  ✅ 企业级 - API Key 管理、速率限制、用量追踪                │
└──────────────────────────────────────────────────────────────┘
```

### 6.3 快速使用

```bash
# 设置环境变量，让 Claude Code 通过代理使用 Bedrock 模型
export CLAUDE_CODE_USE_BEDROCK=0
export ANTHROPIC_BASE_URL=http://your-proxy-url.com
export ANTHROPIC_API_KEY=sk-xxxx
export ANTHROPIC_DEFAULT_SONNET_MODEL=qwen.qwen3-coder-480b-a35b-v1:0

# 启动 Claude Code，自动使用 Qwen3-Coder
claude
```

### 6.4 PTC 配置

```python
# 在代理服务中启用 PTC
ENABLE_PROGRAMMATIC_TOOL_CALLING=True
PTC_SANDBOX_IMAGE=python:3.11-slim
PTC_SESSION_TIMEOUT=270
PTC_EXECUTION_TIMEOUT=60
PTC_MEMORY_LIMIT=256m
PTC_NETWORK_DISABLED=True
```

## 七、总结

Programmatic Tool Calling 代表了 AI Agent 工具调用的新范式：

1. **效率革命**：从 N 次推理降至 1-2 次，Token 消耗降低 37%+
2. **代码编排**：用 Python 表达复杂逻辑，比自然语言更精确
3. **上下文隔离**：中间数据不进入模型上下文，只保留关键输出
4. **并行执行**：通过 asyncio.gather 实现真正的并行工具调用

通过我们的 **Docker Sandbox 自托管方案**，这一能力不再是 Claude 专属：

- ✅ 完全兼容官方 PTC 协议
- ✅ 支持任意大模型（Qwen、DeepSeek、MiniMax...）
- ✅ 私有化部署，完全可控
- ✅ 自定义依赖包和执行环境

**这是 Agent 工具调用从"对话式"向"程序化"演进的重要一步。**

---

## 参考资料

1. **Anthropic 官方文档**
   - [Introducing advanced tool use on the Claude Developer Platform](https://www.anthropic.com/engineering/advanced-tool-use)
   - [Programmatic Tool Calling Documentation](https://platform.claude.com/docs/en/agents-and-tools/tool-use/programmatic-tool-calling)
   - [PTC Cookbook](https://github.com/anthropics/claude-cookbooks/blob/main/tool_use/programmatic_tool_calling_ptc.ipynb)

2. **开源项目**
   - [Sandboxed PTC - Docker Sandbox 实现](https://github.com/xiehust/claude_ptc)
   - [anthropic_api_converter - Anthropic API 代理](https://github.com/xiehust/anthropic_api_converter)

3. **相关技术**
   - [Building Effective Agents - Anthropic Research](https://www.anthropic.com/research/building-effective-agents)
   - [Code Execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp)

---

> **作者注**：本文代码示例均来自实际项目，可在 GitHub 仓库中找到完整实现。欢迎 Star 和贡献！
