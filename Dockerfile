# ============================================================
# Dockerfile — Mitta AI 智能助理后端服务
# ============================================================
# 降级为 Python 3.12 slim，AI 生态兼容性最好
FROM python:3.12-slim

# 构建参数：CI境外环境默认官方源，本地ECS构建可传入阿里云源
ARG APT_MIRROR=deb.debian.org
ARG PIP_INDEX=https://pypi.org/simple
ARG PIP_EXTRA_INDEX=https://download.pytorch.org/whl/cpu

# 动态替换 apt 源
RUN sed -i "s/deb.debian.org/${APT_MIRROR}/g" /etc/apt/sources.list.d/debian.sources && \
    sed -i "s/security.debian.org/${APT_MIRROR}/g" /etc/apt/sources.list.d/debian.sources

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src

# 安装系统依赖（含Node.js用于MCP）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# 安装uv极速包管理器
RUN pip install uv -i ${PIP_INDEX}

COPY requirements.txt .

# uv安装依赖：主源+PyTorch额外源，动态适配构建环境
RUN pip install --no-cache-dir -r requirements.txt \
    -i ${PIP_INDEX} \
    --extra-index-url ${PIP_EXTRA_INDEX}


COPY . .

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
