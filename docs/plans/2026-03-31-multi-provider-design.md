# Multi Bedrock Provider Support

**Date**: 2026-03-31
**Status**: Draft

## Overview

支持多个 AWS 账号的 Bedrock 配置。用户在 Admin Portal 可以添加额外的 Provider（不同 AWS 账号的 Bedrock endpoint），创建 API Key 时可以绑定一个 Provider，默认使用环境变量中的原有配置。

## 需求

- 同一服务（Bedrock），不同 AWS 账号
- 认证方式：Bearer Token（`AWS_BEARER_TOKEN_BEDROCK`，推荐）或 AK/SK/Session Token
- 一个 API Key 绑定一个 Provider（默认 = 环境变量配置）
- 凭证使用现有 Fernet 加密模块加密存储
- Provider 配置字段：名称、Region、认证方式、凭证、可选 endpoint URL

## 数据模型

### 新增 DynamoDB 表：`anthropic-proxy-providers`

| 字段 | 类型 | 说明 |
|------|------|------|
| `provider_id` (PK) | string | UUID，唯一标识 |
| `name` | string | 显示名称，如 "Production Account US" |
| `aws_region` | string | Bedrock region，如 "us-east-1" |
| `auth_type` | string | `bearer_token` 或 `ak_sk` |
| `encrypted_credentials` | string | Fernet 加密后的 JSON |
| `endpoint_url` | string (可选) | 自定义 Bedrock endpoint |
| `is_active` | boolean | 是否启用 |
| `created_at` | string | ISO 时间戳 |
| `updated_at` | string | ISO 时间戳 |

`encrypted_credentials` 解密后的结构：
- Bearer Token 模式：`{"bearer_token": "xxx"}`
- AK/SK 模式：`{"access_key_id": "xxx", "secret_access_key": "xxx", "session_token": "xxx"(可选)}`

### API Key 表扩展

在 `anthropic-proxy-api-keys` 表增加字段：
- `provider_id`：关联的 provider（`null` 或空 = 使用默认环境变量配置）

### 默认 Provider

不创建数据库记录，`provider_id` 为空时使用当前逻辑（环境变量中的 AWS 凭证），零迁移成本。

## Bedrock 客户端管理

### 客户端缓存池

`BedrockService` 按 `provider_id` 缓存 boto3 client：

```python
class BedrockService:
    def __init__(self):
        self.default_client = self._create_default_client()  # 现有逻辑
        self._client_cache: Dict[str, boto3.client] = {}     # provider_id → client
        self._cache_lock = threading.Lock()

    def get_client(self, provider_id: Optional[str] = None) -> boto3.client:
        if not provider_id:
            return self.default_client
        with self._cache_lock:
            if provider_id not in self._client_cache:
                self._client_cache[provider_id] = self._create_provider_client(provider_id)
            return self._client_cache[provider_id]
```

### Provider 客户端创建

- 从 DynamoDB 查 provider 配置 → 解密凭证
- `auth_type == "bearer_token"`：设置 `os.environ['AWS_BEARER_TOKEN_BEDROCK']` 后创建 client（需 `threading.Lock` 保证线程安全）
- `auth_type == "ak_sk"`：直接传 `aws_access_key_id/secret/session_token` 给 `boto3.client()`

### 缓存失效

Admin 更新/删除 provider 时清除对应缓存条目。使用 TTL（5 分钟自动过期）+ 主动驱逐双重机制。

### 请求流程

```
请求进入 → auth 中间件提取 api_key_info（含 provider_id）
→ BedrockService.get_client(provider_id) 获取对应 client
→ 后续调用逻辑不变
```

## Admin Portal API

### Provider 管理 CRUD

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/providers` | 创建 provider |
| `GET` | `/api/providers` | 列出所有 providers |
| `GET` | `/api/providers/{id}` | 获取详情（凭证脱敏） |
| `PUT` | `/api/providers/{id}` | 更新配置/凭证 |
| `DELETE` | `/api/providers/{id}` | 删除（检查 API Key 引用） |
| `POST` | `/api/providers/{id}/test` | 测试连通性 |

### API Key 接口扩展

- 创建/更新 API Key 时支持可选 `provider_id` 字段
- 获取 API Key 详情时返回 `provider_id` 和 `provider_name`

### 凭证脱敏规则

- Bearer Token：前4位 + `****` + 后4位
- AK/SK：Access Key 全显示，Secret Key 只显示 `****` + 后4位

## Admin Portal 前端

### Provider 管理页面

- 列表页：name、region、auth_type、状态、关联 API Key 数量
- 创建/编辑表单：name、region、auth_type（下拉动态切换凭证输入框）、endpoint_url
- 测试连接按钮

### API Key 表单扩展

- 新增下拉框 "Provider"，选项为 "默认" + 已配置的 providers 列表

## 文件变更清单

### 新增文件

| 文件 | 说明 |
|------|------|
| `app/db/provider_manager.py` | ProviderManager 类（CRUD + 加密/解密） |
| `app/schemas/provider.py` | Provider 的 Pydantic 模型 |
| `admin_portal/backend/api/providers.py` | Provider 管理 API 路由 |
| `admin_portal/backend/schemas/provider.py` | Admin 端 Provider 请求/响应模型 |
| `admin_portal/frontend/providers.html` | Provider 管理页面 |
| `tests/unit/test_provider_manager.py` | 单元测试 |

### 修改文件

| 文件 | 变更 |
|------|------|
| `app/services/bedrock_service.py` | 客户端缓存池 + `get_client(provider_id)` |
| `app/api/messages.py` | 从 `api_key_info` 取 `provider_id` 传给 service |
| `app/db/dynamodb.py` | 初始化 providers 表 + 注册 ProviderManager |
| `app/middleware/auth.py` | `api_key_info` 中携带 `provider_id` |
| `app/core/config.py` | 新增 providers 表名配置 |
| `admin_portal/backend/api/api_keys.py` | 支持 `provider_id` |
| `admin_portal/backend/schemas/api_key.py` | 增加 `provider_id` 字段 |
| `admin_portal/frontend/api_keys.html` | Provider 下拉选择 |
| `scripts/setup_tables.py` | 创建 providers 表 |

### 不变的部分

- **Converters** — 转换逻辑不受影响
- **Rate limiting / Budget** — 仍按 API Key 维度控制
- **Streaming** — 只是换了底层 client，SSE 逻辑不变

## 实现顺序

1. **数据层**：Provider 表 + ProviderManager + 加密存储
2. **核心层**：BedrockService 客户端缓存池
3. **请求链路**：auth 中间件 → api handler 透传 provider_id
4. **Admin 后端**：Provider CRUD API + API Key 扩展
5. **Admin 前端**：Provider 管理页 + API Key 表单扩展
6. **测试 + 连通性验证**
