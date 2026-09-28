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
#
# 动态替换 apt 源（Debian 12 bookworm 使用 .sources 格式）
RUN sed -i "s/deb.debian.org/${APT_MIRROR}/g" /etc/apt/sources.list.d/debian.sources && \
    sed -i "s/security.debian.org/${APT_MIRROR}/g" /etc/apt/sources.list.d/debian.sources

WORKDIR /app

# A11: 不以 root 运行容器。用户提前创建，供后续 COPY --chown 与运行时 USER 使用。
# 同时预建 /opt/uv-tools 并交给 appuser：uv 工具将以其身份安装（见下），
# 运行时对工具目录可写（uvx 需在目录内建临时文件），且无需末尾 chown -R——
# 原 `chown -R /app /opt/uv-tools` 因 overlay copy-up 会把整棵树复制进新层，
# 镜像虚增约 414MB；本写法工具层直接归 appuser，chown 层彻底消除。
RUN useradd -m appuser && mkdir -p /opt/uv-tools && chown appuser:appuser /opt/uv-tools

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src \
    # 运行时 uvx 启动 Python 版 MCP server 默认连 pypi.org，国内 ECS 慢，配阿里云源
    # （构建期 PIP_INDEX 只管 pip，管不到运行期 uvx）
    UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple \
    UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple \
    UV_TOOL_DIR=/opt/uv-tools \
    UV_TOOL_BIN_DIR=/opt/uv-tools/bin

# 系统依赖：
# - gcc/libpq-dev：psycopg[binary] 编译兜底
# - curl：健康检查
# - gnupg + nodejs：MCP stdio 服务器需要 npx（filesystem/sequential-thinking 等）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && npm config set registry https://registry.npmmirror.com \
    && npm config set fund false \
    && npm config set audit false \
    && npm config set update-notifier false \
    && npm install -g \
        @modelcontextprotocol/server-filesystem \
        @modelcontextprotocol/server-sequential-thinking \
        @modelcontextprotocol/server-memory \
        @upstash/context7-mcp \
        @bytebase/dbhub \
    && npm ls -g --depth=0 \
    && echo "NPM_MCP_PACKAGES_INSTALLED_OK" 

# 先复制依赖文件，利用 Docker 缓存层（代码变更不触发重装）
COPY requirements.txt .

# 安装 Python 依赖（直接用 pip，不引入 uv 减少复杂度）
RUN pip install --no-cache-dir -r requirements.txt -i ${PIP_INDEX}

# 安装 uv（提供 uvx 命令）：Python 生态 MCP 服务器（如 mcp-server-fetch）
# 通过 `uvx 包名` 启动，缺 uvx 会报 No such file: 'uvx'
RUN pip install --no-cache-dir uv -i ${PIP_INDEX}

# 预热 uvx 缓存：构建期就把 Python 版 MCP server 及其全部依赖下载进 uv 缓存，
# 运行时 uvx 直接命中缓存、零联网，避免容器冷启动首次下载慢导致 MCP 连接超时
# （曾出现：冷启动下载 html5lib/onnxruntime/chromadb 等懒加载依赖超过连接超时
#   -> 拿到 0 工具 -> 空图被缓存；--help 不触发懒加载依赖下载，故改用 tool install）
# 注意：索引经环境变量传入（不用 --default-index 命令行参数，兼容旧版 uv）；
# CI 在 GitHub Actions 境外构建用官方源（境外快）；运行时 ENV 已配阿里云源（国内快）
# 2026-09-28: 以 appuser 身份安装——工具树直接归 appuser，运行时 uvx 可写
# （实测：uvx 启动需在工具目录创建临时文件，root 只读会 Permission denied）。
USER appuser
RUN for pkg in mcp-server-fetch mcp-server-sqlite mcp-server-time markitdown-mcp chroma-mcp basic-memory; do \
        echo "tool install: $pkg" && timeout 300 env UV_DEFAULT_INDEX=https://pypi.org/simple UV_INDEX_URL=https://pypi.org/simple uv tool install "$pkg" >/dev/null 2>&1 || \
        echo "WARN: tool install $pkg failed, will download at runtime"; \
    done && echo "UVX_MCP_PACKAGES_PREWARMED_DONE"
USER root

# 复制项目代码
# 2026-09-09 强制重建：此前连续 6 个提交一次性推送，CI 仅对比最后一次提交
# （196b078 未含 Dockerfile/requirements）→ build_required=false 跳过镜像构建，
# 服务器拉到旧镜像，GET /profile 不含 system_prompt 字段导致前端回显空值。
COPY --chown=appuser:appuser . .

# A11: 不以 root 运行容器（useradd 已在前置层创建；/app 经 COPY --chown 归属 appuser；
#      /opt/uv-tools 由 appuser 安装归其所有，uvx 运行可写临时文件）
USER appuser

EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# 启动命令（PYTHONPATH=/app/src 已在 ENV 中设置，uvicorn main:app 可直接找到）
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
