# agentcore-search-mcp

将 **Amazon Bedrock AgentCore Gateway WebSearch** 工具暴露给任意 MCP 客户端（Claude Code、Codex、Cursor 或你自己的 agent）的 MCP server。

[English documentation](README.md) · [Agent 可直接执行的安装步骤](INSTALL_FOR_AGENTS.md)

## What is this / 这是什么

AgentCore Gateway 本身就使用 MCP 协议（JSON-RPC over HTTPS）——但它要求 **AWS SigV4 请求签名**（service `bedrock-agentcore`），而 MCP 客户端自身无法完成签名。本 server 是一个本地桥：对客户端提供普通的 stdio MCP，对上游的每个调用做 SigV4 签名。

```
┌────────────────┐   stdio (MCP)   ┌──────────────────────┐   SigV4 签名 JSON-RPC     ┌───────────────────────┐
│  MCP 客户端    │ ──────────────► │  agentcore-search-mcp │ ────────────────────────► │  AgentCore Gateway    │
│  (Claude Code, │ ◄────────────── │  (本 server)          │ ◄──────────────────────── │  WebSearch target     │
│  Codex, ...)   │                 └──────────────────────┘                            └───────────────────────┘
└────────────────┘
```

暴露的工具：`web_search(query, max_results=5)` → `{"results": [{"url", "title", "content", "published_date"}]}`。

## Prerequisites / 前置条件

- **带 WebSearch target 的 AgentCore Gateway。** 还没有 gateway？用自带的部署脚本一键创建（见下）。也可以在 AWS 控制台手动创建：Amazon Bedrock AgentCore → Gateways → 创建 gateway 并挂载 Web Search connector target，复制 gateway 的 MCP endpoint URL。注意：Web Search connector 有区域限制（编写时仅 us-east-1）。
- **AWS 凭证**，任意标准凭证链（环境变量、`AWS_PROFILE` 或实例角色），且有调用该 gateway 的权限（如 `bedrock-agentcore:InvokeGateway` 或你账号中对应的 gateway 资源策略）。
- **Python ≥ 3.10** 和 [uv](https://docs.astral.sh/uv/)（推荐；pip 也可以）。

### 用自带脚本部署 gateway

```bash
cd agentcore-search-mcp
uv sync                          # 只需一次——脚本使用 venv 里的 botocore
bash scripts/deploy_gateway.sh
```

脚本是**幂等的**（重复执行会按名称复用已有的角色/gateway/target），会在你的账号中创建三样东西：

1. IAM 服务角色 `agentcore-search-mcp-service-role`（信任 `bedrock-agentcore.amazonaws.com`；内联策略允许 `InvokeGateway` + `InvokeWebSearch`）
2. AgentCore Gateway `agentcore-search-mcp`（MCP 协议，`AWS_IAM` 鉴权）
3. Web Search connector target `web-search-tool`（通过 SigV4 控制面 REST API 创建——connector target 的配置目前无法用 aws CLI 表达）

成功后会打印 `AGENTCORE_GATEWAY_URL`、可直接执行的 `claude mcp add` 命令和冒烟测试命令。名称可通过 `GATEWAY_NAME` / `TARGET_NAME` / `ROLE_NAME` 环境变量覆盖。部署需要 IAM 角色创建和 `bedrock-agentcore-control`（gateway 增删改查）权限——比运行时只需 invoke 的权限更宽。删除需手动操作（控制台，或 `aws bedrock-agentcore-control delete-gateway-target` / `delete-gateway`，再删 IAM 角色）。

## Installation / 安装

包已发布到 PyPI（[agentcore-search-mcp](https://pypi.org/project/agentcore-search-mcp/)），无需 checkout 代码：

```bash
# (a) 最简：直接从 PyPI 运行
uvx agentcore-search-mcp --version

# (b) pip
pip install agentcore-search-mcp
```

或从源码目录（`agentcore-search-mcp/`）使用：

```bash
# (c) uv 项目安装
uv sync && uv run agentcore-search-mcp --version

# (d) uvx 免安装运行本地 checkout
uvx --from /abs/path/to/agentcore-search-mcp agentcore-search-mcp --version
```

以上命令预期输出均为 `agentcore-search-mcp 0.1.0`。使用 PyPI 形式时，下方各客户端配置中的 `--directory` 形式可换成 `"command": "uvx", "args": ["agentcore-search-mcp"]`。发布流程见 [RELEASING.md](RELEASING.md)。

## Configuration / 配置

全部通过环境变量配置：

| 变量 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `AGENTCORE_GATEWAY_URL` | 是 | — | Gateway MCP endpoint：`https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp` |
| `AGENTCORE_GATEWAY_REGION` | 否 | `us-east-1` | SigV4 签名使用的区域（必须与 gateway 所在区域一致） |
| `AGENTCORE_SEARCH_TIMEOUT` | 否 | `30` | 上游 HTTP 超时（秒） |
| `AWS_PROFILE` / `AWS_ACCESS_KEY_ID` 等 | 是（任一凭证链） | — | 用于签名的标准 AWS 凭证链 |

## Client setup / 客户端配置

以下示例都使用 `uvx` 运行 PyPI 上的发布包——无需 checkout 代码或填写路径。如需从源码运行，把 `uvx agentcore-search-mcp` 替换为 `uv --directory /abs/path/to/agentcore-search-mcp run agentcore-search-mcp`（先在该目录 `uv sync`）。

### Claude Code

一行命令（project 作用域）：

```bash
claude mcp add agentcore-search --scope project \
  --env AGENTCORE_GATEWAY_URL=https://<gateway-id>.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp \
  --env AGENTCORE_GATEWAY_REGION=us-east-1 \
  -- uvx agentcore-search-mcp
```

或写入 `.mcp.json`（参见 [`.mcp.json.example`](.mcp.json.example)）：

```json
{
  "mcpServers": {
    "agentcore-search": {
      "command": "uvx",
      "args": ["agentcore-search-mcp"],
      "env": {
        "AGENTCORE_GATEWAY_URL": "https://<gateway-id>.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp",
        "AGENTCORE_GATEWAY_REGION": "us-east-1"
      }
    }
  }
}
```

### Codex

```bash
codex mcp add agentcore-search \
  --env AGENTCORE_GATEWAY_URL=https://<gateway-id>.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp \
  -- uvx agentcore-search-mcp
```

或在 `~/.codex/config.toml` 中：

```toml
[mcp_servers.agentcore-search]
command = "uvx"
args = ["agentcore-search-mcp"]
env = { AGENTCORE_GATEWAY_URL = "https://<gateway-id>.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp" }
```

### Cursor

在项目的 `.cursor/mcp.json` 中使用与上面 Claude Code `.mcp.json` 相同的 JSON 结构。

### Generic MCP client / 通用客户端

以 stdio 子进程方式启动 `uvx agentcore-search-mcp` 并设置环境变量；随后 `initialize` → `tools/list` → `tools/call web_search`。

## Verify / 验证

1. `claude mcp list`（或所用客户端的等价命令）显示 `agentcore-search` 已连接。
2. 在客户端中提问：*“用 web_search 工具查一下 AWS Bedrock 的最新公告。”*
3. 不经客户端直接冒烟：

```bash
AGENTCORE_GATEWAY_URL=https://<gateway-id>.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp \
  uv run python scripts/live_smoke.py
```

## Transports / 传输方式

- **stdio**（默认）——所有桌面 MCP 客户端使用的方式。
- **streamable-http** —— `agentcore-search-mcp --transport streamable-http --port 8900`，用于共享/远程部署。HTTP 监听端口**自身没有鉴权**；请绑定 localhost 或在前面加自己的鉴权层。

## Troubleshooting / 故障排查

| 现象 | 原因 / 处理 |
|---|---|
| gateway 返回 `HTTP 403` | 凭证缺失/过期、对 gateway 资源无权限、或 `AGENTCORE_GATEWAY_REGION` 与 gateway 区域不一致（SigV4 scope 不匹配） |
| `does not expose a 'WebSearch' tool` | gateway URL 不对，或该 gateway 未挂载 Web Search connector target——错误信息会列出实际可用的工具 |
| 结果数少于 `max_results` | 符合预期：无来源 URL（`url: null`）的结果会被过滤 |
| 超时 | 调大 `AGENTCORE_SEARCH_TIMEOUT`；检查到 `*.gateway.bedrock-agentcore.<region>.amazonaws.com` 的网络出口 |
| `AGENTCORE_GATEWAY_URL is not set` | 在客户端 MCP 配置的 `env` 块中设置该变量，仅在 shell 中 export 不会传给子进程 |

## Limitations / 限制

- 查询会被截断到 **200 个字符**（gateway 限制）。
- 每次调用最多 **25 条**结果。
- AgentCore Web Search connector 不支持 `allowed_domains` / `blocked_domains` / 用户位置过滤。
