"""
用户路由：个人资料 / 密码 / system-prompt / 主题 / 记忆 / 会话列表 / 文件 / 用户级 MCP

对应原 main.py 中的 /api/users/{user_id}/* 接口。
"""

from typing import Optional

from fastapi import APIRouter, Depends
from loguru import logger

from routers.deps import require_self_or_admin
from schemas.request_schemas.user_schema import (
    ProfileUpdateRequest, PasswordUpdateRequest,
    ThemeUpdateRequest, McpConfigUpdateRequest,
)
from service.cache_service import cache_service
from service.chat_service import chat_service
from service.file_upload_service import file_upload_service
from service.login_service import login_service
from service.user_profile_service import user_profile_service
from utils.jwt_utils import TokenData
from utils.response_util import Response

router = APIRouter(tags=["用户"])


# ============================================================
# 个人资料
# ============================================================

@router.get("/api/users/{user_id}/profile")
def get_user_profile(user_id: str, current_user: TokenData = Depends(require_self_or_admin)):
    """获取用户个人信息（username, avatar, theme, system_prompt）。"""
    profile = user_profile_service.get_profile(user_id)
    if profile:
        return {
            "ok": True,
            "data": {
                "user_id": profile.get("user_id"),
                "username": profile.get("username"),
                "avatar": profile.get("avatar"),
                "theme": profile.get("theme", "default"),
                "system_prompt": profile.get("system_prompt") or "",
            }
        }
    return {"ok": True, "data": None}


@router.put("/api/users/{user_id}/profile")
def update_user_profile(user_id: str, request_body: ProfileUpdateRequest,
                        current_user: TokenData = Depends(require_self_or_admin)):
    """更新用户个人信息（username, avatar, system_prompt）。"""
    logger.info(f"[profile-debug] 收到请求体: system_prompt={request_body.system_prompt!r} username={request_body.username!r}")
    # 字数限制：最多 500 字
    if request_body.system_prompt is not None and len(request_body.system_prompt) > 500:
        return Response.failed("自定义设定不能超过 500 字")
    success = user_profile_service.update_basic_info(
        user_id=user_id,
        username=request_body.username,
        avatar=request_body.avatar,
        system_prompt=request_body.system_prompt,
    )
    if not success:
        return Response.failed("没有需要更新的字段")

    # system_prompt 变更时失效该用户所有会话的检索缓存（不影响 JWT/登录态等其他缓存）
    if request_body.system_prompt is not None:
        try:
            sessions = chat_service.get_user_sessions(user_id)
            thread_ids = [s["thread_id"] for s in sessions if s.get("thread_id")]
            if thread_ids:
                cache_service.clear_user_thread_caches(user_id, thread_ids)
        except Exception as e:
            # 缓存清除失败不影响主流程：已保存成功，缓存会在 TTL 后自然过期
            logger.warning(f"更新 system_prompt 后清除检索缓存失败 user_id={user_id}: {e}")

    return Response.success("个人信息更新成功")


@router.put("/api/users/{user_id}/password")
def update_user_password(user_id: str, request_body: PasswordUpdateRequest,
                         current_user: TokenData = Depends(require_self_or_admin)):
    """修改用户密码（需验证原密码）。"""
    # 先验证原密码
    user_info = login_service.login(user_id, request_body.old_password)
    if not isinstance(user_info, dict):
        return Response.failed("原密码错误")
    # 修改密码（复用 recover 逻辑）
    result = login_service.recover(user_id, request_body.new_password)
    if result == 1:
        return Response.success("密码修改成功")
    return Response.failed(result or "密码修改失败")


# ============================================================
# 主题
# ============================================================

@router.get("/api/users/{user_id}/theme")
def get_user_theme(user_id: str, current_user: TokenData = Depends(require_self_or_admin)):
    """获取用户主题配置。"""
    theme = user_profile_service.get_theme(user_id)
    return {"ok": True, "data": {"theme": theme}}


@router.put("/api/users/{user_id}/theme")
def update_user_theme(user_id: str, request_body: ThemeUpdateRequest,
                      current_user: TokenData = Depends(require_self_or_admin)):
    """更新用户主题配置。"""
    allowed_themes = ["default", "dark", "ocean", "sunset", "forest", "lavender"]
    if request_body.theme not in allowed_themes:
        return Response.failed(f"不支持的主题，可选：{', '.join(allowed_themes)}")
    user_profile_service.update_theme(user_id, request_body.theme)
    return Response.success("主题更新成功")


# ============================================================
# 记忆 / 会话列表 / 文件
# ============================================================

@router.get("/api/users/{user_id}/memory")
def get_memory(user_id: str, current_user: TokenData = Depends(require_self_or_admin)):
    """获取用户长期记忆（LangGraph Store）。"""
    memory = chat_service.get_memory(user_id)
    return memory


@router.get("/api/users/{user_id}/sessions")
def get_sessions_by_user_id(user_id: str, current_user: TokenData = Depends(require_self_or_admin)):
    """获取用户的所有会话列表。"""
    sessions = chat_service.get_user_sessions(user_id)
    return sessions


@router.get("/api/users/{user_id}/files")
def list_user_files(user_id: str, thread_id: Optional[str] = None,
                     current_user: TokenData = Depends(require_self_or_admin)):
    """列出用户上传的文件。"""
    files = file_upload_service.list_files(user_id, thread_id)
    return {"ok": True, "data": files}


# ============================================================
# 用户级 MCP 配置（数据库存储，保留兼容）
# ============================================================

@router.get("/api/users/{user_id}/mcp")
def get_user_mcp_config(user_id: str, current_user: TokenData = Depends(require_self_or_admin)):
    """获取用户 MCP 服务器配置（数据库存储）。"""
    config = user_profile_service.get_mcp_config(user_id)
    return {"ok": True, "data": {"mcp_servers": config}}


@router.put("/api/users/{user_id}/mcp")
def update_user_mcp_config(user_id: str, request_body: McpConfigUpdateRequest,
                            current_user: TokenData = Depends(require_self_or_admin)):
    """更新用户 MCP 服务器配置（JSON 数组，每个元素是一个 dict）。"""
    try:
        user_profile_service.update_mcp_config(user_id, request_body.mcp_servers)
        return Response.success("MCP 配置更新成功")
    except (ValueError, TypeError) as e:
        return Response.failed(f"MCP 配置格式错误：{e}")
