"""
用户 MCP 配置路由：PostgreSQL 存储的用户级 MCP 服务器配置读写

对应原 /api/mcp/config 接口。
MCP 配置存储在 PostgreSQL user_mcp_servers 表中，按 user_id 隔离。
所有写入操作经过安全校验（命令白名单、路径隔离）和 Windows→Linux 路径自动转换。
"""

from fastapi import APIRouter, Depends

from config import load_mcp_server_configs
import service.mcp_config_service as mcp_config_module
from utils.jwt_utils import get_current_user, TokenData
from utils.response_util import Response

router = APIRouter(tags=["MCP 配置"])


@router.get("/api/mcp/config")
def get_user_mcp_config(current_user: TokenData = Depends(get_current_user)):
    """获取当前用户的 MCP 配置（从 PostgreSQL 读取）。

    Returns:
        { ok, data: { mcp_servers, storage: "postgresql" } }
    """
    user_id = current_user.user_id
    mcp_servers = load_mcp_server_configs(user_id=user_id)
    return {
        "ok": True,
        "data": {
            "mcp_servers": mcp_servers,
            "storage": "postgresql",
            "user_id": user_id,
        }
    }


@router.put("/api/mcp/config")
def update_user_mcp_config(
    request_body: dict,
    current_user: TokenData = Depends(get_current_user)
):
    """更新当前用户的 MCP 配置（保存到 PostgreSQL）。

    安全校验：
    - 命令白名单（npx/uvx/node/python/python3/pipx）
    - 包名安全白名单
    - Windows 路径自动转换为 Linux 容器内路径
    - filesystem 限制在 /app/user_files/{user_id}/ 下
    - 禁止敏感环境变量（PATH/HOME 等）
    - sse 类型禁止内网地址

    Request body:
        - mcp_servers: MCP 配置列表（必填）

    Returns:
        { ok, detail, data: { count } }
    """
    user_id = current_user.user_id
    mcp_servers = request_body.get("mcp_servers")

    if mcp_servers is None:
        return Response.failed("缺少 mcp_servers 字段")
    if not isinstance(mcp_servers, list):
        return Response.failed("mcp_servers 必须是 JSON 数组")

    if mcp_config_module.mcp_config_service is None:
        return Response.failed("MCP 配置服务未初始化，请稍后重试")

    success, message = mcp_config_module.mcp_config_service.save_user_servers(user_id, mcp_servers)
    if not success:
        return Response.failed(message)

    return {
        "ok": True,
        "detail": message + "，下次对话时自动生效",
        "data": {"count": len(mcp_servers), "user_id": user_id}
    }


@router.delete("/api/mcp/config/{server_name}")
def delete_user_mcp_server(
    server_name: str,
    current_user: TokenData = Depends(get_current_user)
):
    """删除当前用户的单个 MCP 服务器配置。

    Args:
        server_name: 要删除的 MCP 服务器名称

    Returns:
        { ok, detail }
    """
    user_id = current_user.user_id
    if mcp_config_module.mcp_config_service is None:
        return Response.failed("MCP 配置服务未初始化")

    deleted = mcp_config_module.mcp_config_service.delete_user_server(user_id, server_name)
    if deleted:
        return {"ok": True, "detail": f"已删除 MCP 服务器: {server_name}"}
    else:
        return Response.failed(f"未找到 MCP 服务器: {server_name}")


@router.post("/api/mcp/reload")
def reload_user_mcp(current_user: TokenData = Depends(get_current_user)):
    """主动重载当前用户的 MCP 配置：清除图缓存并关闭旧连接，下次对话立即重建。

    【为什么需要这个接口】
    ChatService._get_user_graph 已通过配置 hash 检测实现自动热重载——用户保存
    配置后，下一次发消息时 hash 变化会自动触发图重建。但存在两个体验问题：
    1. 旧 MCP 连接（子进程/stdio）不会主动关闭，占用资源；
    2. 用户保存后无法感知"已生效"，前端只能提示"下次对话生效"。
    本接口主动清除该用户的缓存条目并关闭旧连接，让配置立即生效且资源干净。

    Returns:
        { ok, detail, cleared: bool }
    """
    import asyncio
    from service.chat_service import chat_service

    user_id = current_user.user_id
    cleared = False
    try:
        cache = getattr(chat_service, "_user_graph_cache", None)
        if cache and user_id in cache:
            cached = cache.pop(user_id)
            cleared = True
            # cached = (config_hash, graph, connections)，关闭旧 MCP 连接释放子进程
            connections = cached[2] if len(cached) > 2 else []
            if connections and chat_service._tool_loop:
                try:
                    asyncio.run_coroutine_threadsafe(
                        chat_service._close_connections(connections),
                        chat_service._tool_loop,
                    ).result(timeout=5)
                except Exception:
                    pass  # 关闭失败不影响重载，下次重建时旧连接会被 GC
    except Exception as e:
        # 缓存清除失败不影响功能：hash 检测仍会在下次对话时自动重建
        return {"ok": True, "detail": f"重载完成（缓存清除异常，不影响自动重建）: {e}", "cleared": False}

    return {
        "ok": True,
        "detail": "MCP 配置已重载，新工具将在下次对话中生效",
        "cleared": cleared,
        "user_id": user_id,
    }
