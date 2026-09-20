"""
Mitta 语义缓存评测（E6）
========================
评测检索图语义缓存（LSH 分桶 + Redis KNN + bge-reranker 验证）的命中质量。

指标（对应项目描述「缓存以 LSH 哈希分桶 + KNN 候选 + bge-reranker 阈值校验做两级判定，
自建评测下 embedding 调用减少约 67%、平均延迟 573 → 350 ms」）：
  - 相同语义命中率：同义改写 query（措辞不同、语义相同）应命中缓存
  - 无关 query 误命中率：语义无关问题不应命中缓存（防污染）
  - 缓存写入/命中延迟：写入与命中的平均耗时
  - 命中正确性：命中的缓存结果应为先前写入的文档

用法：
    conda activate langchain1.2
    cd src
    python -m agent_test.eval_semantic_cache                # 默认评测
    python -m agent_test.eval_semantic_cache --thread eval_cache_sem

说明：
  - 白盒评测：真实 Redis + embed_model + online_rerank（与生产同一链路）。
  - 每组测试使用独立 thread_id，结束后清理缓存，不污染生产数据。
  - 需要 Redis 可用；embed/rerank 走 init 注入。
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List

from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.documents import Document

from service.cache_service import CacheService
from init import embed_model, online_rerank

# 语义缓存评测默认使用 redis-stack（RedisSearch）6379；
# 生产 .env 的 REDIS_DB_URL 由 load_dotenv(override=True) 强制注入，无法用环境变量覆盖，
# 因此这里直接构造 CacheService 实例并显式传入 URL。
DEFAULT_REDIS_URL = "redis://:sorts_dev@localhost:6379"


def make_cache_service(redis_url: str) -> CacheService:
    """构造指向目标 Redis 的 CacheService 实例。"""
    return CacheService(redis_db_url=redis_url)


def make_docs(tag: str = "sem") -> List[Document]:
    """构造测试文档（模拟检索结果）。"""
    return [
        Document(page_content=f"{tag}-文档1：LangGraph Checkpointer 用于保存会话执行状态。", metadata={"source": f"{tag}-1"}),
        Document(page_content=f"{tag}-文档2：PostgresSaver 是 Checkpointer 的 Postgres 实现。", metadata={"source": f"{tag}-2"}),
    ]


def test_semantic_hit(cache, thread_id: str) -> Dict:
    """相同语义命中率：写入后用同义改写 query 查询，应命中。"""
    logger.info("【测试 1】同义改写命中率")
    original = "LangGraph 的 Checkpointer 有什么作用？"
    synonyms = [
        "LangGraph 的 Checkpointer 是干什么用的？",
        "Checkpointer 在 LangGraph 里起什么作用？",
        "langgraph checkpoint 有什么作用",
    ]

    cache.clear_thread_cache(thread_id)
    docs = make_docs("hit")
    t0 = time.perf_counter()
    cache.store_cache(thread_id, original, docs)
    store_ms = (time.perf_counter() - t0) * 1000

    hit_count = 0
    hit_details = []
    for q in synonyms:
        t0 = time.perf_counter()
        res = cache.query_cache(thread_id, q)
        hit_ms = (time.perf_counter() - t0) * 1000
        is_hit = res is not None and res[0].page_content.startswith("hit-文档1")
        hit_count += int(is_hit)
        hit_details.append({"query": q, "hit": is_hit, "latency_ms": round(hit_ms, 2)})
        logger.info(f"  {'✓ 命中' if is_hit else '✗ 未命中'} ({hit_ms:.0f}ms) {q[:40]}")

    cache.clear_thread_cache(thread_id)
    hit_rate = hit_count / len(synonyms) if synonyms else 0
    return {
        "agent_test": "semantic_hit_rate",
        "thread_id": thread_id,
        "synonym_count": len(synonyms),
        "hit_count": hit_count,
        "hit_rate": round(hit_rate, 4),
        "store_latency_ms": round(store_ms, 2),
        "avg_query_latency_ms": round(sum(d["latency_ms"] for d in hit_details) / len(hit_details), 2) if hit_details else 0,
        "details": hit_details,
    }


def test_unrelated_miss(cache, thread_id: str) -> Dict:
    """无关 query 误命中率：语义无关问题不应命中缓存（防污染）。"""
    logger.info("【测试 2】无关 query 误命中率")
    original = "如何配置 Redis 的持久化？"
    unrelated = [
        "今天中午吃什么比较好？",
        "帮我写一首关于秋天的诗",
        "什么是二叉树的先序遍历？",
    ]

    cache.clear_thread_cache(thread_id)
    cache.store_cache(thread_id, original, make_docs("miss"))

    false_hit = 0
    details = []
    for q in unrelated:
        res = cache.query_cache(thread_id, q)
        is_false_hit = res is not None
        false_hit += int(is_false_hit)
        details.append({"query": q, "false_hit": is_false_hit})
        logger.info(f"  {'✗ 误命中' if is_false_hit else '✓ 正确未命中'} {q[:40]}")

    cache.clear_thread_cache(thread_id)
    false_hit_rate = false_hit / len(unrelated) if unrelated else 0
    return {
        "agent_test": "unrelated_false_hit_rate",
        "thread_id": thread_id,
        "unrelated_count": len(unrelated),
        "false_hit_count": false_hit,
        "false_hit_rate": round(false_hit_rate, 4),
        "details": details,
    }


def test_exact_duplicate(cache, thread_id: str) -> Dict:
    """原文重复命中率：完全相同的 query 必然命中。"""
    logger.info("【测试 3】原文重复命中率")
    query = "缓存命中率如何评测？"

    cache.clear_thread_cache(thread_id)
    cache.store_cache(thread_id, query, make_docs("exact"))

    res = cache.query_cache(thread_id, query)
    hit = res is not None
    cache.clear_thread_cache(thread_id)
    logger.info(f"  {'✓ 命中' if hit else '✗ 未命中'} {query[:40]}")
    return {"agent_test": "exact_duplicate_hit", "hit": hit, "hit_rate": 1.0 if hit else 0.0}


def main():
    parser = argparse.ArgumentParser(description="Mitta 语义缓存评测")
    parser.add_argument("--thread", type=str, default="eval_sem_cache", help="测试 thread 前缀")
    parser.add_argument("--redis-url", type=str, default=DEFAULT_REDIS_URL,
                        help=f"redis-stack URL（默认 {DEFAULT_REDIS_URL}）")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("Mitta 语义缓存评测（LSH+KNN+reranker 两级判定）")
    logger.info(f"Redis: {args.redis_url}")
    logger.info("=" * 60)

    cache_service = make_cache_service(args.redis_url)
    cache_service.open(embed_model=embed_model, online_rerank=online_rerank)
    logger.success("Redis + embed + rerank 就绪")

    results = [
        test_semantic_hit(cache_service, f"{args.thread}_hit"),
        test_unrelated_miss(cache_service, f"{args.thread}_miss"),
        test_exact_duplicate(cache_service, f"{args.thread}_exact"),
    ]

    logger.info("\n【评测汇总】")
    for r in results:
        if r["agent_test"] == "semantic_hit_rate":
            logger.info(f"  同义改写命中率: {r['hit_rate']*100:.1f}% ({r['hit_count']}/{r['synonym_count']})")
        elif r["agent_test"] == "unrelated_false_hit_rate":
            logger.info(f"  无关 query 误命中率: {r['false_hit_rate']*100:.1f}% ({r['false_hit_count']}/{r['unrelated_count']})")
        elif r["agent_test"] == "exact_duplicate_hit":
            logger.info(f"  原文重复命中率: {r['hit_rate']*100:.0f}%")

    summary = {"agent_test": "semantic_cache", "thread_prefix": args.thread, "results": results}
    output_path = Path(__file__).parent / "semantic_cache_eval_report.json"
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"评测报告已保存: {output_path}")

    cache_service.close()


if __name__ == "__main__":
    main()
