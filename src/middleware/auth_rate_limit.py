# ============================================================
# 认证端点独立限流（防凭据爆破）
# 作用：login / recover 这类「验证凭据 / 重置凭据」端点，是爆破的首要目标，
#       必须独立于普通聊天限流单独保护。
# 策略：
#   - 双维度：client IP + 表单里的 userId（任一维度超限即拒）
#   - 失败计数：每失败一次 incr，按次数指数退避设锁 TTL
#   - 成功清零：登录/重置成功立即删除该 IP+user 的失败计数
#   - Redis 不可用降级放行（不把正常用户锁外面，安全网退化为内存层）
# 键：auth:fail:ip:{ip} / auth:fail:user:{uid}，值=连续失败次数，TTL=剩余锁时长
# ============================================================

from fastapi import Request
from loguru import logger

# 连续失败超过此次数后，每次失败都按指数退避锁定（前几次只计数）
AUTH_MAX_FAIL = 5


def _lock_seconds(fail_count: int) -> int:
    """按连续失败次数指数退避：1→10s, 2→30s, 3→60s, 4→120s, ≥5→300s（封顶）。"""
    if fail_count <= 1:
        return 10
    if fail_count == 2:
        return 30
    if fail_count == 3:
        return 60
    if fail_count == 4:
        return 120
    return 300


def client_ip(request: Request) -> str:
    """取客户端真实 IP：走 nginx 反代时优先 X-Forwarded-For 首段。"""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _keys(ip: str, user_id: str) -> list[str]:
    return [f"auth:fail:ip:{ip}", f"auth:fail:user:{user_id}"]


def check_auth_allowed(ip: str, user_id: str) -> tuple[bool, int]:
    """检查该 IP+user 当前是否被限流。

    Returns:
        (allowed, retry_after_seconds)。allowed=False 时 retry_after 为剩余锁时长。
    """
    try:
        from service.cache_service import cache_service
        r = cache_service.redis
        for key in _keys(ip, user_id):
            cnt = r.get(key)
            if cnt is None:
                continue
            cnt = int(cnt)
            if cnt >= AUTH_MAX_FAIL:
                ttl = r.ttl(key)
                if ttl and ttl > 0:
                    return False, int(ttl)
        return True, 0
    except Exception as e:
        logger.warning(f"认证限流检查失败，本次放行（降级）：{e}")
        return True, 0


def record_auth_failure(ip: str, user_id: str) -> None:
    """记录一次认证失败，并按当前失败次数刷新锁 TTL（指数退避）。"""
    try:
        from service.cache_service import cache_service
        r = cache_service.redis
        for key in _keys(ip, user_id):
            cnt = r.incr(key)
            r.expire(key, _lock_seconds(cnt))
    except Exception as e:
        logger.warning(f"记录认证失败计数失败：{e}")


def reset_auth_success(ip: str, user_id: str) -> None:
    """认证成功后清除失败计数，恢复正常。"""
    try:
        from service.cache_service import cache_service
        r = cache_service.redis
        r.delete(*_keys(ip, user_id))
    except Exception as e:
        logger.warning(f"重置认证成功计数失败：{e}")
