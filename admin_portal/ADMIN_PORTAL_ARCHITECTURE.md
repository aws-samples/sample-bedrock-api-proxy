# Admin Portal 项目架构文档

> 本文档由代码分析自动生成，描述了 Admin Portal 的完整架构和实现细节。
>
> **注意**: 由于环境中未配置 LSP 服务器（Python/TypeScript），代码分析基于手动文件读取完成。

## 1. 项目概述

Admin Portal 是 Anthropic API Proxy 服务的独立管理 Web 应用程序，提供以下功能：
- API Key 管理（创建、更新、停用、删除）
- 模型定价配置
- 使用量监控
- 仪表板统计

## 2. 项目结构

```
admin_portal/
├── backend/                 # FastAPI 后端 (Python)
│   ├── main.py             # 应用入口点
│   ├── api/                # API 路由处理器
│   │   ├── __init__.py
│   │   ├── auth.py         # 认证端点
│   │   ├── dashboard.py    # 仪表板统计
│   │   ├── api_keys.py     # API Key 管理
│   │   └── pricing.py      # 模型定价管理
│   ├── middleware/         # 中间件
│   │   ├── auth.py         # 基础认证中间件
│   │   └── cognito_auth.py # Cognito JWT 验证
│   ├── schemas/            # Pydantic 数据模型
│   │   ├── __init__.py
│   │   ├── api_key.py      # API Key 模式
│   │   ├── auth.py         # 认证模式
│   │   ├── dashboard.py    # 仪表板模式
│   │   └── pricing.py      # 定价模式
│   ├── utils/
│   │   └── jwt_validator.py # Cognito JWT 验证器
│   └── .env                # 环境配置
├── frontend/               # React 前端 (TypeScript)
│   ├── src/
│   │   ├── main.tsx        # 应用入口
│   │   ├── App.tsx         # 根组件与路由
│   │   ├── components/     # UI 组件
│   │   │   └── Layout/     # 布局组件
│   │   ├── pages/          # 页面组件
│   │   │   ├── Login.tsx
│   │   │   ├── Dashboard.tsx
│   │   │   ├── ApiKeys.tsx
│   │   │   └── Pricing.tsx
│   │   ├── hooks/          # React Hooks
│   │   │   ├── useAuth.ts
│   │   │   ├── useApiKeys.ts
│   │   │   ├── useDashboard.ts
│   │   │   └── usePricing.ts
│   │   ├── services/       # API 服务
│   │   │   └── api.ts      # API 客户端
│   │   ├── types/          # TypeScript 类型定义
│   │   │   ├── api-key.ts
│   │   │   ├── dashboard.ts
│   │   │   └── index.ts
│   │   ├── config/
│   │   │   └── amplify.ts  # AWS Amplify 配置
│   │   └── i18n/           # 国际化
│   ├── package.json
│   ├── vite.config.ts
│   └── tailwind.config.js
└── scripts/                # 设置脚本
    └── setup_cognito.py
```

## 3. 技术栈

### 3.1 后端技术栈

| 技术 | 用途 |
|------|------|
| **FastAPI** | REST API 框架 |
| **AWS Cognito** | 身份认证 (JWT 验证) |
| **DynamoDB** | 数据持久化 |
| **Pydantic** | 请求/响应验证 |
| **Python 3.12+** | 运行时环境 |

### 3.2 前端技术栈

| 技术 | 用途 |
|------|------|
| **React 18** | UI 框架 |
| **TypeScript** | 类型安全 |
| **Vite** | 构建工具 |
| **TailwindCSS** | 样式框架 |
| **React Router** | 路由管理 |
| **TanStack Query** | 数据获取与缓存 |
| **AWS Amplify** | Cognito 集成 |
| **i18next** | 国际化 (中/英) |

## 4. 后端架构详解

### 4.1 应用入口 (`main.py`)

```python
# 主要配置
ADMIN_PORT = 8005
API_PREFIX = "/api"

# FastAPI 应用创建
app = FastAPI(
    title="Anthropic API Proxy - Admin Portal",
    description="Administration interface for managing API keys and model pricing",
    version="1.0.0",
)

# 中间件配置
app.add_middleware(CORSMiddleware, ...)     # CORS 支持
app.add_middleware(CognitoAuthMiddleware)   # JWT 认证

# 路由注册
app.include_router(auth.router, prefix="/api/auth")
app.include_router(dashboard.router, prefix="/api/dashboard")
app.include_router(api_keys.router, prefix="/api/keys")
app.include_router(pricing.router, prefix="/api/pricing")
```

### 4.2 API 端点列表

| 端点 | 方法 | 描述 | 认证 |
|------|------|------|------|
| `/health` | GET | 健康检查 | 否 |
| `/api/auth/config` | GET | 获取 Cognito 配置 | 否 |
| `/api/auth/verify` | GET | 验证 JWT Token | 是 |
| `/api/auth/me` | GET | 获取当前用户信息 | 是 |
| `/api/dashboard/stats` | GET | 获取仪表板统计 | 是 |
| `/api/keys` | GET | 列出 API Keys | 是 |
| `/api/keys` | POST | 创建 API Key | 是 |
| `/api/keys/{key}` | GET | 获取单个 API Key | 是 |
| `/api/keys/{key}` | PUT | 更新 API Key | 是 |
| `/api/keys/{key}` | DELETE | 停用 API Key | 是 |
| `/api/keys/{key}/reactivate` | POST | 重新激活 API Key | 是 |
| `/api/keys/{key}/permanent` | DELETE | 永久删除 API Key | 是 |
| `/api/keys/{key}/usage` | GET | 获取使用统计 | 是 |
| `/api/pricing` | GET | 列出模型定价 | 是 |
| `/api/pricing` | POST | 创建模型定价 | 是 |
| `/api/pricing/providers` | GET | 列出提供商 | 是 |
| `/api/pricing/{model_id}` | GET | 获取模型定价 | 是 |
| `/api/pricing/{model_id}` | PUT | 更新模型定价 | 是 |
| `/api/pricing/{model_id}` | DELETE | 删除模型定价 | 是 |

### 4.3 数据模型 (Pydantic Schemas)

#### API Key 模型

```python
class ApiKeyCreate(BaseModel):
    user_id: str                           # 用户标识
    name: str                              # Key 名称
    owner_name: Optional[str] = None       # 所有者显示名称
    role: Optional[str] = "Full Access"    # 角色
    monthly_budget: Optional[float] = 0    # 月预算 (USD)
    rpm_limit: Optional[int] = 1000        # 每分钟请求限制
    tpm_limit: Optional[int] = 100000      # 每分钟 Token 限制
    rate_limit: Optional[int] = None       # 自定义速率限制
    service_tier: Optional[str] = None     # Bedrock 服务层级

class ApiKeyResponse(BaseModel):
    api_key: str
    user_id: str
    name: str
    created_at: int                        # Unix 时间戳
    is_active: bool
    rate_limit: int
    service_tier: str
    owner_name: Optional[str] = None
    role: Optional[str] = None
    monthly_budget: Optional[float] = 0
    budget_used: Optional[float] = 0
    rpm_limit: Optional[int] = 1000
    tpm_limit: Optional[int] = 100000
```

#### Dashboard 模型

```python
class DashboardStats(BaseModel):
    total_api_keys: int          # 总 API Keys 数量
    active_api_keys: int         # 活跃 Keys 数量
    revoked_api_keys: int        # 已撤销 Keys 数量
    total_budget: float          # 总预算
    total_budget_used: float     # 已使用预算
    total_models: int            # 总模型数量
    active_models: int           # 活跃模型数量
    system_status: str           # 系统状态
    new_keys_this_week: int      # 本周新增 Keys
```

### 4.4 认证中间件 (`cognito_auth.py`)

```python
class CognitoAuthMiddleware(BaseHTTPMiddleware):
    """Cognito JWT 验证中间件"""

    # 免认证路径
    SKIP_AUTH_PATHS = {
        "/health",
        "/docs",
        "/openapi.json",
        "/api/auth/config",
    }

    async def dispatch(self, request, call_next):
        # 1. 检查是否需要跳过认证
        if request.url.path in SKIP_AUTH_PATHS:
            return await call_next(request)

        # 2. 开发模式：未配置 Cognito 时允许访问
        if not self.is_configured:
            request.state.user = {"username": "dev-user", ...}
            return await call_next(request)

        # 3. 提取并验证 JWT Token
        token = self._extract_token(request)  # 从 Authorization: Bearer xxx
        claims = self._validator.validate_token(token)
        request.state.user = self._validator.get_user_info(claims)

        return await call_next(request)
```

### 4.5 DynamoDB 集成

后端复用主项目的 DynamoDB 管理器：

```python
from app.db.dynamodb import DynamoDBClient, APIKeyManager, UsageTracker, ModelPricingManager

# 使用示例
db_client = DynamoDBClient()
api_key_manager = APIKeyManager(db_client)
pricing_manager = ModelPricingManager(db_client)
```

**DynamoDB 表：**
- `anthropic-proxy-api-keys` - API Key 存储
- `anthropic-proxy-usage` - 使用量追踪
- `anthropic-proxy-model-pricing` - 模型定价配置

## 5. 前端架构详解

### 5.1 应用路由 (`App.tsx`)

```tsx
function App() {
  return (
    <Routes>
      {/* 公开路由 */}
      <Route path="/login" element={<PublicRoute><Login /></PublicRoute>} />

      {/* 受保护路由 */}
      <Route element={<ProtectedRoute><MainLayout /></ProtectedRoute>}>
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/api-keys" element={<ApiKeys />} />
        <Route path="/pricing" element={<Pricing />} />
      </Route>

      {/* 默认重定向 */}
      <Route path="/" element={<Navigate to="/dashboard" />} />
    </Routes>
  );
}
```

### 5.2 API 服务层 (`api.ts`)

```typescript
// 认证 Token 获取
async function getAuthToken(): Promise<string | null> {
  if (!isAmplifyConfigured()) return null;  // 开发模式
  const session = await fetchAuthSession();
  return session.tokens?.idToken?.toString() || null;
}

// API 请求封装
async function apiFetch<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const headers = { 'Content-Type': 'application/json' };
  const token = await getAuthToken();
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const response = await fetch(`/api${endpoint}`, { ...options, headers });
  return response.json();
}

// API 模块
export const apiKeysApi = {
  list: (params?) => apiFetch('/keys', ...),
  get: (apiKey) => apiFetch(`/keys/${apiKey}`),
  create: (data) => apiFetch('/keys', { method: 'POST', body: JSON.stringify(data) }),
  update: (apiKey, data) => apiFetch(`/keys/${apiKey}`, { method: 'PUT', ... }),
  deactivate: (apiKey) => apiFetch(`/keys/${apiKey}`, { method: 'DELETE' }),
  reactivate: (apiKey) => apiFetch(`/keys/${apiKey}/reactivate`, { method: 'POST' }),
  deletePermanently: (apiKey) => apiFetch(`/keys/${apiKey}/permanent`, { method: 'DELETE' }),
  getUsage: (apiKey) => apiFetch(`/keys/${apiKey}/usage`),
};

export const pricingApi = { ... };
export const dashboardApi = { ... };
```

### 5.3 React Hooks (`useApiKeys.ts`)

使用 TanStack Query 进行数据管理：

```typescript
// 查询 Hooks
export function useApiKeys(params?) {
  return useQuery({
    queryKey: ['apiKeys', params],
    queryFn: () => apiKeysApi.list(params),
  });
}

// 变更 Hooks
export function useCreateApiKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: ApiKeyCreate) => apiKeysApi.create(data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['apiKeys'] }),
  });
}

export function useUpdateApiKey() { ... }
export function useDeactivateApiKey() { ... }
export function useReactivateApiKey() { ... }
export function useDeleteApiKey() { ... }
```

### 5.4 TypeScript 类型定义

```typescript
// API Key 类型
interface ApiKey {
  api_key: string;
  user_id: string;
  name: string;
  owner_name?: string;
  role?: string;
  monthly_budget?: number;
  budget_used?: number;
  rpm_limit?: number;
  tpm_limit?: number;
  is_active: boolean;
  created_at: number;
}

// Dashboard 统计类型
interface DashboardStats {
  total_api_keys: number;
  active_api_keys: number;
  revoked_api_keys: number;
  total_budget: number;
  total_budget_used: number;
  total_models: number;
  active_models: number;
  system_status: string;
  new_keys_this_week: number;
}
```

## 6. 页面功能说明

### 6.1 Login 页面 (`/login`)
- Cognito 用户名/密码认证
- NEW_PASSWORD_REQUIRED 挑战处理
- 语言切换器 (中/英)

### 6.2 Dashboard 页面 (`/dashboard`)
- 总预算使用进度条
- 活跃/已撤销 API Keys 统计
- 模型数量统计
- 系统状态显示
- 快捷操作链接

### 6.3 API Keys 页面 (`/api-keys`)
- 可搜索/可过滤表格
- 创建/编辑/停用/重新激活/删除操作
- 预算使用可视化
- RPM/TPM 限制显示

### 6.4 Pricing 页面 (`/pricing`)
- 模型定价表格
- 输入/输出/缓存定价 (每百万 Token)
- 提供商过滤
- 状态管理 (active/deprecated/disabled)

## 7. 认证流程

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Frontend  │────>│   Cognito   │────>│   Backend   │
│   (React)   │     │ (User Pool) │     │  (FastAPI)  │
└─────────────┘     └─────────────┘     └─────────────┘
      │                    │                    │
      │ 1. 用户登录        │                    │
      │ ─────────────────> │                    │
      │                    │                    │
      │ 2. 返回 ID Token   │                    │
      │ <───────────────── │                    │
      │                    │                    │
      │ 3. Bearer Token 请求                    │
      │ ───────────────────────────────────────>│
      │                    │                    │
      │                    │  4. 验证 JWT (JWKS) │
      │                    │ <──────────────────│
      │                    │                    │
      │ 5. 返回数据                             │
      │ <───────────────────────────────────────│
```

## 8. 设计系统

### 8.1 颜色主题 (深色主题)

| 变量 | 颜色值 | 用途 |
|------|--------|------|
| 背景 | `#0b0f19` | 页面背景 |
| Surface | `#111827` | 卡片背景 |
| Border | `#1f2937` | 边框 |
| Primary | `#2B6CEE` | 主色调 |
| Text | White/Gray | 文字 |

### 8.2 字体

- 主字体：**Inter**

## 9. 国际化支持

支持英语和中文：
- 语言存储在 localStorage
- 登录页和 Header 可切换
- 使用 react-i18next

## 10. 安全考虑

1. **认证要求**: 除 `/health` 和 `/api/auth/config` 外所有端点需要有效 JWT
2. **CORS**: 开发环境允许所有源（生产环境需限制）
3. **Token 验证**: 完整的 JWKS JWT 验证（签名、过期、audience、issuer）
4. **无客户端密钥**: Cognito App Client 创建时无 secret（SPA 必需）

## 11. 开发设置

### 后端启动

```bash
cd admin_portal/backend

# 配置环境变量
cat > .env << EOF
COGNITO_USER_POOL_ID=your-user-pool-id
COGNITO_CLIENT_ID=your-client-id
COGNITO_REGION=us-west-2
EOF

# 启动服务 (端口 8005)
cd ../..
python -m uvicorn admin_portal.backend.main:app --port 8005 --reload
```

### 前端启动

```bash
cd admin_portal/frontend

# 安装依赖
npm install

# 开发模式
npm run dev

# 生产构建
npm run build
```

## 12. 与主项目的集成

Admin Portal 复用主项目的 DynamoDB 管理器和表：

```python
# 路径: app/db/dynamodb.py
from app.db.dynamodb import DynamoDBClient, APIKeyManager, UsageTracker, ModelPricingManager
```

这确保了数据一致性和代码复用。

---

**文档生成时间**: 2026-01-02
**分析方法**: 手动代码读取和分析（无 LSP 服务器可用）
