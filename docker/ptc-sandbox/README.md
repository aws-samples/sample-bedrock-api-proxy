# PTC Sandbox Custom Docker Images

本目录包含用于 Programmatic Tool Calling (PTC) 功能的自定义 Docker 镜像构建文件。

## 镜像说明

| 镜像标签 | 描述 | 大小 | 包含的包 |
|---------|------|------|---------|
| `ptc-sandbox:datascience` | 完整数据科学环境 | ~800MB | numpy, pandas, scipy, matplotlib, scikit-learn, statsmodels |
| `ptc-sandbox:minimal` | 最小化环境 | ~200MB | numpy, pandas, requests, httpx |
| `python:3.11-slim` | 默认基础镜像 | ~50MB | 仅 Python 标准库 |

## 快速开始

### 1. 构建镜像

```bash
cd docker/ptc-sandbox

# 构建数据科学版本（推荐）
./build.sh

# 或构建最小版本
./build.sh minimal

# 构建所有版本
./build.sh all
```

### 2. 配置使用

在 `.env` 文件中设置：

```bash
# 使用数据科学版本
PTC_SANDBOX_IMAGE=ptc-sandbox:datascience

# 或使用最小版本
PTC_SANDBOX_IMAGE=ptc-sandbox:minimal
```

或通过环境变量：

```bash
export PTC_SANDBOX_IMAGE=ptc-sandbox:datascience
```

### 3. 验证配置

启动服务后，检查 PTC 健康状态：

```bash
curl http://localhost:8000/health/ptc
```

## 包含的 Python 包

### datascience 版本

```
# 核心数据分析
numpy>=1.24.0
pandas>=2.0.0

# 科学计算
scipy>=1.11.0

# 数据可视化
matplotlib>=3.7.0

# 统计分析
statsmodels>=0.14.0

# 机器学习
scikit-learn>=1.3.0

# HTTP 请求
requests>=2.31.0
httpx>=0.24.0

# 数据处理
orjson>=3.9.0
pydantic>=2.0.0
python-dateutil>=2.8.0
pytz>=2023.3
```

### minimal 版本

```
numpy>=1.24.0
pandas>=2.0.0
requests>=2.31.0
httpx>=0.24.0
orjson>=3.9.0
python-dateutil>=2.8.0
```

## 自定义镜像

如果需要添加其他包，可以修改 Dockerfile：

```dockerfile
# 在 RUN pip install 命令中添加包
RUN pip install --no-cache-dir \
    numpy>=1.24.0 \
    pandas>=2.0.0 \
    # 添加你需要的包
    your-package>=1.0.0
```

### 添加系统依赖

某些 Python 包需要系统级依赖，例如：

```dockerfile
# 安装系统依赖（在 pip install 之前）
RUN apt-get update && apt-get install -y --no-install-recommends \
    # 例如：libpq-dev 用于 psycopg2
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*
```

## 推送到私有仓库

如果需要将镜像推送到私有仓库：

```bash
# 构建并推送
./build.sh --registry your-registry.com --push

# 然后在 .env 中配置
PTC_SANDBOX_IMAGE=your-registry.com/ptc-sandbox:datascience
```

## 安全注意事项

1. **网络隔离**：默认情况下，PTC sandbox 禁用网络访问（`PTC_NETWORK_DISABLED=True`）
2. **非 root 用户**：自定义镜像使用非 root 用户运行
3. **只读文件系统**：sandbox 挂载为只读模式
4. **资源限制**：受 `PTC_MEMORY_LIMIT` 和 CPU 配额限制

## 故障排除

### 镜像构建失败

```bash
# 清理 Docker 缓存后重试
docker builder prune -f
./build.sh
```

### 包导入错误

如果在 PTC 执行中遇到包导入错误：

1. 确认镜像已正确构建：`docker images | grep ptc-sandbox`
2. 确认环境变量已设置：检查 `.env` 或 `echo $PTC_SANDBOX_IMAGE`
3. 重启服务使配置生效
4. 检查 PTC 健康状态：`curl http://localhost:8000/health/ptc`

### 镜像太大

如果镜像大小是问题：

1. 使用 `minimal` 版本
2. 使用多阶段构建减小镜像大小
3. 仅安装必需的包

## 版本兼容性

| Python 版本 | 支持状态 |
|------------|---------|
| 3.11 | ✅ 推荐 |
| 3.12 | ✅ 支持 |
| 3.10 | ⚠️ 可能工作 |
| < 3.10 | ❌ 不支持 |

---

## English Version

See the [main README](../../README_EN.md) for English documentation.

### Quick Start (English)

```bash
# Build data science image
cd docker/ptc-sandbox
./build.sh

# Configure in .env
echo "PTC_SANDBOX_IMAGE=ptc-sandbox:datascience" >> ../../.env

# Verify
curl http://localhost:8000/health/ptc
```
