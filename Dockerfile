# ============================================================
# Dockerfile — Mitta AI 智能助理后端服务
# 构建：docker build -t mitta-ai .
# 运行：docker run -p 8000:8000 --env-file .env mitta-ai
# ============================================================
# 降级为 Python 3.12 slim 镜像，AI 生态兼容性最好，体积仍保持轻量
FROM python:3.12-slim

# 替换 apt 源为阿里云镜像，全链路走国内加速
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources && \
    sed -i 's/security.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src

# 安装系统依赖：
# - gcc/libpq-dev：psycopg 编译需要
# - curl：健康检查
# - nodejs/npm：MCP stdio 服务器需要 npx（filesystem/git/sequential-thinking 等）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    gnupg \
    # 使用阿里云镜像源安装 Node.js 20 LTS，替代境外 nodesource 脚本
    && curl -fsSL https://mirrors.aliyun.com/nodesource/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv 极速包管理器，依赖解析+下载速度是 pip 的 5~10 倍
RUN pip install uv -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com

# 先复制依赖文件，利用 Docker 缓存层
COPY requirements.txt .

# 使用 uv 安装 Python 依赖，全链路阿里云镜像加速
RUN uv pip install --no-cache-dir -r requirements.txt \
    --index-url https://mirrors.aliyun.com/pypi/simple/ \
    --trusted-host mirrors.aliyun.com \
    --system

# 复制项目代码
COPY . .

# 暴露端口
EXPOSE 8000

# 健康检查（使用 curl，slim 镜像已安装）
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# 启动命令
# PYTHONPATH=/app/src 已在 ENV 中设置，uvicorn main:app 可直接找到 src/ 下的模块
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
