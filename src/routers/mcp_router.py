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
