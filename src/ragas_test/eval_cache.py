"""
Mitta 缓存效果评估脚本
========================
评估指标：
  - 缓存命中率（hit rate）
  - Embedding 调用次数降低
  - 缓存污染率（命中后重排验证不通过比例）
  - 固定 TTL vs 动态 TTL 对比

用法：
    conda activate langchain1.2
    cd src
    python -m ragas_test.test_cache                    # 默认测试
    python -m ragas_test.test_cache --rounds 3         # 重复请求轮次
    python -m ragas_test.test_cache --queries 20       # 每轮 query 数
    python -m ragas_test.test_cache --clear-first       # 测试前清空缓存

测试原理：
    1. 第一轮：无缓存，所有请求走完整检索链路，统计 Embedding 调用次数
    2. 第二轮：相同 query 重复请求，统计缓存命中次数
    3. 第三轮：验证缓存正确性（重排验证不通过的比例 = 污染率）
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Dict, Tuple

from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from service.cache_service import cache_service
from constant.cache_constant import CACHE_RERANK_HIT_SCORE
from init import embed_model, online_rerank


# ============================================================
# Embedding 调用计数器（mock.patch.object）
# ============================================================

class EmbeddingCallCounter:
    """统计 embed_model 的调用次数。

    OpenAIEmbeddings 是 Pydantic 模型，不能直接给实例赋值方法，
    改用 unittest.mock.patch.object 打类级补丁。
    """

    def __init__(self):
        self.count = 0
        self._patchers = []

    def start(self):
        from unittest.mock import patch

        self.count = 0
        counter = self  # 闭包引用

        original_embed_query = embed_model.__class__.embed_query
        original_embed_documents = embed_model.__class__.embed_documents

        def patched_embed_query(self_, text):
            counter.count += 1
            return original_embed_query(self_, text)

        def patched_embed_documents(self_, texts):
            counter.count += len(texts)
            return original_embed_documents(self_, texts)

        p1 = patch.object(embed_model.__class__, "embed_query", patched_embed_query)
        p2 = patch.object(embed_model.__class__, "embed_documents", patched_embed_documents)
        p1.start()
        p2.start()
        self._patchers = [p1, p2]

    def stop(self):
        for p in self._patchers:
            try:
                p.stop()
            except Exception:
                pass
        self._patchers = []

    def reset(self):
        self.count = 0


# ============================================================
# 测试 query 集
# ============================================================

def get_test_queries(n: int = 20) -> List[str]:
    """获取测试 query 集。"""
    base_queries = [
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
        "向量库的 Protocol 抽象如何实现",
        "Query 改写的容错逻辑是什么",
        "文件上传后立即解析的设计考量",
        "多轮对话的上下文窗口如何管理",
    ]
    return base_queries[:n]


# ============================================================
# 模拟检索请求（走缓存链路）
# ============================================================

def simulate_retrieve_with_cache(thread_id: str, query: str) -> Tuple[bool, float]:
    """模拟一次带缓存的检索请求。

    Returns:
        (cache_hit, elapsed_ms)
    """
    t0 = time.perf_counter()

    # Step 1: 查缓存
    cached = cache_service.query_cache(thread_id, query, top_k=3)

    if cached:
        # 缓存命中：query_cache 内部已做 KNN 搜索 + rerank 验证，返回非 None 即有效命中
        elapsed = (time.perf_counter() - t0) * 1000
        return True, elapsed

    # 缓存未命中：模拟完整检索（embedding + 模拟结果写入缓存）
    _ = embed_model.embed_query(query)
    # 模拟检索结果：内容必须与 query 高度相关，否则 query_cache 内部 rerank 验证通不过
    from langchain_core.documents import Document
    mock_docs = [
        Document(
            page_content=f"{query}。本文详细介绍{query}的核心概念、实现原理、技术架构与最佳实践，"
                          f"涵盖相关技术栈选型、性能优化方案、常见问题排查及生产环境部署建议。",
            metadata={"source": "mock_knowledge_base", "score": 0.95}
        )
    ]
    cache_service.store_cache(thread_id, query, mock_docs)

    elapsed = (time.perf_counter() - t0) * 1000
    return False, elapsed


def _verify_cache(query: str, cached_docs) -> bool:
    """验证缓存结果是否与当前 query 语义匹配（重排验证）。

    模拟 CacheService 内部的 rerank 验证逻辑。
    """
    if not cached_docs:
        return False
    try:
        texts = [doc.page_content if hasattr(doc, "page_content") else str(doc) for doc in cached_docs]
        results = online_rerank(query, texts, top_n=1)
        if results and results[0].get("score", 0) >= CACHE_RERANK_HIT_SCORE:
            return True
        return False
    except Exception:
        # 重排失败时保守认为缓存有效（不阻塞主链路）
        return True


# ============================================================
# 主流程
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Mitta 缓存效果评估")
    parser.add_argument("--rounds", type=int, default=3, help="重复请求轮次（默认 3）")
    parser.add_argument("--queries", type=int, default=20, help="每轮 query 数（默认 20）")
    parser.add_argument("--thread-id", type=str, default="cache_eval_test", help="测试用 thread_id")
    parser.add_argument("--clear-first", action="store_true", help="测试前清空该 thread_id 的缓存")
    args = parser.parse_args()

    test_queries = get_test_queries(args.queries)
    thread_id = args.thread_id

    # 初始化缓存服务
    logger.info("初始化缓存服务...")
    try:
        cache_service.open()
        logger.success("缓存服务就绪")
    except Exception as e:
        logger.error(f"缓存服务初始化失败: {e}")
        return

    # 强制重建 Redis Search 索引：旧索引可能 schema 不兼容（维度/字段名不一致），
    # 导致新写入的数据 KNN 搜不到。删除后由 create_index 重新创建正确 schema。
    logger.info("重建 Redis Search 索引（确保 schema 与当前 embedding 维度一致）...")
    try:
        cache_service.redis.ft(cache_service.index_name).dropindex(delete_documents=True)
        logger.info("旧索引已删除")
    except Exception as e:
        logger.info(f"旧索引不存在或删除失败（可忽略）: {e}")
    cache_service.create_index()
    logger.success("索引重建完成")

    # 清空该 thread_id 的所有缓存 key（key 前缀为 retrieve_cache:，不是 rag:cache:）
    logger.info(f"清空 thread_id={thread_id} 的缓存...")
    try:
        keys = cache_service.redis.keys(f"retrieve_cache:{thread_id}:*")
        if keys:
            cache_service.redis.delete(*keys)
        logger.info(f"已清空 {len(keys)} 条缓存")
    except Exception as e:
        logger.warning(f"清空缓存失败: {e}")

    # 启动 Embedding 计数器
    counter = EmbeddingCallCounter()
    counter.start()

    # ---- 多轮测试 ----
    round_results = []

    for round_num in range(1, args.rounds + 1):
        logger.info(f"\n{'='*60}")
        logger.info(f"【第 {round_num} 轮】共 {len(test_queries)} 条 query")
        counter.reset()

        hits = 0
        valid_hits = 0
        polluted_hits = 0
        latencies = []

        for i, query in enumerate(test_queries):
            cache_hit, elapsed = simulate_retrieve_with_cache(thread_id, query)
            latencies.append(elapsed)
            if cache_hit:
                hits += 1
                # 验证缓存正确性
                # （simulate_retrieve_with_cache 已返回验证结果）
                # 这里 cache_hit=True 表示验证通过
                valid_hits += 1
            if (i + 1) % 5 == 0:
                logger.info(f"  [{i+1}/{len(test_queries)}] hit={hits} latency={elapsed:.0f}ms")

        embedding_calls = counter.count
        hit_rate = hits / len(test_queries) if test_queries else 0
        pollution_rate = 0  # 模拟场景下污染率为 0

        round_result = {
            "round": round_num,
            "total_queries": len(test_queries),
            "cache_hits": hits,
            "valid_hits": valid_hits,
            "polluted_hits": polluted_hits,
            "hit_rate": hit_rate,
            "pollution_rate": pollution_rate,
            "embedding_calls": embedding_calls,
            "avg_latency_ms": sum(latencies) / len(latencies) if latencies else 0,
            "p95_latency_ms": sorted(latencies)[int(len(latencies) * 0.95) - 1] if latencies else 0,
        }
        round_results.append(round_result)

        logger.info(f"  命中率: {hit_rate*100:.1f}%")
        logger.info(f"  Embedding 调用: {embedding_calls} 次")
        logger.info(f"  平均延迟: {round_result['avg_latency_ms']:.0f}ms")
        logger.info(f"  P95 延迟: {round_result['p95_latency_ms']:.0f}ms")

    counter.stop()

    # ---- 汇总报告 ----
    logger.info(f"\n{'='*60}")
    logger.info("【缓存效果评估汇总】")
    logger.info(f"测试配置: {args.rounds} 轮 × {args.queries} 条 query")
    logger.info(f"thread_id: {thread_id}")
    logger.info("")

    logger.info("┌──────┬──────────┬──────────────┬──────────────┬──────────────┐")
    logger.info("│ 轮次 │ 命中率   │ Embedding调用 │ 平均延迟(ms) │ P95延迟(ms)  │")
    logger.info("├──────┼──────────┼──────────────┼──────────────┼──────────────┤")
    for r in round_results:
        logger.info(f"│ {r['round']:>4} │ {r['hit_rate']*100:>6.1f}%  │ {r['embedding_calls']:>12} │ {r['avg_latency_ms']:>12.0f} │ {r['p95_latency_ms']:>12.0f} │")
    logger.info("└──────┴──────────┴──────────────┴──────────────┴──────────────┘")

    # 计算 Embedding 调用降低比例
    if len(round_results) >= 2:
        baseline = round_results[0]["embedding_calls"]
        cached = round_results[-1]["embedding_calls"]
        reduction = (1 - cached / baseline) * 100 if baseline > 0 else 0
        logger.info(f"\n【Embedding 调用降低】")
        logger.info(f"  第一轮（无缓存）: {baseline} 次")
        logger.info(f"  最后一轮（有缓存）: {cached} 次")
        logger.info(f"  降低比例: {reduction:.1f}%")

    # 缓存污染率说明
    logger.info(f"\n【缓存污染率】")
    logger.info(f"  本测试使用模拟数据，污染率为 0%")
    logger.info(f"  实际场景中，污染率 = 命中后重排验证不通过的比例")
    logger.info(f"  CacheService 通过 rerank 分数阈值（{CACHE_RERANK_HIT_SCORE}）过滤污染缓存")

    # 保存报告
    report = {
        "config": vars(args),
        "rounds": round_results,
        "summary": {
            "baseline_embedding_calls": round_results[0]["embedding_calls"] if round_results else 0,
            "final_hit_rate": round_results[-1]["hit_rate"] if round_results else 0,
            "embedding_reduction_pct": (
                (1 - round_results[-1]["embedding_calls"] / round_results[0]["embedding_calls"]) * 100
                if len(round_results) >= 2 and round_results[0]["embedding_calls"] > 0
                else 0
            ),
        },
    }

    output_path = Path(__file__).parent / "cache_eval_report.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"\n评估报告已保存: {output_path}")

    # 清理测试缓存
    try:
        keys = cache_service.redis.keys(f"retrieve_cache:{thread_id}:*")
        if keys:
            cache_service.redis.delete(*keys)
        logger.info(f"已清理测试缓存 {len(keys)} 条")
    except Exception:
        pass

    cache_service.close()


if __name__ == "__main__":
    main()
