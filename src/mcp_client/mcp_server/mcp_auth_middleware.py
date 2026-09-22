# ============================================================
# /mcp 端点鉴权中间件（P0-5）
# 问题：mcp.http_app() 裸奔，工具 user_id 由调用方自填 → 越权调他人账号
# 修法：
#   1) ASGI 层校验 Authorization: Bearer <JWT>，无/非法 token → 401
#   2) 解出的 user_id 存入 contextvar；工具内部不再信任入参 user_id，
#      强制用 contextvar（调用方改不了）
# 说明：stdio 本地子进程不经过本中间件，contextvar 为空时工具 fallback 默认值
# ============================================================

import json
from contextvars import ContextVar

from utils.jwt_utils import get_username_from_token

# web 模式下由中间件注入的真实 user_id；stdio 本地模式为空串
mcp_user_id_var: ContextVar[str] = ContextVar("mcp_user_id", default="")


class McpAuthMiddleware:
    """ASGI 包装层：校验 /mcp 请求的 JWT，注入 user_id 到上下文。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # 只拦 HTTP；websocket/lifespan 等放行
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        headers = {
            k.decode().lower(): v.decode()
            for k, v in scope.get("headers", [])
        }
        auth = headers.get("authorization", "")
        token = auth[7:] if auth.lower().startswith("bearer ") else ""

        uid = get_username_from_token(token) if token else None
        if not uid:
            body = json.dumps({
                "error": "unauthorized",
                "detail": "missing or invalid Authorization: Bearer <token>",
            }).encode()
            await send({
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    [b"content-type", b"application/json"],
                    [b"content-length", str(len(body)).encode()],
                ],
            })
            await send({"type": "http.response.body", "body": body})
            return

        ctx = mcp_user_id_var.set(uid)
        try:
            await self.app(scope, receive, send)
        finally:
            mcp_user_id_var.reset(ctx)
