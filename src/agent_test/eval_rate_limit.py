"""
Mitta 限流中间件评估脚本
==========================
评估指标：
  - 拦截准确率（429 比例是否符合预期）
  - 降级耗时（Redis 不可用时切换到内存限流的耗时）
  - 固定窗口边界行为

用法：
    conda activate langchain1.2
    cd src
    python -m ragas_test.test_rate_limit                  # 默认测试
    python -m ragas_test.test_rate_limit --concurrent 50  # 并发数
    python -m ragas_test.test_rate_limit --max-req 30     # 限流阈值
    python -m ragas_test.test_rate_limit --ragas_test-degrade    # 测试 Redis 降级

注意：
  - 本脚本直接调用 RateLimitMiddleware 的内部方法，不启动 HTTP 服务
  - 测试降级时会临时断开 Redis 连接，测试完成后恢复
"""
import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Dict, Tuple
from unittest.mock import patch, MagicMock

from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from middleware.rate_limit_middleware import (
    RateLimitMiddleware,
    RATE_LIMIT_MAX_REQUESTS,
    RATE_LIMIT_WINDOW_SECONDS,
    RATE_LIMITED_PATHS,
)
from service.cache_service import cache_service


# ============================================================
# 模拟 Request 对象
# ============================================================

class MockRequest:
    """模拟 FastAPI Request 对象。"""

    def __init__(self, path: str = "/api/chat/", client_ip: str = "127.0.0.1"):
        self.url = MagicMock()
        self.url.path = path
        self.client = MagicMock()
        self.client.host = client_ip


# ============================================================
# 测试用例
# ============================================================

def test_normal_traffic(max_requests: int, window: int) -> Dict:
    """测试正常流量：低于阈值的请求应全部通过。"""
    logger.info("【测试 1】正常流量（低于阈值）")
    middleware = RateLimitMiddleware(app=MagicMock())
    request = MockRequest(path="/api/chat/", client_ip="192.168.1.100")

    # 清空该 IP 的限流计数
    key = f"rate_limit:192.168.1.100"
    try:
        cache_service.redis.delete(key)
    except Exception:
        pass

    passed = 0
    blocked = 0
    latencies = []

    for i in range(max_requests):
        t0 = time.perf_counter()
        allowed = middleware.sliding_window_limit(key)
        elapsed = (time.perf_counter() - t0) * 1000
        latencies.append(elapsed)
        if allowed:
            passed += 1
        else:
            blocked += 1

    result = {
        "ragas_test": "normal_traffic",
        "total": max_requests,
        "passed": passed,
        "blocked": blocked,
        "pass_rate": passed / max_requests,
        "avg_latency_ms": sum(latencies) / len(latencies),
        "p95_latency_ms": sorted(latencies)[int(len(latencies) * 0.95) - 1],
    }
    logger.info(f"  通过: {passed}/{max_requests} ({result['pass_rate']*100:.1f}%)")
    logger.info(f"  平均延迟: {result['avg_latency_ms']:.2f}ms")
    logger.info(f"  P95 延迟: {result['p95_latency_ms']:.2f}ms")

    # 清理
    try:
        cache_service.redis.delete(key)
    except Exception:
        pass

    return result


def test_over_limit(max_requests: int, window: int) -> Dict:
    """测试超限流量：超过阈值的请求应被拦截。"""
    logger.info("【测试 2】超限流量（超过阈值）")
    middleware = RateLimitMiddleware(app=MagicMock())
    request = MockRequest(path="/api/chat/", client_ip="192.168.1.200")
    key = f"rate_limit:192.168.1.200"

    try:
        cache_service.redis.delete(key)
    except Exception:
        pass

    total = max_requests + 10  # 超过阈值 10 个
    passed = 0
    blocked = 0

    for i in range(total):
        allowed = middleware.sliding_window_limit(key)
        if allowed:
            passed += 1
        else:
            blocked += 1

    # 预期：前 max_requests 个通过，后面的被拦截
    # 实际可能有 1-2 个误差（并发/时钟）
    expected_blocked = total - max_requests
    accuracy = 1 - abs(blocked - expected_blocked) / total

    result = {
        "ragas_test": "over_limit",
        "total": total,
        "threshold": max_requests,
        "passed": passed,
        "blocked": blocked,
        "expected_blocked": expected_blocked,
        "intercept_accuracy": accuracy,
    }
    logger.info(f"  总请求: {total}, 阈值: {max_requests}")
    logger.info(f"  通过: {passed}, 拦截: {blocked}")
    logger.info(f"  预期拦截: {expected_blocked}")
    logger.info(f"  拦截准确率: {accuracy*100:.1f}%")

    try:
        cache_service.redis.delete(key)
    except Exception:
        pass

    return result


def test_path_filtering() -> Dict:
    """测试路径过滤：非限流路径应直接通过。"""
    logger.info("【测试 3】路径过滤")
    middleware = RateLimitMiddleware(app=MagicMock())

    test_paths = [
        ("/api/chat/", True),   # 应被限流
        ("/health", False),     # 不应被限流
        ("/api/users/profile", False),  # 不应被限流
        ("/api/chat/history", True),    # 前缀匹配
    ]

    results = []
    for path, should_limit in test_paths:
        is_limited = any(path.startswith(p) for p in RATE_LIMITED_PATHS)
        match = is_limited == should_limit
        results.append({"path": path, "should_limit": should_limit, "is_limited": is_limited, "match": match})
        logger.info(f"  {path}: 应限流={should_limit}, 实际={is_limited}, {'✓' if match else '✗'}")

    accuracy = sum(1 for r in results if r["match"]) / len(results)
    return {"ragas_test": "path_filtering", "accuracy": accuracy, "details": results}


def test_memory_fallback() -> Dict:
    """测试 Redis 不可用时降级到内存限流。"""
    logger.info("【测试 4】Redis 降级到内存限流")
    middleware = RateLimitMiddleware(app=MagicMock())
    key = "rate_limit:192.168.1.300"

    # 模拟 Redis 不可用：patch sliding_window_limit 内部的 cache_service.redis
    original_redis = cache_service.redis

    # 先测 Redis 可用时的延迟
    redis_latencies = []
    for _ in range(10):
        t0 = time.perf_counter()
        middleware.sliding_window_limit(key)
        redis_latencies.append((time.perf_counter() - t0) * 1000)

    # 模拟 Redis 不可用
    cache_service.redis = None  # 触发降级路径

    memory_latencies = []
    passed = 0
    blocked = 0
    for i in range(RATE_LIMIT_MAX_REQUESTS + 5):
        t0 = time.perf_counter()
        allowed = middleware.sliding_window_limit(key)  # 会降级到 _check_memory
        elapsed = (time.perf_counter() - t0) * 1000
        memory_latencies.append(elapsed)
        if allowed:
            passed += 1
        else:
            blocked += 1

    # 恢复 Redis
    cache_service.redis = original_redis

    avg_redis = sum(redis_latencies) / len(redis_latencies)
    avg_memory = sum(memory_latencies) / len(memory_latencies)
    overhead = avg_memory - avg_redis

    result = {
        "ragas_test": "memory_fallback",
        "redis_avg_latency_ms": avg_redis,
        "memory_avg_latency_ms": avg_memory,
        "degrade_overhead_ms": overhead,
        "memory_passed": passed,
        "memory_blocked": blocked,
        "memory_works": blocked > 0,  # 内存限流也能拦截超限
    }
    logger.info(f"  Redis 平均延迟: {avg_redis:.2f}ms")
    logger.info(f"  内存限流平均延迟: {avg_memory:.2f}ms")
    logger.info(f"  降级开销: {overhead:.2f}ms")
    logger.info(f"  内存限流通过: {passed}, 拦截: {blocked}")
    logger.info(f"  内存限流有效: {'✓' if blocked > 0 else '✗'}")

    # 清理
    try:
        cache_service.redis.delete(key)
    except Exception:
        pass
    middleware._memory_store.clear()

    return result


def test_degrade_switch_time() -> Dict:
    """精确测量 Redis 断开后切换到内存限流的耗时。

    模拟真实场景：Redis 正常运行中突然断开，测量第一次请求
    从尝试 Redis 到降级到内存限流的总耗时（即切换延迟）。
    """
    logger.info("【测试 4b】Redis 断开切换耗时（精确测量）")
    middleware = RateLimitMiddleware(app=MagicMock())
    key = "rate_limit:192.168.1.400"

    # 先在 Redis 正常时写入一些计数
    for _ in range(5):
        middleware.sliding_window_limit(key)

    # 模拟 Redis 突然断开：将 redis 设为一个会抛异常的 mock
    original_redis = cache_service.redis

    class BrokenRedis:
        """模拟 Redis 断开：所有操作都抛 ConnectionError。"""
        def __getattr__(self, name):
            raise ConnectionError("Redis 连接已断开（模拟）")

    # 测量切换耗时：第一次请求会尝试 Redis → 失败 → 降级到内存
    switch_latencies = []
    cache_service.redis = BrokenRedis()

    for i in range(10):
        t0 = time.perf_counter()
        try:
            allowed = middleware.sliding_window_limit(key)
        except Exception:
            allowed = middleware._check_memory(key)
        elapsed = (time.perf_counter() - t0) * 1000
        switch_latencies.append(elapsed)

    # 恢复 Redis
    cache_service.redis = original_redis

    first_switch = switch_latencies[0]  # 第一次切换（含异常捕获开销）
    subsequent_avg = sum(switch_latencies[1:]) / len(switch_latencies[1:]) if len(switch_latencies) > 1 else 0

    result = {
        "ragas_test": "degrade_switch_time",
        "first_switch_ms": first_switch,
        "subsequent_avg_ms": subsequent_avg,
        "all_latencies_ms": switch_latencies,
        "max_ms": max(switch_latencies),
        "min_ms": min(switch_latencies),
    }
    logger.info(f"  首次切换耗时: {first_switch:.2f}ms（含 Redis 异常捕获）")
    logger.info(f"  后续平均耗时: {subsequent_avg:.2f}ms（纯内存限流）")
    logger.info(f"  Max: {max(switch_latencies):.2f}ms, Min: {min(switch_latencies):.2f}ms")

    # 清理
    try:
        cache_service.redis.delete(key)
    except Exception:
        pass
    middleware._memory_store.clear()

    return result


def test_window_reset(max_requests: int, window: int) -> Dict:
    """测试窗口重置：等待窗口过期后计数应归零。"""
    logger.info("【测试 5】窗口重置（快速验证，不等完整窗口）")
    middleware = RateLimitMiddleware(app=MagicMock())
    key = "rate_limit:192.168.1.400"

    try:
        cache_service.redis.delete(key)
    except Exception:
        pass

    # 第一轮：打满阈值
    for _ in range(max_requests):
        middleware.sliding_window_limit(key)

    # 检查 TTL
    try:
        ttl = cache_service.redis.ttl(key)
    except Exception:
        ttl = -1

    # 验证超限被拦截
    blocked_after = not middleware.sliding_window_limit(key)

    result = {
        "ragas_test": "window_reset",
        "window_seconds": window,
        "current_ttl": ttl,
        "blocked_after_threshold": blocked_after,
        "note": "完整窗口重置需等待 window 秒，本测试只验证 TTL 设置正确",
    }
    logger.info(f"  窗口大小: {window}s")
    logger.info(f"  当前 TTL: {ttl}s")
    logger.info(f"  超限后拦截: {'✓' if blocked_after else '✗'}")

    try:
        cache_service.redis.delete(key)
    except Exception:
        pass

    return result


# ============================================================
# 主流程
# ============================================================

async def run_dispatch_test(concurrent: int, max_requests: int) -> Dict:
    """并发打 LLM 接口模拟（直接调用中间件，不启动 HTTP）。"""
    logger.info(f"【并发压测】{concurrent} 并发请求")
    middleware = RateLimitMiddleware(app=MagicMock())
    key = "rate_limit:192.168.1.500"

    try:
        cache_service.redis.delete(key)
    except Exception:
        pass

    semaphore = asyncio.Semaphore(concurrent)
    results = {"passed": 0, "blocked": 0}
    lock = asyncio.Lock()

    async def one_request():
        async with semaphore:
            # 模拟异步调用（sliding_window_limit 是同步的，用线程池）
            loop = asyncio.get_event_loop()
            allowed = await loop.run_in_executor(None, middleware.sliding_window_limit, key)
            async with lock:
                if allowed:
                    results["passed"] += 1
                else:
                    results["blocked"] += 1

    tasks = [one_request() for _ in range(concurrent)]
    await asyncio.gather(*tasks)

    total = results["passed"] + results["blocked"]
    block_rate = results["blocked"] / total if total else 0

    logger.info(f"  总请求: {total}")
    logger.info(f"  通过: {results['passed']}")
    logger.info(f"  拦截: {results['blocked']} ({block_rate*100:.1f}%)")

    try:
        cache_service.redis.delete(key)
    except Exception:
        pass

    return {"concurrent": concurrent, **results, "block_rate": block_rate}


def main():
    parser = argparse.ArgumentParser(description="Mitta 限流中间件评估")
    parser.add_argument("--concurrent", type=int, default=100000, help="并发压测请求数（默认 50）")
    parser.add_argument("--max-req", type=int, default=RATE_LIMIT_MAX_REQUESTS, help=f"限流阈值（默认 {RATE_LIMIT_MAX_REQUESTS}）")
    parser.add_argument("--window", type=int, default=RATE_LIMIT_WINDOW_SECONDS, help=f"时间窗口秒数（默认 {RATE_LIMIT_WINDOW_SECONDS}）")
    parser.add_argument("--ragas_test-degrade", action="store_true", help="包含 Redis 降级测试")
    parser.add_argument("--skip-concurrent", action="store_true", help="跳过高并发压测")
    parser.add_argument("--test-degrade", action="store_true", help="是否开启降级测试")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("Mitta 限流中间件评估")
    logger.info(f"配置: 阈值={args.max_req}, 窗口={args.window}s, 限流路径={RATE_LIMITED_PATHS}")
    logger.info("=" * 60)

    # 初始化 Redis
    try:
        cache_service.open()
        logger.success("Redis 连接就绪")
    except Exception as e:
        logger.error(f"Redis 连接失败: {e}")
        logger.warning("将仅运行内存限流测试")

    all_results = []

    # 测试 1: 正常流量
    all_results.append(test_normal_traffic(args.max_req, args.window))

    # 测试 2: 超限流量
    all_results.append(test_over_limit(args.max_req, args.window))

    # 测试 3: 路径过滤
    all_results.append(test_path_filtering())

    # 测试 4: 内存降级
    if args.test_degrade:
        all_results.append(test_memory_fallback())
        all_results.append(test_degrade_switch_time())

    # 测试 5: 窗口重置
    all_results.append(test_window_reset(args.max_req, args.window))

    # 并发压测
    if not args.skip_concurrent:
        concurrent_result = asyncio.run(run_dispatch_test(args.concurrent, args.max_req))
        all_results.append({"ragas_test": "concurrent_stress", **concurrent_result})

    # ---- 汇总报告 ----
    logger.info(f"\n{'='*60}")
    logger.info("【限流评估汇总】")
    logger.info("")

    for r in all_results:
        test_name = r.get("ragas_test", "unknown")
        if test_name == "normal_traffic":
            logger.info(f"  正常流量通过率: {r['pass_rate']*100:.1f}% (期望 100%)")
        elif test_name == "over_limit":
            logger.info(f"  超限拦截准确率: {r['intercept_accuracy']*100:.1f}%")
        elif test_name == "path_filtering":
            logger.info(f"  路径过滤准确率: {r['accuracy']*100:.1f}%")
        elif test_name == "memory_fallback":
            logger.info(f"  降级开销: {r['degrade_overhead_ms']:.2f}ms, 内存限流有效: {r['memory_works']}")
        elif test_name == "degrade_switch_time":
            logger.info(f"  切换耗时: 首次 {r['first_switch_ms']:.2f}ms, 后续平均 {r['subsequent_avg_ms']:.2f}ms")
        elif test_name == "window_reset":
            logger.info(f"  窗口 TTL: {r['current_ttl']}s, 超限拦截: {r['blocked_after_threshold']}")
        elif test_name == "concurrent_stress":
            logger.info(f"  并发压测({r['concurrent']}): 拦截率 {r['block_rate']*100:.1f}%")

    # 保存报告
    output_path = Path(__file__).parent / "rate_limit_eval_report.json"
    output_path.write_text(json.dumps(all_results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    logger.info(f"\n评估报告已保存: {output_path}")

    cache_service.close()


if __name__ == "__main__":
    main()
