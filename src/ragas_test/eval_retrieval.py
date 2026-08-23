"""
Mitta 检索链路离线评估脚本
============================
评估指标：
  - Top5 召回率（recall@5）
  - 检索 P95 延迟
  - 单路召回 vs 三级流水线（改写+多路召回+RRF+重排）对比

用法：
    conda activate langchain1.2
    cd src
    python -m ragas_test.test_retrieval                  # 默认 50 条 query
    python -m ragas_test.test_retrieval --limit 100      # 指定 query 数量
    python -m ragas_test.test_retrieval --threshold 0.5   # 指定距离阈值
    python -m ragas_test.test_retrieval --no-pipeline     # 只测单路召回

测试集说明：
    从 resources/knowledge-base/ragas_test-qa 目录下的 markdown 文件解析 QA 对，
    以 question 为查询，ground_truth 对应的文档为期望召回文档。
    若无标注文件，使用内置示例 query 集。
"""
import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import List, Dict, Tuple

from loguru import logger

from config import load_vector_db_config

# 确保 src 在路径中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from constant.retrieval_constants import TOP_K, DISTANCE_THRESHOLD, RRF_K
from vector.vector_store import create_vector_store
from vector.retrieve_doc import RetrievedDoc
from init import embed_model, online_rerank, model


# ============================================================
# 测试集加载
# ============================================================

def load_test_queries(limit: int = 50) -> List[Dict]:
    """从 ragas_test-qa 目录加载测试 query，无文件时使用内置示例。"""
    test_qa_dir = Path(__file__).parent.parent.parent / "resources" / "knowledge-base" / "ragas_test-qa"
    queries = []

    if test_qa_dir.exists():
        for md_file in sorted(test_qa_dir.glob("*.md")):
            try:
                content = md_file.read_text(encoding="utf-8")
                # 简单解析 markdown 中的 Q: / A: 对
                lines = content.split("\n")
                current_q = None
                for line in lines:
                    line = line.strip()
                    if line.startswith("Q:") or line.startswith("Q：") or line.startswith("**Q"):
                        current_q = line.lstrip("Q:：* ").strip()
                    elif current_q and (line.startswith("A:") or line.startswith("A：") or line.startswith("**A")):
                        answer = line.lstrip("A:：* ").strip()
                        if current_q and len(current_q) > 5:
                            queries.append({
                                "query": current_q,
                                "expected_keywords": answer[:200],
                                "source_file": md_file.name,
                            })
                        current_q = None
                    elif line.startswith("## ") and current_q:
                        current_q = None
            except Exception as e:
                logger.warning(f"解析 {md_file.name} 失败: {e}")

    # 内置示例 query（无标注文件时兜底）
    if not queries:
        queries = [
            {"query": "LangGraph 的 Checkpointer 有什么作用", "expected_keywords": "checkpoint 状态 持久化", "source_file": "builtin"},
            {"query": "MCP 工具如何连接到 LangGraph", "expected_keywords": "MCP tool StructuredTool", "source_file": "builtin"},
            {"query": "RAG 检索流程有哪些步骤", "expected_keywords": "检索 改写 重排 RRF", "source_file": "builtin"},
            {"query": "Redis Search 缓存如何实现语义匹配", "expected_keywords": "Redis Search KNN 向量 缓存", "source_file": "builtin"},
            {"query": "PostgresSaver 和 PostgresStore 的区别", "expected_keywords": "checkpointer store 对话 记忆", "source_file": "builtin"},
            {"query": "流式输出中 tool_calls 丢失如何修复", "expected_keywords": "AIMessageChunk add 合并 tool_calls", "source_file": "builtin"},
            {"query": "bge-m3 embedding 的维度是多少", "expected_keywords": "1024 维度 embedding", "source_file": "builtin"},
            {"query": "RRF 融合的公式是什么", "expected_keywords": "倒数排名 RRF k rank", "source_file": "builtin"},
            {"query": "FastAPI 同步路由和异步路由的区别", "expected_keywords": "同步 异步 线程池 事件循环", "source_file": "builtin"},
            {"query": "JWT 双 Token 机制如何实现续签", "expected_keywords": "access refresh token 续签 Redis", "source_file": "builtin"},
        ]

    return queries[:limit]


# ============================================================
# 单路召回（baseline）
# ============================================================

def single_path_retrieve(vector_store, query: str, top_k: int, threshold: float) -> Tuple[List[RetrievedDoc], float]:
    """单路召回：直接用原始 query 做向量检索，不做改写和重排。"""
    t0 = time.perf_counter()
    results = vector_store.query([query], top_k, threshold)
    elapsed = time.perf_counter() - t0
    return results[0] if results else [], elapsed


# ============================================================
# 三级流水线（改写 + 多路召回 + RRF + 重排）
# ============================================================

def pipeline_retrieve(vector_store, query: str, top_k: int, threshold: float) -> Tuple[List[RetrievedDoc], float, Dict]:
    """三级流水线：query 改写 → 多路向量检索 → RRF 融合 → 重排。"""
    stats = {"rewrite_time": 0, "retrieve_time": 0, "rerank_time": 0, "num_queries": 1}
    t_total = time.perf_counter()

    # Step 1: Query 改写
    t0 = time.perf_counter()
    try:
        rewrite_prompt = (
            f"将以下用户问题改写为适合向量检索的查询，输出 JSON 格式：\n"
            f'{{"主查询": "...", "子查询": ["...", "..."], "关键词": ["...", "..."]}}\n\n'
            f"用户问题：{query}"
        )
        resp = model.invoke(rewrite_prompt, response_format={"type": "json_object"})
        raw = json.loads(resp.content)
        queries = [raw.get("主查询", query)] + raw.get("子查询", [])
        queries = [q for q in queries if q and len(q) > 2][:4]  # 最多 4 路
        if not queries:
            queries = [query]
    except Exception as e:
        logger.warning(f"Query 改写失败，使用原始 query: {e}")
        queries = [query]
    stats["rewrite_time"] = time.perf_counter() - t0
    stats["num_queries"] = len(queries)

    # Step 2: 多路向量检索
    t0 = time.perf_counter()
    all_results = vector_store.query(queries, top_k, threshold)
    stats["retrieve_time"] = time.perf_counter() - t0

    # Step 3: RRF 融合
    scores = {}
    for docs in all_results:
        for rank, doc in enumerate(docs):
            key = doc.id or doc.text
            if key not in scores:
                scores[key] = {"doc": doc, "score": 0.0}
            scores[key]["score"] += 1.0 / (RRF_K + rank + 1)
    merged = [item["doc"] for item in sorted(scores.values(), key=lambda x: x["score"], reverse=True)]

    # Step 4: 重排
    t0 = time.perf_counter()
    if merged:
        try:
            rerank_results = online_rerank(queries[0], [doc.text for doc in merged], top_n=top_k)
            final_docs = [merged[r["index"]] for r in rerank_results]
        except Exception as e:
            logger.warning(f"重排失败，使用 RRF 结果: {e}")
            final_docs = merged[:top_k]
    else:
        final_docs = []
    stats["rerank_time"] = time.perf_counter() - t0

    total_elapsed = time.perf_counter() - t_total
    return final_docs, total_elapsed, stats


# ============================================================
# 评估指标
# ============================================================

def evaluate_recall(retrieved_docs: List[RetrievedDoc], expected_keywords: str, k: int = 5) -> float:
    """评估 Top-K 召回率：检索结果中是否包含期望关键词。

    简化评估：检查检索文档文本是否包含期望答案中的关键词。
    更精确的评估需要人工标注文档 ID 匹配。
    """
    if not retrieved_docs or not expected_keywords:
        return 0.0

    # 从期望答案中提取关键词（简单分词）
    keywords = [w for w in expected_keywords.replace("，", " ").replace("。", " ").split() if len(w) >= 2]
    if not keywords:
        keywords = [expected_keywords[:10]]

    top_k_docs = retrieved_docs[:k]
    combined_text = " ".join(doc.text for doc in top_k_docs)

    hit_count = sum(1 for kw in keywords if kw in combined_text)
    return hit_count / len(keywords) if keywords else 0.0


def percentile(data: List[float], p: float) -> float:
    """计算百分位数。"""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * p / 100
    f = int(k)
    c = f + 1 if f + 1 < len(sorted_data) else f
    return sorted_data[f] + (sorted_data[c] - sorted_data[f]) * (k - f)


# ============================================================
# 主流程
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Mitta 检索链路离线评估")
    parser.add_argument("--limit", type=int, default=50, help="测试 query 数量（默认 50）")
    parser.add_argument("--threshold", type=float, default=DISTANCE_THRESHOLD, help=f"距离阈值（默认 {DISTANCE_THRESHOLD}）")
    parser.add_argument("--top-k", type=int, default=TOP_K, help=f"Top-K（默认 {TOP_K}）")
    parser.add_argument("--no-pipeline", action="store_true", help="只测单路召回，不测三级流水线")
    args = parser.parse_args()

    logger.info(f"加载测试集（limit={args.limit}）...")
    test_queries = load_test_queries(args.limit)
    logger.info(f"加载完成，共 {len(test_queries)} 条 query")

    if not test_queries:
        logger.error("无测试 query，退出")
        return

    # 初始化向量库
    logger.info("初始化向量库...")
    vector_store = create_vector_store(load_vector_db_config())
    logger.info(f"向量库就绪，collection 文档数: {vector_store.count()}")

    # ---- 单路召回评估 ----
    logger.info("=" * 60)
    logger.info("【单路召回评估】")
    single_recalls = []
    single_latencies = []
    single_zero_count = 0

    for i, item in enumerate(test_queries):
        query = item["query"]
        docs, elapsed = single_path_retrieve(vector_store, query, args.top_k, args.threshold)
        recall = evaluate_recall(docs, item["expected_keywords"], args.top_k)
        single_recalls.append(recall)
        single_latencies.append(elapsed)
        if not docs:
            single_zero_count += 1
        if (i + 1) % 10 == 0:
            logger.info(f"  [{i+1}/{len(test_queries)}] recall@5={recall:.2f} latency={elapsed*1000:.0f}ms docs={len(docs)}")

    # ---- 三级流水线评估 ----
    pipeline_recalls = []
    pipeline_latencies = []
    pipeline_zero_count = 0
    pipeline_stats_list = []

    if not args.no_pipeline:
        logger.info("=" * 60)
        logger.info("【三级流水线评估】（改写 + 多路召回 + RRF + 重排）")
        for i, item in enumerate(test_queries):
            query = item["query"]
            docs, elapsed, stats = pipeline_retrieve(vector_store, query, args.top_k, args.threshold)
            recall = evaluate_recall(docs, item["expected_keywords"], args.top_k)
            pipeline_recalls.append(recall)
            pipeline_latencies.append(elapsed)
            pipeline_stats_list.append(stats)
            if not docs:
                pipeline_zero_count += 1
            if (i + 1) % 10 == 0:
                logger.info(f"  [{i+1}/{len(test_queries)}] recall@5={recall:.2f} latency={elapsed*1000:.0f}ms docs={len(docs)} queries={stats['num_queries']}")

    # ---- 汇总报告 ----
    logger.info("=" * 60)
    logger.info("【评估汇总报告】")
    logger.info(f"测试 query 数: {len(test_queries)}")
    logger.info(f"距离阈值: {args.threshold}")
    logger.info(f"Top-K: {args.top_k}")
    logger.info("")

    # 辅助函数：安全获取流水线数据，无流水线时返回 "未测试"
    def pval(data, fmt="{:.4f}"):
        return fmt.format(statistics.mean(data)) if data else "未测试"
    def pval_ms(data):
        return f"{statistics.mean(data)*1000:.1f}" if data else "未测试"
    def pval_pct(data):
        return f"{percentile(data, 95)*1000:.1f}" if data else "未测试"
    def pzero(count):
        return f"{count/len(test_queries)*100:.1f}%" if test_queries else "N/A"

    logger.info("┌────────────────────┬──────────────┬──────────────┐")
    logger.info("│ 指标               │ 单路召回     │ 三级流水线   │")
    logger.info("├────────────────────┼──────────────┼──────────────┤")
    logger.info(f"│ 平均 recall@{args.top_k}      │ {statistics.mean(single_recalls):.4f}       │ {pval(pipeline_recalls)}       │")
    logger.info(f"│ 中位数 recall@{args.top_k}    │ {statistics.median(single_recalls):.4f}       │ {pval(pipeline_recalls, '{:.4f}') if pipeline_recalls else '未测试     '}       │")
    logger.info(f"│ 平均延迟(ms)       │ {statistics.mean(single_latencies)*1000:.1f}        │ {pval_ms(pipeline_latencies)}        │")
    logger.info(f"│ P50 延迟(ms)       │ {percentile(single_latencies, 50)*1000:.1f}        │ {f'{percentile(pipeline_latencies, 50)*1000:.1f}' if pipeline_latencies else '未测试      '}        │")
    logger.info(f"│ P95 延迟(ms)       │ {percentile(single_latencies, 95)*1000:.1f}        │ {pval_pct(pipeline_latencies)}        │")
    logger.info(f"│ P99 延迟(ms)       │ {percentile(single_latencies, 99)*1000:.1f}        │ {f'{percentile(pipeline_latencies, 99)*1000:.1f}' if pipeline_latencies else '未测试      '}        │")
    logger.info(f"│ 0 结果占比          │ {single_zero_count/len(test_queries)*100:.1f}%        │ {pipeline_zero_count/len(test_queries)*100:.1f}%        │" if pipeline_recalls else f"│ 0 结果占比          │ {single_zero_count/len(test_queries)*100:.1f}%        │ 未测试       │")
    logger.info("└────────────────────┴──────────────┴──────────────┘")

    if pipeline_stats_list:
        avg_rewrite = statistics.mean(s["rewrite_time"] for s in pipeline_stats_list) * 1000
        avg_retrieve = statistics.mean(s["retrieve_time"] for s in pipeline_stats_list) * 1000
        avg_rerank = statistics.mean(s["rerank_time"] for s in pipeline_stats_list) * 1000
        avg_queries = statistics.mean(s["num_queries"] for s in pipeline_stats_list)
        logger.info("")
        logger.info("【流水线各阶段耗时分解】")
        logger.info(f"  Query 改写:  {avg_rewrite:.1f}ms (平均 {avg_queries:.1f} 路查询)")
        logger.info(f"  向量检索:    {avg_retrieve:.1f}ms")
        logger.info(f"  重排:        {avg_rerank:.1f}ms")

    # 保存结果
    report = {
        "config": {"limit": args.limit, "threshold": args.threshold, "top_k": args.top_k},
        "single_path": {
            "avg_recall": statistics.mean(single_recalls),
            "median_recall": statistics.median(single_recalls),
            "avg_latency_ms": statistics.mean(single_latencies) * 1000,
            "p95_latency_ms": percentile(single_latencies, 95) * 1000,
            "zero_result_ratio": single_zero_count / len(test_queries),
        },
    }
    if pipeline_recalls:
        report["pipeline"] = {
            "avg_recall": statistics.mean(pipeline_recalls),
            "median_recall": statistics.median(pipeline_recalls),
            "avg_latency_ms": statistics.mean(pipeline_latencies) * 1000,
            "p95_latency_ms": percentile(pipeline_latencies, 95) * 1000,
            "zero_result_ratio": pipeline_zero_count / len(test_queries),
        }

    output_path = Path(__file__).parent / "retrieval_eval_report.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"\n评估报告已保存: {output_path}")


if __name__ == "__main__":
    main()
