"""
Mitta JWT 性能与续签评估
==========================
评估指标：
  - 登录态校验平均耗时（JWT 验签 + Redis 登录态校验）
  - token 过期自动续签成功率
  - 并发校验性能

用法：
    conda activate langchain1.2
    cd src
    python -m ragas_test.eval_jwt                  # 默认测试
    python -m ragas_test.eval_jwt --iterations 100 # 校验迭代次数
    python -m ragas_test.eval_jwt --concurrent 20  # 并发数
"""
import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Dict, List

from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.jwt_utils import (
    create_access_token,
    create_refresh_token,
    get_current_user,
    SECRET_KEY,
    ALGORITHM,
    ACCESS_TOKEN_EXPIRE_MINUTES,
)
from constant.cache_constant import USER_TOKEN_KEY, USER_REFRESH_TOKEN_KEY
from service.cache_service import cache_service

import jwt


def setup_test_user(user_id: str = "jwt_test_user", username: str = "testuser") -> tuple:
    """创建测试用户的登录态（access + refresh token 存入 Redis）。"""
    data = {"sub": f"{user_id}:{username}", "role": "学员"}
    access = create_access_token(data)
    refresh = create_refresh_token(data)

    r = cache_service.redis
    r.setex(USER_TOKEN_KEY.format(user_id=user_id),
            ACCESS_TOKEN_EXPIRE_MINUTES * 60, access)
    r.setex(USER_REFRESH_TOKEN_KEY.format(user_id=user_id),
            30 * 86400, refresh)

    return access, refresh


def test_verify_performance(iterations: int = 100) -> Dict:
    """测试登录态校验平均耗时。"""
    logger.info(f"【测试 1】登录态校验性能（{iterations} 次）")
    user_id = "jwt_perf_user"
    access, _ = setup_test_user(user_id)

    latencies = []
    for i in range(iterations):
        t0 = time.perf_counter()
        # 模拟 get_current_user 的核心逻辑：JWT 解码 + Redis 校验
        try:
            payload = jwt.decode(access, SECRET_KEY, algorithms=[ALGORITHM])
            r = cache_service.redis
            stored = r.get(USER_TOKEN_KEY.format(user_id=user_id))
            if stored:
                if isinstance(stored, bytes):
                    stored = stored.decode("utf-8")
                _ = stored == access
        except Exception as e:
            logger.warning(f"校验失败: {e}")
        elapsed = (time.perf_counter() - t0) * 1000
        latencies.append(elapsed)

    avg = sum(latencies) / len(latencies)
    p50 = sorted(latencies)[len(latencies) // 2]
    p95 = sorted(latencies)[int(len(latencies) * 0.95) - 1]
    p99 = sorted(latencies)[int(len(latencies) * 0.99) - 1]

    result = {
        "ragas_test": "verify_performance",
        "iterations": iterations,
        "avg_ms": avg,
        "p50_ms": p50,
        "p95_ms": p95,
        "p99_ms": p99,
        "min_ms": min(latencies),
        "max_ms": max(latencies),
    }
    logger.info(f"  平均: {avg:.2f}ms, P50: {p50:.2f}ms, P95: {p95:.2f}ms, P99: {p99:.2f}ms")
    logger.info(f"  Min: {min(latencies):.2f}ms, Max: {max(latencies):.2f}ms")

    # 清理
    cache_service.redis.delete(USER_TOKEN_KEY.format(user_id=user_id))
    cache_service.redis.delete(USER_REFRESH_TOKEN_KEY.format(user_id=user_id))
    return result


def test_token_renewal() -> Dict:
    """测试 token 过期自动续签成功率。

    每次循环使用独立的过期 access token，模拟用户每次请求时 token 都已过期的场景。
    续签成功后 Redis 中的 token 会被更新，但下一次循环重新写入过期 token，确保每次都触发续签。
    """
    logger.info("【测试 2】token 过期自动续签")
    user_id = "jwt_renewal_user"
    username = "renewal_user"

    data = {"sub": f"{user_id}:{username}", "role": "学员"}
    valid_refresh = create_refresh_token(data)

    r = cache_service.redis
    # 先存入有效 refresh token（整个测试期间保持有效）
    r.setex(USER_REFRESH_TOKEN_KEY.format(user_id=user_id), 30 * 86400, valid_refresh)

    success = 0
    total = 20
    new_tokens = set()

    for i in range(total):
        try:
            # 每次循环都创建一个新的过期 access token 并写入 Redis
            # 模拟：用户每次请求时 access token 都已过期，但 refresh token 仍有效
            expired_access = create_access_token(data, expires_delta=timedelta(seconds=-10))
            r.setex(USER_TOKEN_KEY.format(user_id=user_id), 60, expired_access)

            # 第一步：不过期解析（定位用户）
            payload = jwt.decode(expired_access, SECRET_KEY, algorithms=[ALGORITHM],
                                 options={"verify_exp": False})
            sub = payload.get("sub")
            uid = str(sub).split(":")[0]
            uname = str(sub).split(":")[-1]
            role = payload.get("role")

            # 第二步：Redis 校验
            stored = r.get(USER_TOKEN_KEY.format(user_id=uid))
            if stored:
                if isinstance(stored, bytes):
                    stored = stored.decode("utf-8")

            # 第三步：检测过期并续签（模拟 get_current_user 核心逻辑）
            try:
                jwt.decode(expired_access, SECRET_KEY, algorithms=[ALGORITHM])
                # 未过期，不需要续签（本测试不应走到这里）
                pass
            except jwt.ExpiredSignatureError:
                # 需要续签
                refresh = r.get(USER_REFRESH_TOKEN_KEY.format(user_id=uid))
                if refresh:
                    if isinstance(refresh, bytes):
                        refresh = refresh.decode("utf-8")
                    jwt.decode(refresh, SECRET_KEY, algorithms=[ALGORITHM])
                    new_access = create_access_token(
                        {"sub": f"{uid}:{uname}", "role": role}
                    )
                    new_refresh = create_refresh_token({"sub": f"{uid}:{uname}"})
                    r.setex(USER_TOKEN_KEY.format(user_id=uid),
                            ACCESS_TOKEN_EXPIRE_MINUTES * 60, new_access)
                    r.setex(USER_REFRESH_TOKEN_KEY.format(user_id=uid),
                            30 * 86400, new_refresh)
                    new_tokens.add(new_access)
                    success += 1
        except Exception as e:
            logger.warning(f"续签失败 ({i+1}/{total}): {e}")

    success_rate = success / total if total else 0
    result = {
        "ragas_test": "token_renewal",
        "total": total,
        "success": success,
        "success_rate": success_rate,
        "unique_new_tokens": len(new_tokens),
    }
    logger.info(f"  续签成功: {success}/{total} ({success_rate*100:.1f}%)")
    logger.info(f"  生成新 token 数: {len(new_tokens)}（轮换防重放）")

    # 清理
    cache_service.redis.delete(USER_TOKEN_KEY.format(user_id=user_id))
    cache_service.redis.delete(USER_REFRESH_TOKEN_KEY.format(user_id=user_id))
    return result


def test_concurrent_verify(concurrent: int = 20) -> Dict:
    """并发登录态校验测试。"""
    logger.info(f"【测试 3】并发校验（{concurrent} 并发）")
    user_id = "jwt_concurrent_user"
    access, _ = setup_test_user(user_id)

    async def verify_once():
        t0 = time.perf_counter()
        try:
            payload = jwt.decode(access, SECRET_KEY, algorithms=[ALGORITHM])
            r = cache_service.redis
            stored = r.get(USER_TOKEN_KEY.format(user_id=user_id))
            _ = stored is not None
        except Exception:
            pass
        return (time.perf_counter() - t0) * 1000

    async def run():
        sem = asyncio.Semaphore(concurrent)
        results = []

        async def bounded():
            async with sem:
                return await verify_once()

        tasks = [bounded() for _ in range(concurrent * 5)]
        results = await asyncio.gather(*tasks)
        return results

    latencies = asyncio.run(run())

    avg = sum(latencies) / len(latencies)
    p95 = sorted(latencies)[int(len(latencies) * 0.95) - 1]

    result = {
        "ragas_test": "concurrent_verify",
        "concurrent": concurrent,
        "total_requests": len(latencies),
        "avg_ms": avg,
        "p95_ms": p95,
    }
    logger.info(f"  总请求: {len(latencies)}, 平均: {avg:.2f}ms, P95: {p95:.2f}ms")

    # 清理
    cache_service.redis.delete(USER_TOKEN_KEY.format(user_id=user_id))
    cache_service.redis.delete(USER_REFRESH_TOKEN_KEY.format(user_id=user_id))
    return result


def main():
    parser = argparse.ArgumentParser(description="JWT 性能与续签评估")
    parser.add_argument("--iterations", type=int, default=100, help="校验迭代次数（默认 100）")
    parser.add_argument("--concurrent", type=int, default=20, help="并发数（默认 20）")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("Mitta JWT 性能与续签评估")
    logger.info(f"配置: iterations={args.iterations}, concurrent={args.concurrent}")
    logger.info(f"Access token 有效期: {ACCESS_TOKEN_EXPIRE_MINUTES} 分钟")
    logger.info("=" * 60)

    # 初始化 Redis
    cache_service.open()
    logger.success("Redis 连接就绪")

    results = []

    # 测试 1: 校验性能
    results.append(test_verify_performance(args.iterations))

    # 测试 2: 续签
    results.append(test_token_renewal())

    # 测试 3: 并发
    results.append(test_concurrent_verify(args.concurrent))

    # 汇总
    logger.info(f"\n{'='*60}")
    logger.info("【JWT 评估汇总】")
    for r in results:
        if r["ragas_test"] == "verify_performance":
            logger.info(f"  校验延迟: 平均 {r['avg_ms']:.2f}ms, P95 {r['p95_ms']:.2f}ms")
        elif r["ragas_test"] == "token_renewal":
            logger.info(f"  续签成功率: {r['success_rate']*100:.1f}% ({r['success']}/{r['total']})")
        elif r["ragas_test"] == "concurrent_verify":
            logger.info(f"  并发({r['concurrent']}): 平均 {r['avg_ms']:.2f}ms, P95 {r['p95_ms']:.2f}ms")
    logger.info(f"{'='*60}")

    # 保存报告
    output_path = Path(__file__).parent / "jwt_eval_report.json"
    output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"评估报告已保存: {output_path}")

    cache_service.close()


if __name__ == "__main__":
    main()
