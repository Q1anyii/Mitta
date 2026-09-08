"""用户档案工具：用户名解析和长期记忆档案管理。

拆分自原 main_graph.py 的 _get_username 和 _ensure_username_profile 闭包函数。
依赖：cache_service（Redis 全局单例），通过延迟导入避免循环依赖。
"""
import re

from langchain_core.runnables import RunnableConfig
from langgraph.store.base import BaseStore
from loguru import logger


def _get_username(config: RunnableConfig) -> str | None:
    """取当前用户 username。

    优先用鉴权解析结果（chat 路由已从 JWT 解析，放在 config.configurable.user_info），
    invoke 等无 user_info 的路径回退到 Redis 登录态 token 解析。

    Args:
        config: LangGraph 配置

    Returns:
        用户名字符串，解析失败返回 None
    """
    user_info = config["configurable"].get("user_info")
    if user_info is not None:
        username = getattr(user_info, "username", None)
        if username:
            return username
    user_id = config["configurable"].get("user_id", "default")
    from constant.cache_constant import USER_TOKEN_KEY
    from service.cache_service import cache_service
    from utils.jwt_utils import get_username_from_token
    try:
        token = cache_service.redis.get(USER_TOKEN_KEY.format(user_id=user_id))
    except Exception as e:
        logger.warning(f"读取登录态 token 失败，跳过用户名解析：{e}")
        return None
    return get_username_from_token(token) if token else None


def _ensure_username_profile(store: BaseStore, user_id: str, username: str | None) -> str:
    """把 username 并入长期记忆档案并立即落库（回答前完成），返回合并后档案。

    username 属长期事实，先写入再组装提示词，AI 首轮就能识别用户；
    档案按 (rag_chat, user_id) 命名空间隔离，各用户独立存储。

    Args:
        store: 长期记忆存储
        user_id: 用户 ID
        username: 用户名（可能为 None）

    Returns:
        合并后的档案字符串
    """
    namespace = ("rag_chat", user_id)
    item = store.get(namespace, "user_profile")
    profile = item.value["profile"] if item else "（暂无档案）"
    base_profile = f"用户名：{username}" if username else ""
    if base_profile:
        # 用户名变更时替换旧行（正则匹配"用户名：xxx"），避免新旧名字并存导致 AI 混淆
        profile_new = re.sub(r"^用户名：[^\n]*$", base_profile, profile, flags=re.MULTILINE)
        if profile_new != profile:
            profile = profile_new
            store.put(namespace, "user_profile", {"profile": profile})
            logger.info(f"用户名档案已更新（user_id={user_id}）")
        elif base_profile not in profile:
            profile = f"{profile}\n{base_profile}" if profile != "（暂无档案）" else base_profile
            store.put(namespace, "user_profile", {"profile": profile})
            logger.info(f"用户名基础档案已落库（user_id={user_id}）")
    return profile
