"""
认证路由：登录 / 注册 / 密码找回 / 登出

对应原 main.py 中的 /api/login、/api/register、/api/recover、/api/logout 接口。
"""

from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from loguru import logger

from config import get_env_int
from constant.cache_constant import USER_TOKEN_KEY, USER_REFRESH_TOKEN_KEY
from schemas.request_schemas.login_schema import LoginRequest, RegisterRequest, RecoverRequest, RecoverCodeRequest
from service.cache_service import cache_service
from service.login_service import login_service
from middleware.auth_rate_limit import check_auth_allowed, record_auth_failure, reset_auth_success, client_ip
import secrets
from service.email_service import send_recover_code
from utils.response_util import Response
from utils.jwt_utils import (
    create_access_token,
    create_refresh_token,
    REFRESH_TOKEN_EXPIRE_DAYS,
    get_current_user,
    TokenData,
)

router = APIRouter(tags=["认证"])

# access token 过期时间（分钟），默认 15
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = get_env_int("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", 15)


@router.post("/api/login")
def login(request_body: LoginRequest, request: Request):
    """用户登录：校验 PostgreSQL 用户表，返回 JWT token + 用户信息。"""
    ip = client_ip(request)
    user_id = request_body.userId
    # 认证端点独立限流（防爆破）：IP + userId 双维度，失败指数退避
    allowed, retry = check_auth_allowed(ip, user_id)
    if not allowed:
        return Response.failed(f"尝试过于频繁，请 {retry} 秒后再试")
    password = request_body.password
    user_info = login_service.login(user_id, password)
    # login 返回 dict 才是成功：密码错误/用户不存在时返回的是字符串提示
    if not isinstance(user_info, dict):
        record_auth_failure(ip, user_id)
        return Response.failed(user_info or "用户 ID 或密码错误")
    reset_auth_success(ip, user_id)
    token = create_access_token(
        data={
            "sub": str(user_info["user_id"] + ":" + user_info["username"]),
            "role": user_info.get("role", "学员"),  # 管理员角色用于资源越权放行
        },
        expires_delta=timedelta(minutes=int(JWT_ACCESS_TOKEN_EXPIRE_MINUTES)),
    )
    # 隐式 refresh token：只存 Redis 不下发前端，access 过期时由后端（jwt_utils）自动续签
    refresh_token = create_refresh_token(
        data={"sub": str(user_info["user_id"] + ":" + user_info["username"])}
    )
    r = cache_service.redis
    # setex 第二参数单位是「秒」：access 配置为分钟需 ×60
    r.setex(USER_TOKEN_KEY.format(user_id=user_id), int(JWT_ACCESS_TOKEN_EXPIRE_MINUTES) * 60, token)
    r.setex(
        USER_REFRESH_TOKEN_KEY.format(user_id=user_id),
        REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        refresh_token,
    )
    return {"ok": True, "token": token, "user_info": user_info}


@router.post("/api/register")
def register(request_body: RegisterRequest):
    """用户注册：创建新用户。"""
    # 显式传递参数，替代原 *request_body 隐式展开
    flag, response = login_service.register(
        username=request_body.userName,
        user_id=request_body.userId,
        password=request_body.password,
        email=request_body.email
    )
    if flag:
        return Response.success(response)
    else:
        return Response.failed(response)


@router.post("/api/recover/code")
def recover_code(request_body: RecoverCodeRequest, request: Request):
    """发送密码找回验证码：按邮箱反查 user_id，查到才发码。

    生成一次性验证码存 Redis（TTL 5 分钟），经邮件 service 发给用户。
    邮箱不存在也返回成功（防账号枚举）。"""
    ip = client_ip(request)
    email = request_body.email
    # 限流维度用 email（前端未传 userId，按 email 做桶键）
    allowed, retry = check_auth_allowed(ip, email)
    if not allowed:
        return Response.failed(f"尝试过于频繁，请 {retry} 秒后再试")
    r = cache_service.redis
    # 按邮箱反查 user_id；查不到不发码，但对外仍返回成功（防枚举）
    user_id = login_service.find_user_id_by_email(email)
    if user_id:
        code = f"{secrets.randbelow(1000000):06d}"
        # 一次性验证码：TTL 300s，重置成功即焚
        r.setex(f"recover:code:{user_id}", 300, code)
        send_recover_code(email, code)
    return Response.success("验证码已发送，5 分钟内有效")


@router.post("/api/recover")
def recover(request_body: RecoverRequest, request: Request):
    """密码重置：按邮箱反查 user_id，校验一次性验证码（用后即焚）后才改密码。"""
    ip = client_ip(request)
    email = request_body.email
    # 重置凭据端点同样限流（防爆破/撞库）
    allowed, retry = check_auth_allowed(ip, email)
    if not allowed:
        return Response.failed(f"尝试过于频繁，请 {retry} 秒后再试")

    # 按邮箱反查 user_id；查不到直接拒绝（对外不区分邮箱是否存在）
    user_id = login_service.find_user_id_by_email(email)
    if not user_id:
        record_auth_failure(ip, email)
        return Response.failed("验证码错误或已过期，请重新获取")

    # 一次性验证码校验：GETDEL 取出即焚，防止重放
    r = cache_service.redis
    stored = r.getdel(f"recover:code:{user_id}")
    if not stored or stored.decode() != request_body.code.strip():
        record_auth_failure(ip, email)
        return Response.failed("验证码错误或已过期，请重新获取")

    new_password = request_body.newPassword
    response = login_service.recover(user_id, new_password)
    if not response:
        return Response.failed("注册失败")
    elif response == 1:
        reset_auth_success(ip, email)
        return Response.success()
    else:
        return Response.failed(response)


@router.post("/api/logout")
def logout(current_user: TokenData = Depends(get_current_user)):
    """用户登出：删除 Redis 中的 access + refresh token，实现即时失效。

    无状态 JWT 本身无法作废，通过 Redis 白名单机制实现：
    登录时 token 存入 Redis，每次请求校验 Redis 中是否存在；
    登出时删除 Redis 中的两个 key，下次请求校验失败即 401。
    """
    user_id = current_user.user_id
    r = cache_service.redis
    # 同时删除 access 和 refresh，防止 access 过期后用 refresh 续签
    r.delete(USER_TOKEN_KEY.format(user_id=user_id))
    r.delete(USER_REFRESH_TOKEN_KEY.format(user_id=user_id))
    logger.info(f"用户 [{user_id}] 登出，Redis 登录态已清除")
    return Response.success()
