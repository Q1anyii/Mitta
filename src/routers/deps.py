"""
公共依赖注入（FastAPI Depends）

存放跨路由复用的依赖函数，避免在 main.py 和各 router 中重复定义。
"""

from fastapi import Depends, HTTPException

from utils.jwt_utils import get_current_user, TokenData


def require_self_or_admin(user_id: str, current_user: TokenData = Depends(get_current_user)):
    """资源归属校验：只允许本人访问自己的资源，管理员角色放行。

    FastAPI 会自动把路径参数 user_id 注入本依赖（必须定义在使用它的路由之前）。
    """
    if str(current_user.user_id) != user_id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="无权访问该用户资源")
    return current_user


def require_admin(current_user: TokenData = Depends(get_current_user)):
    """管理员权限校验：仅 role=admin 的用户可执行知识库管理类写操作。

    比只信 JWT 里的 role 更严谨：token 签发后角色变更不会立即生效，
    这里从数据库读最新 role 再校验，避免旧 token 携带过期角色越权。
    """
    from service.login_service import login_service

    if current_user.role == "admin":
        return current_user
    # token 中 role 非 admin 时，再从库确认一次（可能是角色刚升级、旧 token 未刷新）
    user_info = login_service.get_user_by_id(str(current_user.user_id))
    if user_info and user_info.get("role") == "admin":
        return current_user
    raise HTTPException(status_code=403, detail="仅管理员可执行此操作")
