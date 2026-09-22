# ============================================================
# 请求链路追踪 + 日志轮转
# 1) request_id：每个请求生成短 id，注入 loguru 每行日志，排查时 grep 同一请求
# 2) 日志轮转：文件 10MB 自动切、保留 7 天、旧的压 zip，避免 ./logs 卷撑爆磁盘
# ============================================================

import sys
import uuid
from contextvars import ContextVar

from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

# 当前请求的短 id（线程/协程隔离），无请求时为 "-"
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


def _log_patcher(record: dict) -> None:
    """给每条日志 record 注入 request_id，供 format 里 {extra[request_id]} 使用。"""
    record["extra"]["request_id"] = request_id_var.get() or "-"


def setup_logging() -> None:
    """配置 loguru：控制台 + 轮转文件，均带 request_id。

    在 app 启动（lifespan 前）调用一次。
    """
    logger.configure(patcher=_log_patcher)
    fmt = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</> "
        "| <level>{level: <7}</> "
        "| <c>[req={extra[request_id]}]</c> "
        "| {name}:{function}:{line} "
        "| {message}"
    )
    # 移除默认 handler，统一用带 request_id 的格式
    logger.remove()
    logger.add(sys.stderr, format=fmt, level="INFO")
    # 文件轮转：单文件 10MB 切，保留 7 天，旧文件 gzip 压缩
    # 非 root 容器 / 挂载卷无写权限时静默跳过文件 sink，只打控制台，不阻断启动
    try:
        logger.add(
            "logs/mitta.log",
            format=fmt,
            level="DEBUG",
            rotation="10 MB",
            retention="7 days",
            compression="zip",
            encoding="utf-8",
            enqueue=True,  # 多进程/异步下安全写
        )
    except (PermissionError, OSError) as e:
        logger.warning(f"文件日志 sink 不可用（{type(e).__name__}: {e}），仅保留控制台输出")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """给每个请求生成 request_id 并放入 contextvar，响应头带回 X-Request-ID。"""

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:8]
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            request_id_var.reset(token)
