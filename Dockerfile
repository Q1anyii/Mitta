# ============================================================
# Dockerfile — Mitta AI 智能助理后端服务
# 构建：docker build -t mitta-ai .
# 运行：docker run -p 8000:8000 --env-file .env mitta-ai
# 国内ECS构建加速：docker build --build-arg APT_MIRROR=mirrors.aliyun.com --build-arg PIP_INDEX=https://mirrors.aliyun.com/pypi/simple .
# ============================================================

# Python 3.12 slim：AI 生态兼容性最好（部分包暂无 3.13 wheel）
FROM python:3.12-slim

# 构建参数：CI境外环境默认官方源，国内ECS构建可传入阿里云源
ARG APT_MIRROR=deb.debian.org
ARG PIP_INDEX=https://pypi.org/simple

# 动态替换 apt 源（Debian 12 bookworm 使用 .sources 格式）
RUN sed -i "s/deb.debian.org/${APT_MIRROR}/g" /etc/apt/sources.list.d/debian.sources && \
    sed -i "s/security.debian.org/${APT_MIRROR}/g" /etc/apt/sources.list.d/debian.sources

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src

# 系统依赖：
# - gcc/libpq-dev：psycopg[binary] 编译兜底
# - curl：健康检查
# - gnupg + nodejs：MCP stdio 服务器需要 npx（filesystem/git/sequential-thinking 等）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# 先复制依赖文件，利用 Docker 缓存层（代码变更不触发重装）
COPY requirements.txt .

# 安装 Python 依赖（直接用 pip，不引入 uv 减少复杂度）
RUN pip install --no-cache-dir -r requirements.txt -i ${PIP_INDEX}

# 复制项目代码
COPY . .

EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# 启动命令（PYTHONPATH=/app/src 已在 ENV 中设置，uvicorn main:app 可直接找到）
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
