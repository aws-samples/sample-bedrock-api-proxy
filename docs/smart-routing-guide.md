# 智能路由机制说明

## 概述

网关的智能路由系统根据请求内容、配置策略和运行时状态，自动决定将请求路由到哪个模型。核心目标是在保证质量的前提下降低 LLM API 调用成本。

## 路由策略

每个 API Key 可独立配置路由策略（`routing_strategy`），可选值：

| 策略 | 行为 |
|------|------|
| `off` | 不做路由，直接使用客户端指定的模型 |
| `cost` | 从可用模型中选择成本最低的 |
| `quality` | 从可用模型中选择质量最高的（按价格排序，价格越高视为质量越高） |
| `auto` | 使用 RouteLLM 智能分类，按复杂度自动选择强模型或弱模型 |

## Strong Model 与 Weak Model

这是 `auto`（智能路由）策略的核心配置：

- **Strong Model（强模型）**：能力更强、价格更贵的模型，用于处理复杂请求。默认 `claude-sonnet-4-5-20250929`
- **Weak Model（弱模型）**：更轻量、更便宜的模型，用于处理简单请求。默认 `claude-haiku-4-5-20251001`
- **Threshold（分类阈值）**：0.0 ~ 1.0，默认 0.5。值越高，越倾向于使用弱模型（更省钱但可能牺牲质量）

### 工作原理

当路由策略为 `auto` 时，SmartRouter 使用 [RouteLLM](https://github.com/lm-sys/RouteLLM) 库分析用户消息的复杂度：

```
用户消息 → RouteLLM 分类器 → high / low
                                 │        │
                                 ▼        ▼
                           strong_model  weak_model
```

- 复杂度判定为 `high` → 路由到 strong_model
- 复杂度判定为 `low` → 路由到 weak_model
- 分类失败（异常或 RouteLLM 未安装）→ 降级到 strong_model（保质量）

### 额外用途：预算降级

当 API Key 的月度预算使用率超过 80% 时，无论当前策略是什么（只要不是 `off`），都会强制路由到 weak_model，避免超支。

## 环境变量配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ROUTING_ENABLED` | `false` | 路由功能总开关 |
| `SMART_ROUTING_ENABLED` | `false` | 智能路由开关 |
| `SMART_ROUTING_STRONG_MODEL` | `claude-sonnet-4-5-20250929` | 强模型 ID |
| `SMART_ROUTING_WEAK_MODEL` | `claude-haiku-4-5-20251001` | 弱模型 ID |
| `SMART_ROUTING_THRESHOLD` | `0.5` | 分类阈值 |
| `CACHE_AWARE_ROUTING_ENABLED` | `true` | Cache Affinity 开关 |

也可通过 Admin Portal 的智能路由配置面板修改，配置持久化到 DynamoDB 并支持热更新。

## 客户端指定 Model ID 时的行为

当客户端请求中指定了 `model` 字段，路由引擎按以下优先级决策：

```
请求进入
  │
  ▼
① routing_strategy == "off"?
  │── 是 → 使用客户端指定的 model ✅
  │
  ▼
② 请求包含 cache_control（Cache-Active 会话）?
  │── 是 → Cache Affinity 生效，保持客户端指定的 model ✅
  │
  ▼
③ RuleEngine 规则匹配?
  │── 是 → 使用规则的目标 model ⚠️ 覆盖客户端指定值
  │
  ▼
④ 月度预算使用 ≥ 80%?
  │── 是 → 强制降级到 weak_model ⚠️ 覆盖客户端指定值
  │
  ▼
⑤ 按策略路由
  │── cost   → 选最便宜的 model ⚠️ 覆盖
  │── quality → 选最贵的 model ⚠️ 覆盖
  │── auto   → RouteLLM 分类选 strong/weak ⚠️ 覆盖
```

### 总结

| 场景 | 客户端指定的 model 是否生效 |
|------|---------------------------|
| 路由关闭（`off`） | ✅ 生效 |
| Cache-Active 会话 | ✅ 生效（Cache Affinity） |
| 规则命中 | ❌ 被规则目标覆盖 |
| 预算降级 | ❌ 被强制切换到 weak_model |
| cost / quality / auto 策略 | ❌ 被策略选择的 model 覆盖 |

## Cache Affinity（缓存亲和性）

当请求中的 `system`、`messages` 或 `tools` 包含 `cache_control` 块时，说明客户端正在使用 Prompt Cache。此时路由引擎会跳过成本/智能路由，保持使用客户端指定的模型，避免切换模型导致 cache 失效、成本反增。

Cache Affinity 的优先级高于规则引擎和策略路由，仅在以下情况失效：
- 路由策略为 `off`（此时本身就不做路由）
- 当前模型所有 Key 不可用触发 Failover（此时 cache 失效不可避免，会记录警告日志）

## 规则路由（RuleEngine）

规则引擎在策略路由之前执行，支持三种匹配方式：

| 类型 | 说明 | 示例 |
|------|------|------|
| 关键词 | 用户消息包含指定关键词（不区分大小写） | 包含"翻译" → 路由到 haiku |
| 正则表达式 | 用户消息匹配正则 | 匹配 `code review\|PR review` → 路由到 sonnet |
| 模型名 | 请求模型在指定列表中 | 模型为 claude-3-opus → 路由到 sonnet |

多条规则同时命中时，使用第一条（按配置顺序）。规则可通过 Admin Portal 管理和排序。

## Failover 机制

当路由决策确定的模型所有 Key 都不可用时：

1. Failover Manager 按配置的 Failover 链查找备用模型
2. 找到可用备用 → 透明切换，客户端无感知
3. 所有备用都不可用 → 返回 503 Service Unavailable

Failover 链通过 `FAILOVER_CHAINS` 环境变量或 Admin Portal 配置。

## 完整请求流程

```
客户端请求 POST /v1/messages
  │
  ▼
MULTI_PROVIDER_ENABLED?
  │── false → 原有 BedrockService 直接调用（零影响）
  │── true ↓
  │
  ▼
COMPRESSION_ENABLED? → 是则压缩上下文
  │
  ▼
ROUTING_ENABLED?
  │── false → 使用请求指定模型
  │── true → RoutingEngine 决策（上述优先级流程）
  │
  ▼
KeyPoolManager 获取可用 Key
  │── 有 Key → Provider 调用
  │── 无 Key → Failover 或返回错误
  │
  ▼
Provider 调用
  │── 成功 → 返回响应
  │── 429/限流 → 标记 Key 冷却，重试其他 Key
```
