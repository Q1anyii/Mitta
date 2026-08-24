"""
Mitta 检索链路离线评估脚本
============================
评估指标：
  - Top5 召回率（recall@5）
  - 检索 P95 / P99 延迟
  - 0 结果占比
  - 单路向量召回 vs 混合检索（改写+稠密多路+BM25+RRF+重排+过滤）对比
  - 流水线各阶段耗时分解（改写/稠密检索/BM25/重排）

用法：
    conda activate langchain1.2
    cd src
    python -m ragas_test.eval_retrieval                  # 默认 50 条 query
    python -m ragas_test.eval_retrieval --limit 20       # 指定 query 数量
    python -m ragas_test.eval_retrieval --no-pipeline    # 只测单路召回
    python -m ragas_test.eval_retrieval --n-results 20   # 稠密召回数量

测试集：
    resources/knowledge-base/test-qa/eval_dataset.json
    每条含 question / ground_truth / category / source_file
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from constant.retrieval_constants import RRF_K
from constant.cache_constant import SPARSE_INDEX_NAME, DOC_PREFIX
from vector.vector_store import create_vector_store
from vector.retrieve_doc import RetrievedDoc
from init import embed_model, online_rerank, model
from service.cache_service import cache_service


# ============================================================
# 测试集加载
# ============================================================

DATASET_PATH = Path(__file__).parent.parent.parent / "resources" / "knowledge-base" / "test-qa" / "eval_dataset.json"


def load_test_queries(limit: int = 50, category: str = None) -> List[Dict]:
    """从 eval_dataset.json 加载测试 QA。"""
    if not DATASET_PATH.exists():
        logger.error(f"测试集不存在: {DATASET_PATH}")
        sys.exit(1)

    with open(DATASET_PATH, encoding="utf-8") as f:
        data = json.load(f)

    if category:
        data = [d for d in data if category in d.get("category", "")]

    logger.info(f"加载测试集: {len(data)} 条（limit={limit}, category={category or '全部'}）")
    return data[:limit]


# ============================================================
# 通用工具
# ============================================================

def bm25_search(query: str, top_k: int = 20) -> List[RetrievedDoc]:
    """BM25 稀疏检索（RedisSearch），返回 RetrievedDoc 列表。"""
    import jieba
    # RedisSearch 查询语法中 : ( ) - @ * 等是特殊字符，直接传中文问句会 Syntax error。
    # 用 jieba 分词后空格拼接，自然去除标点和特殊字符，同时提升中文 BM25 匹配效果。
    tokens = [t.strip() for t in jieba.lcut(query) if t.strip() and len(t.strip()) > 1]
    safe_query = " ".join(tokens) if tokens else query

    try:
        result = cache_service.redis.execute_command(
            "FT.SEARCH", SPARSE_INDEX_NAME,
            safe_query,
            "NOCONTENT", "WITHSCORES",
            "LIMIT", "0", str(top_k),
        )
    except Exception as e:
        logger.warning(f"BM25 检索失败，降级返回空: {e}")
        return []

    # 健壮性校验：result 必须是非空列表，第一个元素是总数
    if not isinstance(result, (list, tuple)) or len(result) < 2:
        return []

    docs = []
    for i in range(1, len(result) - 1, 2):
        try:
            raw_id = result[i]
            if isinstance(raw_id, bytes):
                raw_id = raw_id.decode("utf-8")
            doc_id = raw_id.replace(DOC_PREFIX, "")
            score = float(result[i + 1])
            content = cache_service.redis.hget(raw_id, "content")
            text = content.decode("utf-8") if isinstance(content, bytes) else (content or "")
            docs.append(RetrievedDoc(
                id=doc_id,
                text=text,
                distance=0.0,
                metadata={"source": "bm25", "bm25_score": score},
            ))
        except Exception as e:
            logger.warning(f"BM25 结果解析跳过 result[{i}]: {e}")
            continue
    return docs


def rrf_fusion(results: List[List[RetrievedDoc]], k: int = RRF_K) -> List[RetrievedDoc]:
    """RRF 融合：多路结果按排名倒数求和去重。"""
    scores = {}
    for docs in results:
        for rank, doc in enumerate(docs):
            key = doc.id or doc.text
            if key not in scores:
                scores[key] = {"doc": doc, "score": 0.0}
            scores[key]["score"] += 1.0 / (k + rank + 1)
    return [item["doc"] for item in sorted(scores.values(), key=lambda x: x["score"], reverse=True)]


def dedup_by_text(docs: List[RetrievedDoc]) -> List[RetrievedDoc]:
    """按文档正文去重，保留排名靠前的。"""
    seen = set()
    result = []
    for d in docs:
        key = d.text if d.text else d.id
        if key and key not in seen:
            seen.add(key)
            result.append(d)
    return result


def rewrite_query(query: str) -> List[str]:
    """LLM 查询改写，返回 [主查询, 子查询...]。"""
    try:
        from constant.retrieval_constants import REWRITE_PROMPT
        prompt = REWRITE_PROMPT.format(question=query, history="无")
        resp = model.invoke(prompt, response_format={"type": "json_object"})
        raw = json.loads(resp.content)
        queries = [raw.get("主查询", query)] + raw.get("子查询", [])
        queries = [q for q in queries if q and len(q) > 2][:4]
        return queries if queries else [query]
    except Exception as e:
        logger.warning(f"Query 改写失败，使用原始 query: {e}")
        return [query]


def percentile(data: List[float], p: float) -> float:
    """计算百分位数。"""
    if not data:
        return 0.0
    s = sorted(data)
    k = (len(s) - 1) * p / 100
    f = int(k)
    c = f + 1 if f + 1 < len(s) else f
    return s[f] + (s[c] - s[f]) * (k - f)


def evaluate_recall(retrieved_docs: List[RetrievedDoc], ground_truth: str, k: int = 5) -> float:
    """Top-K 召回率：检索结果文本覆盖 ground_truth 关键词的比例。"""
    if not retrieved_docs or not ground_truth:
        return 0.0

    # 从 ground_truth 提取关键词：中文按 2-gram，英文按单词，过滤常见停用词
    stop_words = {"的", "了", "是", "在", "和", "与", "或", "等", "也", "都", "就", "要", "会", "能", "可以", "这", "那", "有", "无", "不", "没", "为", "从", "到", "对", "中", "上", "下", "用", "做", "使", "让", "把", "被", "给", "向", "按", "因", "所", "以", "之", "其", "此", "该", "每", "各", "某", "一", "二", "三", "the", "a", "an", "is", "are", "of", "to", "in", "on", "for", "and", "or", "with", "by", "as", "at", "be", "this", "that", "it", "its"}

    # 提取有意义的关键词：长度>=2 且非停用词
    import re
    words = re.findall(r'[\u4e00-\u9fa5]{2,}|[a-zA-Z][a-zA-Z0-9_]+', ground_truth)
    keywords = list(set(w for w in words if w.lower() not in stop_words and len(w) >= 2))

    if not keywords:
        return 0.0

    top_k_text = " ".join(doc.text for doc in retrieved_docs[:k])
    hit = sum(1 for kw in keywords if kw in top_k_text)
    return hit / len(keywords)


# ============================================================
# 单路召回（baseline）
# ============================================================

def single_path_retrieve(vector_store, query: str, n_results: int) -> Tuple[List[RetrievedDoc], float]:
    """单路召回：原始 query 直接向量检索，不改写、不重排、不过滤。"""
    t0 = time.perf_counter()
    results = vector_store.query([query], n_results=n_results)
    elapsed = time.perf_counter() - t0
    return results[0] if results else [], elapsed


# ============================================================
# 混合检索流水线（当前项目 retrieve_graph 的离线版）
# ============================================================

def hybrid_retrieve(vector_store, query: str, n_results: int, filter_threshold: float = 0.15) -> Tuple[List[RetrievedDoc], float, Dict]:
    """混合检索：改写 → 稠密多路 → BM25 → RRF → 去重 → 重排 → 过滤。"""
    stats = {"rewrite_time": 0, "dense_time": 0, "bm25_time": 0, "rerank_time": 0, "num_queries": 1, "num_candidates": 0}
    t_total = time.perf_counter()

    # Step 1: Query 改写
    t0 = time.perf_counter()
    queries = rewrite_query(query)
    stats["rewrite_time"] = time.perf_counter() - t0
    stats["num_queries"] = len(queries)

    # Step 2: 稠密向量多路检索（不过滤距离，bge-m3 相关文档距离偏高）
    t0 = time.perf_counter()
    dense_results = vector_store.query(queries, n_results=n_results)
    stats["dense_time"] = time.perf_counter() - t0

    # Step 3: BM25 稀疏检索
    t0 = time.perf_counter()
    bm25_docs = bm25_search(query, top_k=n_results)
    stats["bm25_time"] = time.perf_counter() - t0

    # Step 4: RRF 融合（稠密多路 + BM25 一路）
    rank_lists = dense_results + [bm25_docs]
    merged = rrf_fusion(rank_lists)
    merged = dedup_by_text(merged)
    stats["num_candidates"] = len(merged)

    # Step 5: 重排
    t0 = time.perf_counter()
    if merged:
        try:
            rerank_results = online_rerank(queries[0], [d.text for d in merged], top_n=5)
            final_docs = []
            for r in rerank_results:
                doc = merged[r["index"]]
                doc.metadata["relevance_score"] = r["relevance_score"]
                final_docs.append(doc)
        except Exception as e:
            logger.warning(f"重排失败，使用 RRF 结果: {e}")
            final_docs = merged[:5]
    else:
        final_docs = []
    stats["rerank_time"] = time.perf_counter() - t0

    # Step 6: 过滤（relevance_score >= threshold），空则兜底 top3
    filtered = [d for d in final_docs if d.metadata.get("relevance_score", 0) >= filter_threshold]
    if not filtered and final_docs:
        filtered = final_docs[:3]

    total_elapsed = time.perf_counter() - t_total
    return filtered, total_elapsed, stats


# ============================================================
# 主流程
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Mitta 检索链路离线评估")
    parser.add_argument("--limit", type=int, default=50, help="测试 query 数量（默认 50）")
    parser.add_argument("--n-results", type=int, default=20, help="稠密召回数量（默认 20）")
    parser.add_argument("--filter-threshold", type=float, default=0.15, help="重排分数过滤阈值（默认 0.15）")
    parser.add_argument("--category", type=str, default=None, help="按 category 过滤测试集")
    parser.add_argument("--no-pipeline", action="store_true", help="只测单路召回")
    args = parser.parse_args()

    # 加载测试集
    test_queries = load_test_queries(args.limit, args.category)
    if not test_queries:
        logger.error("无测试 query，退出")
        return

    # 初始化向量库 + RedisSearch（BM25）
    logger.info("初始化向量库...")
    vector_store = create_vector_store(load_vector_db_config())
    logger.info(f"向量库就绪，collection 文档数: {vector_store.count()}")

    # 初始化 cache_service（创建 BM25 稀疏索引，已存在则跳过）
    try:
        cache_service.open()
        logger.info("RedisSearch BM25 索引就绪")
    except Exception as e:
        logger.warning(f"cache_service 初始化失败，BM25 路将降级为空: {e}")

    # ============================================================
    # 单路召回评估
    # ============================================================
    logger.info("=" * 60)
    logger.info(f"【单路召回评估】n_results={args.n_results}（原始 query 直接向量检索）")
    single_recalls = []
    single_latencies = []
    single_zero = 0

    for i, item in enumerate(test_queries):
        query = item["question"]
        docs, elapsed = single_path_retrieve(vector_store, query, args.n_results)
        recall = evaluate_recall(docs, item["ground_truth"], k=5)
        single_recalls.append(recall)
        single_latencies.append(elapsed)
        if not docs:
            single_zero += 1
        if (i + 1) % 10 == 0:
            logger.info(f"  [{i+1}/{len(test_queries)}] recall@5={recall:.2f} latency={elapsed*1000:.0f}ms docs={len(docs)}")

    # ============================================================
    # 混合检索流水线评估
    # ============================================================
    pipeline_recalls = []
    pipeline_latencies = []
    pipeline_zero = 0
    pipeline_stats_all = []

    if not args.no_pipeline:
        logger.info("=" * 60)
        logger.info(f"【混合检索评估】改写+稠密多路+BM25+RRF+重排+过滤(>={args.filter_threshold})")
        for i, item in enumerate(test_queries):
            query = item["question"]
            docs, elapsed, stats = hybrid_retrieve(vector_store, query, args.n_results, args.filter_threshold)
            recall = evaluate_recall(docs, item["ground_truth"], k=5)
            pipeline_recalls.append(recall)
            pipeline_latencies.append(elapsed)
            pipeline_stats_all.append(stats)
            if not docs:
                pipeline_zero += 1
            if (i + 1) % 10 == 0:
                logger.info(f"  [{i+1}/{len(test_queries)}] recall@5={recall:.2f} latency={elapsed*1000:.0f}ms docs={len(docs)} candidates={stats['num_candidates']}")

    # ============================================================
    # 汇总报告
    # ============================================================
    logger.info("=" * 60)
    logger.info("【评估汇总报告】")
    logger.info(f"测试 query 数: {len(test_queries)}")
    logger.info(f"稠密召回 n_results: {args.n_results}")
    logger.info(f"过滤阈值: {args.filter_threshold}")
    logger.info("")

    def fmt(val, suffix=""):
        return f"{val:.4f}{suffix}" if isinstance(val, float) else str(val)

    def ms(data):
        return f"{statistics.mean(data)*1000:.1f}" if data else "N/A"

    def p95(data):
        return f"{percentile(data, 95)*1000:.1f}" if data else "N/A"

    def p99(data):
        return f"{percentile(data, 99)*1000:.1f}" if data else "N/A"

    logger.info("┌────────────────────┬──────────────┬──────────────┐")
    logger.info("│ 指标               │ 单路向量召回 │ 混合检索     │")
    logger.info("├────────────────────┼──────────────┼──────────────┤")
    logger.info(f"│ 平均 recall@5      │ {statistics.mean(single_recalls):.4f}       │ {statistics.mean(pipeline_recalls):.4f}       │" if pipeline_recalls else f"│ 平均 recall@5      │ {statistics.mean(single_recalls):.4f}       │ N/A          │")
    logger.info(f"│ 中位数 recall@5    │ {statistics.median(single_recalls):.4f}       │ {statistics.median(pipeline_recalls):.4f}       │" if pipeline_recalls else f"│ 中位数 recall@5    │ {statistics.median(single_recalls):.4f}       │ N/A          │")
    logger.info(f"│ 平均延迟(ms)       │ {ms(single_latencies)}        │ {ms(pipeline_latencies)}        │")
    logger.info(f"│ P95 延迟(ms)       │ {p95(single_latencies)}        │ {p95(pipeline_latencies)}        │")
    logger.info(f"│ P99 延迟(ms)       │ {p99(single_latencies)}        │ {p99(pipeline_latencies)}        │")
    logger.info(f"│ 0 结果占比          │ {single_zero/len(test_queries)*100:.1f}%        │ {pipeline_zero/len(test_queries)*100:.1f}%        │" if pipeline_recalls else f"│ 0 结果占比          │ {single_zero/len(test_queries)*100:.1f}%        │ N/A          │")
    logger.info("└────────────────────┴──────────────┴──────────────┘")

    # 流水线各阶段耗时分解
    if pipeline_stats_all:
        avg = lambda key: statistics.mean(s[key] for s in pipeline_stats_all) * 1000
        logger.info("")
        logger.info("【混合检索各阶段耗时分解】")
        logger.info(f"  Query 改写:  {avg('rewrite_time'):.1f}ms (平均 {statistics.mean(s['num_queries'] for s in pipeline_stats_all):.1f} 路)")
        logger.info(f"  稠密向量检索: {avg('dense_time'):.1f}ms")
        logger.info(f"  BM25 稀疏检索: {avg('bm25_time'):.1f}ms")
        logger.info(f"  重排精排:     {avg('rerank_time'):.1f}ms")
        logger.info(f"  平均候选数:   {statistics.mean(s['num_candidates'] for s in pipeline_stats_all):.1f}")

    # 保存 JSON 报告
    report = {
        "config": {
            "limit": args.limit,
            "n_results": args.n_results,
            "filter_threshold": args.filter_threshold,
            "category": args.category,
        },
        "single_path": {
            "avg_recall": round(statistics.mean(single_recalls), 4),
            "median_recall": round(statistics.median(single_recalls), 4),
            "avg_latency_ms": round(statistics.mean(single_latencies) * 1000, 1),
            "p95_latency_ms": round(percentile(single_latencies, 95) * 1000, 1),
            "p99_latency_ms": round(percentile(single_latencies, 99) * 1000, 1),
            "zero_result_ratio": round(single_zero / len(test_queries), 4),
        },
    }
    if pipeline_recalls:
        report["hybrid_pipeline"] = {
            "avg_recall": round(statistics.mean(pipeline_recalls), 4),
            "median_recall": round(statistics.median(pipeline_recalls), 4),
            "avg_latency_ms": round(statistics.mean(pipeline_latencies) * 1000, 1),
            "p95_latency_ms": round(percentile(pipeline_latencies, 95) * 1000, 1),
            "p99_latency_ms": round(percentile(pipeline_latencies, 99) * 1000, 1),
            "zero_result_ratio": round(pipeline_zero / len(test_queries), 4),
            "stage_breakdown_ms": {
                "rewrite": round(statistics.mean(s["rewrite_time"] for s in pipeline_stats_all) * 1000, 1),
                "dense_retrieve": round(statistics.mean(s["dense_time"] for s in pipeline_stats_all) * 1000, 1),
                "bm25": round(statistics.mean(s["bm25_time"] for s in pipeline_stats_all) * 1000, 1),
                "rerank": round(statistics.mean(s["rerank_time"] for s in pipeline_stats_all) * 1000, 1),
            },
        }

    output_path = Path(__file__).parent / "retrieval_eval_report.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"\n评估报告已保存: {output_path}")


if __name__ == "__main__":
    main()
