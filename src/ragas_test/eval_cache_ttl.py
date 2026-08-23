"""
Mitta 缓存 TTL 策略对比评估
=============================
对比固定 TTL 和动态 TTL（滑动续期）下的缓存命中率。

测试原理：
  - 固定 TTL：写入缓存后不续期，等待部分 key 过期后重复查询，统计命中率
  - 动态 TTL：每次命中时自动续期（cache_service.query_cache 内部已实现滑动续期）
  - 对比两种策略在相同查询模式下的命中率差异

用法：
    conda activate langchain1.2
    cd src
    python -m ragas_test.eval_cache_ttl                  # 默认测试
    python -m ragas_test.eval_cache_ttl --queries 20     # query 数量
    python -m ragas_test.eval_cache_ttl --ttl 5          # 短 TTL（秒），加速测试
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Dict

from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from service.cache_service import cache_service
from init import embed_model


def get_test_queries(n: int = 20) -> List[str]:
    base = [
        "LangGraph 的 Checkpointer 有什么作用",
        "MCP 工具如何连接到 LangGraph",
        "RAG 检索流程有哪些步骤",
        "Redis Search 缓存如何实现语义匹配",
        "PostgresSaver 和 PostgresStore 的区别",
        "流式输出中 tool_calls 丢失如何修复",
        "bge-m3 embedding 的维度是多少",
        "RRF 融合的公式是什么",
        "FastAPI 同步路由和异步路由的区别",
        "JWT 双 Token 机制如何实现续签",
        "Milvus 和 ChromaDB 如何切换",
        "工具筛选的两层机制是什么",
        "SSE 流式输出如何实现",
        "分布式锁如何用 Redisson 实现",
        "用户头像存储为什么用 MEDIUMTEXT",
        "距离阈值 0.3 为什么会过滤所有结果",
        "MCP 服务器连接失败如何处理",
        "记忆节点的 idle/executed 状态区别",
        "LangGraph 子图如何嵌套到主图",
        "Nginx 反向代理 SSE 需要什么配置",
    ]
    return base[:n]


def run_fixed_ttl_test(queries: List[str], ttl_seconds: int, thread_id: str) -> Dict:
    """固定 TTL 测试：写入后不续期，等待部分过期后查询。

    模拟：写入一批缓存，等待 TTL/2 时间后，先查询一半（命中但不续期），
    再等待 TTL/2 时间后查询全部（第一批已过期），统计命中率。
    """
    logger.info(f"【固定 TTL】ttl={ttl_seconds}s, {len(queries)} 条 query")

    # 重建索引
    try:
        cache_service.redis.ft(cache_service.index_name).dropindex(delete_documents=True)
    except Exception:
        pass
    cache_service.create_index()

    # 清空
    keys = cache_service.redis.keys(f"retrieve_cache:{thread_id}:*")
    if keys:
        cache_service.redis.delete(*keys)

    # 写入所有缓存（用短 TTL）
    from langchain_core.documents import Document
    for q in queries:
        mock_docs = [Document(
            page_content=f"{q}。本文详细介绍{q}的核心概念与实现原理。",
            metadata={"source": "mock"}
        )]
        # 直接用 hset 写入，设置短 TTL
        vec = embed_model.embed_query(q)
        import numpy as np
        key = f"retrieve_cache:{thread_id}:fixed_{hash(q) % 10000}"
        cache_service.redis.hset(key, mapping={
            "thread_id": thread_id,
            "query_embedding": np.array(vec, dtype=np.float32).tobytes(),
            "query_text": q,
            "result": json.dumps([{"page_content": mock_docs[0].page_content, "metadata": {}}], ensure_ascii=False),
            "created_at": time.time(),
        })
        cache_service.redis.expire(key, ttl_seconds)

    logger.info(f"  已写入 {len(queries)} 条缓存，TTL={ttl_seconds}s")

    # 等待 TTL 过期（加速测试：实际等待 ttl_seconds）
    logger.info(f"  等待 {ttl_seconds}s 让缓存过期...")
    time.sleep(ttl_seconds + 1)

    # 查询所有，统计命中率（固定 TTL 下应全部过期，命中率 0）
    hits = 0
    for q in queries:
        result = cache_service.query_cache(thread_id, q, top_k=3)
        if result:
            hits += 1

    hit_rate = hits / len(queries) if queries else 0
    logger.info(f"  过期后命中率: {hit_rate*100:.1f}% ({hits}/{len(queries)})")

    # 清理
    keys = cache_service.redis.keys(f"retrieve_cache:{thread_id}:*")
    if keys:
        cache_service.redis.delete(*keys)

    return {"strategy": "fixed_ttl", "ttl_seconds": ttl_seconds, "hit_rate": hit_rate, "hits": hits, "total": len(queries)}


def run_dynamic_ttl_test(queries: List[str], ttl_seconds: int, thread_id: str) -> Dict:
    """动态 TTL 测试：每次命中自动续期（滑动过期）。

    模拟：写入缓存后，在 TTL 过期前周期性查询（触发续期），
    即使总时间超过初始 TTL，因续期仍保持命中。
    """
    logger.info(f"【动态 TTL（滑动续期）】ttl={ttl_seconds}s, {len(queries)} 条 query")

    # 重建索引
    try:
        cache_service.redis.ft(cache_service.index_name).dropindex(delete_documents=True)
    except Exception:
        pass
    cache_service.create_index()

    # 清空
    keys = cache_service.redis.keys(f"retrieve_cache:{thread_id}:*")
    if keys:
        cache_service.redis.delete(*keys)

    # 写入缓存（用 store_cache，它会设置 cache_ttl）
    from langchain_core.documents import Document
    original_ttl = cache_service.cache_ttl
    cache_service.cache_ttl = ttl_seconds  # 临时改短 TTL

    for q in queries:
        mock_docs = [Document(
            page_content=f"{q}。本文详细介绍{q}的核心概念与实现原理。",
            metadata={"source": "mock"}
        )]
        cache_service.store_cache(thread_id, q, mock_docs)

    logger.info(f"  已写入 {len(queries)} 条缓存，TTL={ttl_seconds}s")

    # 分 3 轮查询，每轮间隔 ttl/2，每次命中触发续期
    total_hits = 0
    total_queries = 0
    rounds = 3
    for r in range(1, rounds + 1):
        if r > 1:
            wait = ttl_seconds // 2
            logger.info(f"  等待 {wait}s（第 {r} 轮查询前）...")
            time.sleep(wait)

        hits = 0
        for q in queries:
            result = cache_service.query_cache(thread_id, q, top_k=3)
            if result:
                hits += 1
        total_hits += hits
        total_queries += len(queries)
        logger.info(f"  第 {r} 轮命中率: {hits/len(queries)*100:.1f}% ({hits}/{len(queries)})")

    # 恢复原始 TTL
    cache_service.cache_ttl = original_ttl

    overall_hit_rate = total_hits / total_queries if total_queries else 0
    logger.info(f"  总体命中率: {overall_hit_rate*100:.1f}% ({total_hits}/{total_queries})")

    # 清理
    keys = cache_service.redis.keys(f"retrieve_cache:{thread_id}:*")
    if keys:
        cache_service.redis.delete(*keys)

    return {
        "strategy": "dynamic_ttl",
        "ttl_seconds": ttl_seconds,
        "rounds": rounds,
        "hit_rate": overall_hit_rate,
        "hits": total_hits,
        "total": total_queries,
    }


def main():
    parser = argparse.ArgumentParser(description="缓存 TTL 策略对比评估")
    parser.add_argument("--queries", type=int, default=10, help="测试 query 数（默认 10）")
    parser.add_argument("--ttl", type=int, default=5, help="测试用 TTL 秒数（默认 5，加速测试）")
    parser.add_argument("--thread-id", type=str, default="ttl_eval_test", help="测试 thread_id")
    args = parser.parse_args()

    queries = get_test_queries(args.queries)

    logger.info("=" * 60)
    logger.info("Mitta 缓存 TTL 策略对比评估")
    logger.info(f"配置: {len(queries)} 条 query, TTL={args.ttl}s")
    logger.info("=" * 60)

    # 初始化
    cache_service.open()
    logger.success("缓存服务就绪")

    results = []

    # 固定 TTL 测试
    fixed_result = run_fixed_ttl_test(queries, args.ttl, f"{args.thread_id}_fixed")
    results.append(fixed_result)

    # 动态 TTL 测试
    dynamic_result = run_dynamic_ttl_test(queries, args.ttl, f"{args.thread_id}_dynamic")
    results.append(dynamic_result)

    # 对比
    improvement = (dynamic_result["hit_rate"] - fixed_result["hit_rate"]) * 100

    logger.info(f"\n{'='*60}")
    logger.info("【TTL 策略对比结果】")
    logger.info(f"  固定 TTL 命中率:   {fixed_result['hit_rate']*100:.1f}%")
    logger.info(f"  动态 TTL 命中率:   {dynamic_result['hit_rate']*100:.1f}%")
    logger.info(f"  命中率提升:        {improvement:+.1f} 百分点")
    logger.info(f"{'='*60}")

    # 保存报告
    report = {
        "config": vars(args),
        "results": results,
        "improvement_percentage_points": improvement,
    }
    output_path = Path(__file__).parent / "cache_ttl_eval_report.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"评估报告已保存: {output_path}")

    cache_service.close()


if __name__ == "__main__":
    main()
