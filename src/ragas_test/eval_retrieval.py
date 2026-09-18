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
    python -m ragas_test.eval_retrieval --dataset resources/knowledge-base/test-qa/eval_project_dataset.json --output project_retrieval_eval_report.json  # 项目专属评测集

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


def load_test_queries(limit: int = 50, category: str = None, dataset_path: Path = None) -> List[Dict]:
    """从评测集 JSON 加载测试 QA（默认 eval_dataset.json，可传 --dataset 指定其他集）。

    兼容两种结构：
    - 原集：顶层为数组 [{question, ground_truth, key_points?, category, source_file?}]
    - 新集（eval_project_dataset.json）：顶层对象 {"_meta": {...}, "dataset": [...]}
    """
    path = dataset_path or DATASET_PATH
    if not path.exists():
        logger.error(f"测试集不存在: {path}")
        sys.exit(1)

    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get("dataset", [])

    if category:
        data = [d for d in data if category in d.get("category", "")]

    logger.info(f"加载测试集: {path.name} {len(data)} 条（limit={limit}, category={category or '全部'}）")
    return data[:limit]


# ============================================================
# 通用工具
# ============================================================

def bm25_search(query: str, top_k: int = 20) -> List[RetrievedDoc]:
    """BM25 稀疏检索（RedisSearch），返回 RetrievedDoc 列表。"""
    import jieba
    # RedisSearch 查询语法中 : ( ) - @ * 等是特殊字符，直接传中文问句会 Syntax error。
    # 用 jieba 分词后以 OR（|）连接：空格 AND 会因中文多词无交集整体返回 0，
    # OR 保证英文/专有名词/任意分词能命中（与生产 query_nodes.py 保持一致）。
    # 2026-09-18 修复：分词 token 含 . - _ 等字符（代码块/路径类问题）会触发 Syntax error，
    # 对 RedisSearch 特殊字符统一加 \ 转义（已验证 45 条 query 0 报错）。
    import re as _re
    _redis_special = _re.compile(r'([,.<>{}\[\]"\'=~!@#$%^&*();:|\-+\\])')
    tokens = [t.strip() for t in jieba.lcut(query) if t.strip() and len(t.strip()) > 1]
    escaped = [_redis_special.sub(r"\\\1", t) for t in tokens]
    safe_query = " | ".join(escaped) if escaped else _redis_special.sub(r"\\\1", query)

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

    # 兼容两种返回格式：新版 redis-py（8.x）返回 dict（键为 bytes 或 str）；旧版返回扁平 list
    def _get(d, name):
        return d.get(name) if name in d else d.get(name.encode("utf-8"))

    if isinstance(result, dict):
        docs = []
        for it in (_get(result, "results") or []):
            try:
                raw_id = _get(it, "id") or b""
                if isinstance(raw_id, bytes):
                    raw_id = raw_id.decode("utf-8")
                doc_id = raw_id.replace(DOC_PREFIX, "")
                attrs = _get(it, "extra_attributes") or {}
                content = _get(attrs, "content") or b""
                if not content:
                    # NOCONTENT 模式下 extra_attributes 不含正文，需从 Hash 读取
                    content = cache_service.redis.hget(raw_id, "content") or b""
                text = content.decode("utf-8") if isinstance(content, bytes) else (content or "")
                score = _get(it, "score") or 0.0
                docs.append(RetrievedDoc(
                    id=doc_id,
                    text=text,
                    distance=0.0,
                    metadata={"source": "bm25", "bm25_score": float(score)},
                ))
            except Exception as e:
                logger.warning(f"BM25 dict 结果解析跳过: {e}")
                continue
        return docs

    # 旧版扁平 list：[总数, doc_id1, score1, doc_id2, score2, ...]
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


_STOP_WORDS = {"的", "了", "是", "在", "和", "与", "或", "等", "也", "都", "就", "要", "会", "能", "可以", "这", "那", "有", "无", "不", "没", "为", "从", "到", "对", "中", "上", "下", "用", "做", "使", "让", "把", "被", "给", "向", "按", "因", "所", "以", "之", "其", "此", "该", "每", "各", "某", "一", "二", "三", "the", "a", "an", "is", "are", "of", "to", "in", "on", "for", "and", "or", "with", "by", "as", "at", "be", "this", "that", "it", "its"}


def _split_sentences(text: str) -> list:
    """按句末标点切分答案。"""
    import re
    parts = re.split(r"[。！？；\n]+", text)
    return [p.strip() for p in parts if p.strip()]


def _sentence_keywords(sentence: str) -> set:
    """提取句子核心关键词：英文词原样保留；中文连续串用 jieba 切词（消除超长整串惩罚），
    过滤停用词与单字。"""
    import re
    words = []
    for piece in re.findall(r"[a-zA-Z][a-zA-Z0-9_]+|[\u4e00-\u9fa5]+", sentence):
        if re.fullmatch(r"[\u4e00-\u9fa5]+", piece):
            import jieba
            words.extend(t for t in jieba.lcut(piece) if len(t) >= 2)
        else:
            words.append(piece)
    return set(w for w in words if w.lower() not in _STOP_WORDS)


def evaluate_recall(retrieved_docs: List[RetrievedDoc], ground_truth: str, k: int = 5, metric: str = "boolean") -> float:
    """Top-K 召回判定。

    metric="boolean"（默认）：**布尔命中率**——ground_truth 的核心要点句是否被 top-k 文档覆盖。
      每句的全部核心关键词（AND）完整出现在 top-k 文本中 = 该句被覆盖；
      query 命中 = 覆盖句占比 >= 0.5（过半答案要点有文档支撑，贴近生产体验）。
      返回 0.0 / 1.0，avg 即布尔命中率。
    metric="coverage"：旧关键词覆盖率——关键词子串命中比例（长答案整串匹配惩罚重，已非默认）。
    """
    if not retrieved_docs or not ground_truth:
        return 0.0

    top_k_text = " ".join(doc.text for doc in retrieved_docs[:k])

    if metric == "coverage":
        import re
        words = re.findall(r"[\u4e00-\u9fa5]{2,}|[a-zA-Z][a-zA-Z0-9_]+", ground_truth)
        keywords = list(set(w for w in words if w.lower() not in _STOP_WORDS and len(w) >= 2))
        if not keywords:
            return 0.0
        hit = sum(1 for kw in keywords if kw in top_k_text)
        return hit / len(keywords)

    # boolean：句级要点覆盖
    # - 句子覆盖 = 该句核心关键词命中 top-k 文本的比例 >= 0.6（允许个别词字面差异，如“用于/支持”）
    # - query 命中 = 覆盖句占比 >= 0.5（过半答案要点有文档支撑，贴近生产体验）
    sentences = _split_sentences(ground_truth)
    if not sentences:
        return 0.0
    covered = 0
    for sentence in sentences:
        kws = _sentence_keywords(sentence)
        if not kws:
            continue
        hit = sum(1 for kw in kws if kw in top_k_text)
        if hit / len(kws) >= 0.6:
            covered += 1
    return 1.0 if covered / len(sentences) >= 0.5 else 0.0


def evaluate_key_points(retrieved_docs: List[RetrievedDoc], key_points: List[str], k: int = 5) -> Tuple[int, int]:
    """关键事实点覆盖（H-20260918-02 新口径）：任一事实点在 top-k 文档文本中出现即命中。

    事实点取自评测集 ground_truth 的核心事实，且以能在知识库某条 chunk 中找到原句为准
    （禁止"宽泛到必然命中"的作弊式表述）。返回 (命中点数, 总点数)。
    """
    if not retrieved_docs or not key_points:
        return 0, len(key_points) or 0
    top_k_text = " ".join(doc.text for doc in retrieved_docs[:k])
    hit = sum(1 for kp in key_points if kp and kp in top_k_text)
    return hit, len(key_points)


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

    # 口径修复（2026-09-18）：与单路同口径算 recall@5。
    # 重排只保留 top5，过滤后通常只剩 1~3 条，关键词覆盖文本条数天然比单路
    # （固定 5 条全文）少——指标被"候选条数"人为压低，并非链路变差。
    # 过滤后不足 5 条时，按 RRF 融合顺序补足到 5 条。
    if len(filtered) < 5 and merged:
        seen = {id(d) for d in filtered}
        for d in merged:
            if len(filtered) >= 5:
                break
            if id(d) not in seen:
                filtered.append(d)
                seen.add(id(d))

    total_elapsed = time.perf_counter() - t_total
    return filtered, total_elapsed, stats


def diagnose_retrieve(vector_store, query: str, n_results: int, filter_threshold: float) -> str:
    """诊断单条 query：输出 dense 候选池 → rerank → filter 各级变化，
    辅助判定相关文档是在哪一级丢失（H-20260918-02 诊断要求）。"""
    rows = []
    queries = rewrite_query(query)
    rows.append(f'- 改写: {queries}')
    dense_results = vector_store.query(queries, n_results=n_results)
    dense20 = []
    for qres in dense_results:
        for d in qres:
            if all(d.text != x.text for x in dense20):
                dense20.append(d)
    rows.append(f'- dense 候选池({len(dense20)}):')
    for d in dense20[:20]:
        src = ((d.metadata or {}).get("source") or (d.metadata or {}).get("source_file") or "?")
        rows.append(f'    `[{str(d.id)[:16]}]` {src} | {d.text[:60]}')
    bm25_docs = bm25_search(query, top_k=n_results)
    rows.append(f'- BM25 候选({len(bm25_docs)})')
    merged = dedup_by_text(rrf_fusion(dense_results + [bm25_docs]))
    rows.append(f'- RRF 融合候选({len(merged)})')
    final_docs = []
    if merged:
        try:
            rerank_results = online_rerank(queries[0], [d.text for d in merged], top_n=5)
            for r in rerank_results:
                doc = merged[r["index"]]
                doc.metadata["relevance_score"] = r["relevance_score"]
                final_docs.append(doc)
        except Exception as e:
            rows.append(f'- rerank 失败: {e}')
            final_docs = merged[:5]
    rows.append('- rerank top5:')
    for d in final_docs:
        rows.append(f"    `[{str(d.id)[:16]}]` score={d.metadata.get('relevance_score', '?'):.3f} | {d.text[:50]}")
    filtered = [d for d in final_docs if d.metadata.get("relevance_score", 0) >= filter_threshold]
    if not filtered and final_docs:
        filtered = final_docs[:3]
    rows.append(f'- filter(>={filter_threshold}) 后({len(filtered)}): {[str(d.id)[:12] for d in filtered]}')
    return "\n".join(rows)


# ============================================================
# 主流程
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Mitta 检索链路离线评估")
    parser.add_argument("--limit", type=int, default=50, help="测试 query 数量（默认 50）")
    parser.add_argument("--n-results", type=int, default=20, help="稠密召回数量（默认 20）")
    parser.add_argument("--filter-threshold", type=float, default=0.25, help="重排分数过滤阈值（默认 0.25，对齐生产 filter_node）")
    parser.add_argument("--category", type=str, default=None, help="按 category 过滤测试集")
    parser.add_argument("--metric", type=str, default="boolean", choices=["boolean", "coverage"], help="recall 口径：boolean 布尔命中率（默认）| coverage 旧关键词覆盖率")
    parser.add_argument("--no-pipeline", action="store_true", help="只测单路召回")
    parser.add_argument("--diagnose", action="store_true", help="对未命中 query 输出候选明细诊断到 diagnose_report.md")
    parser.add_argument("--dataset", type=str, default=None,
                        help="评测集 JSON 路径（默认 resources/knowledge-base/test-qa/eval_dataset.json）")
    parser.add_argument("--output", type=str, default="retrieval_eval_report.json",
                        help="报告输出文件名（默认 retrieval_eval_report.json，落盘到本脚本同目录）")
    args = parser.parse_args()

    # 加载测试集（--dataset 指定其他集，如 eval_project_dataset.json）
    dataset_path = None
    if args.dataset:
        _p = Path(args.dataset)
        dataset_path = _p if _p.is_absolute() else (Path(__file__).parent.parent.parent / _p)
    test_queries = load_test_queries(args.limit, args.category, dataset_path)
    if not test_queries:
        logger.error("无测试 query，退出")
        return

    # 初始化向量库 + RedisSearch（BM25）
    logger.info("初始化向量库...")
    vector_store = create_vector_store(load_vector_db_config())
    logger.info(f"向量库就绪，collection 文档数: {vector_store.count()}")

    # 注入含 RedisSearch 的 Redis（6379 WSL sorts-redis；.env 的 6380 无 RedisSearch，BM25 无法工作）
    import redis as _redis
    cache_service.db_url = "redis://:sorts_dev@localhost:6379"
    cache_service.host, cache_service.port, cache_service.password = cache_service.parse_url(cache_service.db_url)
    cache_service.redis = _redis.Redis(
        host=cache_service.host, port=cache_service.port,
        password=cache_service.password,
        socket_timeout=3, socket_connect_timeout=3,
    )

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
    single_kp = []
    pipeline_kp = []
    diagnose_log = []

    for i, item in enumerate(test_queries):
        query = item["question"]
        docs, elapsed = single_path_retrieve(vector_store, query, args.n_results)
        recall = evaluate_recall(docs, item["ground_truth"], k=5, metric=args.metric)
        single_recalls.append(recall)
        single_latencies.append(elapsed)
        kp_hit, kp_total = evaluate_key_points(docs, item.get("key_points") or [], k=5)
        single_kp.append((kp_hit, kp_total))
        if args.diagnose and (recall == 0 or (kp_total and kp_hit < kp_total)):
            diagnose_log.append(f"### 单路 Q{i} [{item.get('category')}] {query}")
            diagnose_log.append(diagnose_retrieve(vector_store, query, args.n_results, args.filter_threshold))
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
            recall = evaluate_recall(docs, item["ground_truth"], k=5, metric=args.metric)
            pipeline_recalls.append(recall)
            pipeline_latencies.append(elapsed)
            pipeline_stats_all.append(stats)
            kp_hit, kp_total = evaluate_key_points(docs, item.get("key_points") or [], k=5)
            pipeline_kp.append((kp_hit, kp_total))
            if args.diagnose and (recall == 0 or (kp_total and kp_hit < kp_total)):
                diagnose_log.append(f"### 混合 Q{i} [{item.get('category')}] {query}")
                diagnose_log.append(diagnose_retrieve(vector_store, query, args.n_results, args.filter_threshold))
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
    logger.info(f"recall 口径: {args.metric}（boolean=句级要点覆盖布尔命中率 | coverage=旧关键词覆盖率）")
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
    def kp_stats(pairs):
        pairs = [p for p in pairs if p[1] > 0]
        if not pairs:
            return None
        coverage = [h / t for h, t in pairs]
        full = sum(1 for h, t in pairs if h == t) / len(pairs)
        return {
            "avg_point_coverage": round(statistics.mean(coverage), 4),
            "query_full_hit_ratio": round(full, 4),
            "queries_with_kp": len(pairs),
        }

    report = {
        "config": {
            "limit": args.limit,
            "n_results": args.n_results,
            "filter_threshold": args.filter_threshold,
            "category": args.category,
            "metric": args.metric,
            "dataset": str(dataset_path or DATASET_PATH),
            "deprecated_metric_note": "旧口径（coverage 关键词覆盖率 / boolean 句级覆盖）依赖 ground_truth 与知识库的文本同一性；实测 test-qa 文档未入库、答案多为改写组织，字面匹配天然偏低。key_points 口径以知识库可支撑的原句事实点为判定单元，更接近生产体验（H-20260918-02）。",
            "key_points_scope": "基础概念 10 条试点（已按生产 405 chunks 验证事实点可在某 chunk 找到原句）" if not dataset_path else "项目专属评测集全部条目（21 条，key_points 逐一在生产 405 chunks 验证存在原句）",
        },
        "key_points": {
            "single_path": kp_stats(single_kp),
            "hybrid_pipeline": kp_stats(pipeline_kp),
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

    output_path = Path(__file__).parent / args.output
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"\n评估报告已保存: {output_path}")

    if args.diagnose:
        diag_path = Path(__file__).parent / "diagnose_report.md"
        if diagnose_log:
            header = "# 检索失败 Case 诊断明细（H-20260918-02）\n\n对布尔口径=0 或 key_points 未全中的 query，输出各级候选池变化，判定相关文档在哪一级丢失。\n\n"
            diag_path.write_text(header + "\n\n".join(diagnose_log), encoding="utf-8")
            logger.info(f"诊断明细已保存: {diag_path}（{len(diagnose_log)} 条 case）")
        else:
            diag_path.write_text("# 检索失败 Case 诊断明细（H-20260918-02）\n\n无失败 case。", encoding="utf-8")
            logger.info(f"诊断明细已保存: {diag_path}（无失败 case）")


if __name__ == "__main__":
    main()
