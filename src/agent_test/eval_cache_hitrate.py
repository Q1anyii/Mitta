"""
Mitta 检索语义缓存专项评测（E6-B）
==================================
回答两个此前没有可信数据的问题：

  1. **同义改写命中率**：措辞不同、语义相同的 query，检索缓存到底能不能命中？
     同时给出「命中但命中错条目」的比例（这才是缓存真正的风险）和误命中率。
  2. **有无缓存 embedding 调用率**：同一条 query 流，开缓存 vs 不开缓存，
     实际嵌入的文本条数差多少。

与既有脚本的分工（别再拿旧脚本的数字对外讲）：
  - `eval_semantic_cache.py`（E6）：只有 1 条原 query × 3 条同义改写，样本量不足以报
    百分比（命中率只能是 0/33/67/100）。本脚本扩到 12 组 × 3 改写，并补「同域不同问」硬负样本。
  - `eval_cache.py`：3 轮用的是**完全相同的 query 字符串**，测的是「原文重复」，不是
    「同义改写」；且它的 120/40 次是重复计数口径（`embed_query` → `embed_documents`
    各计一次，实测真实嵌入文本数为 60/20）。本脚本按**实际嵌入的文本条数**计数，
    并把对比基线换成真正的「无缓存」路径（不是「第一轮有缓存」）。

用法：
    conda activate langchain1.2
    cd src
    python -m agent_test.eval_cache_hitrate                    # 全量
    python -m agent_test.eval_cache_hitrate --bases 6          # 缩小规模冒烟
    python -m agent_test.eval_cache_hitrate --skip-rate        # 只跑命中率
    python -m agent_test.eval_cache_hitrate --tag run2         # 输出文件名加后缀，保留多版本

说明：
  - 白盒评测：真实 Redis（需 RedisSearch）+ 真实 embed_model + 真实 online_rerank。
  - 每个测试用独立 thread_id 前缀，结束后清理，不污染生产数据。
  - 生产 .env 的 REDIS_DB_URL 指向远端且缺密码（连不上），这里默认连本地 redis-stack，
    可用 --redis-url 覆盖。
"""
import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple
from unittest.mock import patch

from langchain_core.documents import Document
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

# 脚本从 src/ 以 -m 方式运行时，cwd 可能不含 .env，显式按文件位置加载
load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=True)

from service.cache_service import CacheService
from constant.cache_constant import CACHE_RERANK_HIT_SCORE
from init import embed_model, online_rerank, model
from graphs.nodes.retrieve.query_nodes import rewrite_query
from agent_test.report_path import resolve_report_path

# 语义缓存评测默认使用本地 redis-stack（RedisSearch）；
# 生产 .env 的 REDIS_DB_URL 由 load_dotenv(override=True) 强制注入且不可用，
# 因此显式构造 CacheService 实例并传入 URL。
DEFAULT_REDIS_URL = "redis://:sorts_dev@localhost:6379"


# ============================================================
# 一、Embedding 调用计数（按「实际嵌入的文本条数」计）
# ============================================================

class EmbeddingTextCounter:
    """统计实际送进 embedding 的文本条数。

    只打 `embed_documents`：实测 `OpenAIEmbeddings.embed_query(text)` 会转调
    `embed_documents([text])`，在两层都打补丁会把一次调用算成两次（旧 eval_cache.py
    的 120/40 就是这么来的）。这里按 len(texts) 累加，得到真实的文本条数。
    """

    def __init__(self):
        self.texts = 0
        self.calls = 0
        self._patcher = None

    def start(self):
        self.texts = 0
        self.calls = 0
        counter = self
        original = embed_model.__class__.embed_documents

        def patched(self_, texts):
            counter.calls += 1
            counter.texts += len(texts)
            return original(self_, texts)

        self._patcher = patch.object(embed_model.__class__, "embed_documents", patched)
        self._patcher.start()

    def stop(self):
        if self._patcher:
            try:
                self._patcher.stop()
            except Exception:
                pass
            self._patcher = None

    def reset(self):
        self.texts = 0
        self.calls = 0


def selfcheck_counter() -> bool:
    """自检：一次 embed_query 只应产生 1 条文本的计数（防止计数口径漂移）。"""
    counter = EmbeddingTextCounter()
    counter.start()
    try:
        embed_model.embed_query("自检：计数口径")
    finally:
        counter.stop()
    ok = counter.texts == 1
    logger.info(f"计数口径自检：embed_query 一次 → 计入 {counter.texts} 条文本（期望 1）{'✓' if ok else '✗'}")
    return ok


# ============================================================
# 二、测试语料：12 组「原 query + 3 条同义改写」 + 两类负样本
# ============================================================
# 改写规则：措辞/语序/中英混排明显不同，语义等价（不增删信息点）。

BASE_QUERIES: List[Dict] = [
    {
        "id": "B01",
        "base": "LangGraph 的 Checkpointer 有什么作用？",
        "paraphrases": [
            "Checkpointer 在 LangGraph 中是用来干什么的？",
            "langgraph 里 checkpoint 的用途是什么",
            "LangGraph 的 Checkpointer 主要负责哪件事？",
        ],
    },
    {
        "id": "B02",
        "base": "MCP 工具如何接入 LangGraph？",
        "paraphrases": [
            "怎么把 MCP 工具连到 LangGraph 里面去？",
            "LangGraph 集成 MCP 工具的方式是什么？",
            "MCP server 要怎样才能被 LangGraph 调起来？",
        ],
    },
    {
        "id": "B03",
        "base": "RAG 的检索流程包含哪些步骤？",
        "paraphrases": [
            "RAG 检索是分哪几步做的？",
            "讲一下 RAG 检索链路的具体流程",
            "RAG 从提问到拿到文档要经过哪些环节？",
        ],
    },
    {
        "id": "B04",
        "base": "PostgresSaver 和 PostgresStore 有什么区别？",
        "paraphrases": [
            "PostgresSaver 与 PostgresStore 的差异在哪里？",
            "PostgresStore 和 PostgresSaver 该怎么区分？",
            "postgressaver vs postgresstore 区别",
        ],
    },
    {
        "id": "B05",
        "base": "RedisSearch 是怎么实现语义缓存匹配的？",
        "paraphrases": [
            "Redis 的语义缓存用 RedisSearch 怎么做向量匹配？",
            "基于 RedisSearch 的语义缓存匹配机制是什么？",
            "redis search 语义缓存怎么做的",
        ],
    },
    {
        "id": "B06",
        "base": "SSE 流式输出是怎么实现的？",
        "paraphrases": [
            "服务端 SSE 流式推送是怎么做的？",
            "要如何实现 SSE 方式的流式返回？",
            "sse 流式输出实现方式",
        ],
    },
    {
        "id": "B07",
        "base": "JWT 双 Token 机制如何实现续签？",
        "paraphrases": [
            "双 token 方案里 refresh token 是怎么续期的？",
            "JWT 无感刷新（双令牌）的实现方式是什么？",
            "access token 过期后怎么用 refresh token 换新的？",
        ],
    },
    {
        "id": "B08",
        "base": "bge-m3 embedding 的向量维度是多少？",
        "paraphrases": [
            "bge-m3 生成的 embedding 有多少维？",
            "BAAI/bge-m3 输出向量的维度是几？",
            "bge-m3 向量维度",
        ],
    },
    {
        "id": "B09",
        "base": "RRF 融合的公式是什么？",
        "paraphrases": [
            "倒数排名融合 RRF 的计算公式怎么写？",
            "RRF 是怎么把多路召回结果合并打分的？",
            "rrf fusion 公式",
        ],
    },
    {
        "id": "B10",
        "base": "工具筛选的两层机制分别是什么？",
        "paraphrases": [
            "工具选择为什么分两层，各层做什么？",
            "LLM 工具筛选的两级过滤机制是怎样的？",
            "工具两层筛选机制介绍",
        ],
    },
    {
        "id": "B11",
        "base": "记忆节点的 idle 和 executed 状态有什么区别？",
        "paraphrases": [
            "memory 节点里 idle 状态和 executed 状态怎么区分？",
            "记忆节点的 idle/executed 两种状态分别是什么意思？",
            "langgraph 记忆节点 idle executed 区别",
        ],
    },
    {
        "id": "B12",
        "base": "Nginx 反向代理 SSE 需要哪些配置？",
        "paraphrases": [
            "用 Nginx 反代 SSE 要怎么配？",
            "Nginx 转发 SSE 流式响应需要改哪些参数？",
            "nginx sse 反向代理配置要点",
        ],
    },
]

# 硬负样本：同属技术域，但与 12 组原 query 都**不等价**——语义缓存最容易在这类
# 「看起来相关、其实问的是另一件事」的 query 上误命中。
HARD_NEGATIVES: List[str] = [
    "Milvus 和 ChromaDB 之间如何切换？",
    "用户头像为什么用 MEDIUMTEXT 存储？",
    "距离阈值 0.3 为什么会过滤掉所有结果？",
    "文件上传后立即解析的设计考量是什么？",
    "多轮对话的上下文窗口如何管理？",
    "分布式锁用 Redisson 怎么实现？",
    "FastAPI 同步路由和异步路由的区别是什么？",
    "向量库的 Protocol 抽象是怎么实现的？",
]

# 跨域负样本：与知识库完全无关，理论误命中率应为 0。
UNRELATED_QUERIES: List[str] = [
    "今天中午吃什么比较好？",
    "帮我写一首描写秋天的诗",
    "上海下周天气怎么样？",
    "红烧排骨怎么做好吃？",
    "去成都旅游三天怎么安排行程？",
    "猫为什么喜欢晒太阳？",
]


def make_docs(tag: str) -> List[Document]:
    """构造带 tag 的测试文档，用于判定「命中的是不是正确条目」。"""
    return [
        Document(
            page_content=f"[{tag}] 该条目讲解：{tag} 的核心概念、实现原理与工程实践，"
                         f"包含技术选型依据、关键参数取值、常见坑与线上验证方式。",
            metadata={"source": tag, "mock": True},
        )
    ]


def lat_stats(latencies: List[float]) -> Dict:
    """延迟统计。均值单独给会被个别网络抖动（实测出现 3~9s 尖刺）拉偏，必须同时看中位数。"""
    if not latencies:
        return {"avg_latency_ms": 0, "p50_latency_ms": 0, "p95_latency_ms": 0, "max_latency_ms": 0}
    s = sorted(latencies)
    return {
        "avg_latency_ms": round(statistics.mean(s), 2),
        "p50_latency_ms": round(statistics.median(s), 2),
        "p95_latency_ms": round(s[max(0, int(len(s) * 0.95) - 1)], 2),
        "max_latency_ms": round(s[-1], 2),
    }


def hit_tag(docs) -> str | None:
    """从命中的文档里取回 tag；取不到说明缓存内容异常。"""
    if not docs:
        return None
    meta = getattr(docs[0], "metadata", {}) or {}
    return meta.get("source")


# ============================================================
# 三、检索阶段「嵌入多少条文本」实测
# ============================================================

def probe_rewrite_width(sample_queries: List[str]) -> Dict:
    """实测检索链路每条 query 会嵌入多少条文本。

    真实链路：`rewrite` 产出 rewritten_queries（1 主查询 + 1~2 子查询），
    `dense_query` 嵌入 `[原始问题] + rewritten_queries`（`query_nodes.py:65`），
    BM25 走 RedisSearch 全文索引不消耗 embedding。
    所以每条 query 的检索嵌入量 = 1 + 1 + |子查询|。

    Args:
        sample_queries: 抽样的 query（含原句与改写句，覆盖两种形态）

    Returns:
        {"n": 样本数, "mean": 均值, "min":, "max":, "details": [...]}
    """
    logger.info("【测试 0】实测检索阶段每条 query 的 embedding 文本数（Query 改写宽度）")
    widths = []
    details = []
    for q in sample_queries:
        try:
            out = rewrite_query({"question": q, "history": []}, model)
            rewritten = out.get("rewritten_queries", []) or []
            # dense_query: all_queries = [origin] + rewritten_queries
            width = 1 + len(rewritten)
        except Exception as e:
            logger.warning(f"  Query 改写失败，按兜底 1 计：{e}")
            rewritten = []
            width = 1
        widths.append(width)
        details.append({"query": q, "rewritten": rewritten, "embedded_texts": width})
        logger.info(f"  嵌入 {width} 条 ← {q[:30]}")

    return {
        "n": len(widths),
        "mean": round(statistics.mean(widths), 2) if widths else 0,
        "min": min(widths) if widths else 0,
        "max": max(widths) if widths else 0,
        "details": details,
    }


# ============================================================
# 四、同义改写命中率
# ============================================================

def run_isolated_hit(cache, thread_prefix: str, n_paraphrases: int) -> Dict:
    """隔离会话：每个 thread 只存 1 条原 query，用同义改写去查。

    测的是缓存「纯语义匹配能力」——没有其它条目干扰时的上限。
    """
    logger.info("【测试 1】同义改写命中率（隔离会话：每会话仅 1 条缓存）")
    total = hit = 0
    latencies = []
    per_base = []

    for item in BASE_QUERIES:
        bid = item["id"]
        thread = f"{thread_prefix}_iso_{bid}"
        cache.clear_thread_cache(thread)
        cache.store_cache(thread, item["base"], make_docs(bid))

        b_hit = 0
        for q in item["paraphrases"][:n_paraphrases]:
            t0 = time.perf_counter()
            res = cache.query_cache(thread, q)
            lat_ms = (time.perf_counter() - t0) * 1000
            latencies.append(lat_ms)
            ok = res is not None and hit_tag(res) == bid
            b_hit += int(ok)
            total += 1
            hit += int(ok)
            logger.info(f"  {bid} {'✓' if ok else '✗'} ({lat_ms:.0f}ms) {q[:34]}")

        per_base.append({"id": bid, "hit": b_hit, "total": len(item["paraphrases"][:n_paraphrases])})
        cache.clear_thread_cache(thread)

    return {
        "total": total,
        "hit": hit,
        "hit_rate": round(hit / total, 4) if total else 0,
        **lat_stats(latencies),
        "per_base": per_base,
    }


def run_mixed_hit(cache, thread_prefix: str, n_paraphrases: int,
                  size: int | None = None, top_k: int = 3) -> Dict:
    """混合会话：一个 thread 里存下 `size` 条原 query，再用同义改写去查。

    这是生产真实形态（同一会话会积累多条缓存），既测命中率，也测
    「命中的是不是那一条」——命中但命中错条目 = 缓存污染，比不命中更危险。

    Args:
        size: 会话内缓存条目数（None = 全部 12 条）。用于画「命中率 vs 缓存规模」曲线。
        top_k: 传给 query_cache 的 KNN 候选数（生产默认 3）。
    """
    entries = BASE_QUERIES if size is None else BASE_QUERIES[:size]
    logger.info(f"【测试 2】同义改写命中率（混合会话：{len(entries)} 条缓存，KNN top_k={top_k}）")
    thread = f"{thread_prefix}_mix{len(entries)}_k{top_k}"
    cache.clear_thread_cache(thread)

    for item in entries:
        cache.store_cache(thread, item["base"], make_docs(item["id"]))

    # LSH 分桶冲突自检：多条 query 若被哈希进同一个桶，后写的会覆盖先写的
    keys = list(cache.redis.scan_iter(match=f"retrieve_cache:{thread}:*", count=200))
    stored_keys = len(keys)
    if stored_keys < len(entries):
        logger.warning(
            f"  LSH 分桶冲突：写入 {len(entries)} 条，实际只剩 {stored_keys} 个 key"
            f"（同桶覆盖，命中率会被低估）"
        )

    total = hit = correct = wrong = 0
    latencies = []
    per_base = []
    wrong_cases = []

    for item in entries:
        bid = item["id"]
        b_hit = b_correct = 0
        for q in item["paraphrases"][:n_paraphrases]:
            t0 = time.perf_counter()
            res = cache.query_cache(thread, q, top_k=top_k)
            lat_ms = (time.perf_counter() - t0) * 1000
            latencies.append(lat_ms)

            total += 1
            if res is None:
                logger.info(f"  {bid} ✗ 未命中 ({lat_ms:.0f}ms) {q[:34]}")
                continue
            hit += 1
            b_hit += 1
            tag = hit_tag(res)
            if tag == bid:
                correct += 1
                b_correct += 1
                logger.info(f"  {bid} ✓ 命中正确 ({lat_ms:.0f}ms) {q[:34]}")
            else:
                wrong += 1
                wrong_cases.append({"query": q, "expected": bid, "got": tag})
                logger.warning(f"  {bid} ⚠ 命中但条目错误（得到 {tag}） {q[:34]}")

        per_base.append({"id": bid, "hit": b_hit, "correct": b_correct,
                         "total": len(item["paraphrases"][:n_paraphrases])})

    cache.clear_thread_cache(thread)
    return {
        "thread_id": thread,
        "stored_entries": len(entries),
        "top_k": top_k,
        "stored_keys_after_lsh": stored_keys,
        "total": total,
        "hit": hit,
        "hit_rate": round(hit / total, 4) if total else 0,
        "correct": correct,
        "correct_hit_rate": round(correct / total, 4) if total else 0,
        "wrong": wrong,
        "wrong_hit_rate": round(wrong / total, 4) if total else 0,
        **lat_stats(latencies),
        "per_base": per_base,
        "wrong_cases": wrong_cases,
    }


def run_knn_recall(cache, thread_prefix: str, n_paraphrases: int, top_k_max: int = 12) -> Dict:
    """根因诊断：KNN 向量初筛阶段，正确条目到底进没进候选集（rerank 之前）。

    `query_cache` 先按向量距离取 top_k 个候选，再交给 rerank 判定。若正确条目
    压根没进候选集，rerank 再准也只能未命中——所以命中率的上限是 recall@top_k。
    这里直接用 top_k_max 拉一次长榜，记录正确条目排在几位，据此算出 recall@k。
    """
    import numpy as np
    from redis.commands.search.query import Query

    logger.info(f"【测试 2b】KNN 候选召回诊断（{len(BASE_QUERIES)} 条缓存，长榜 top_k={top_k_max}）")
    thread = f"{thread_prefix}_knn"
    cache.clear_thread_cache(thread)
    for item in BASE_QUERIES:
        cache.store_cache(thread, item["base"], make_docs(item["id"]))

    ranks = []
    for item in BASE_QUERIES:
        for q in item["paraphrases"][:n_paraphrases]:
            vec = cache.query_to_vector(q)
            vec_bytes = np.array(vec, dtype=np.float32).tobytes()
            qq = (
                Query(f"@thread_id:{{{thread}}} => [KNN {top_k_max} @query_embedding $vec AS vector_score]")
                .sort_by("vector_score")
                .return_fields("query_text")
                .dialect(2)
            )
            try:
                res = cache.redis.ft(cache.index_name).search(qq, query_params={"vec": vec_bytes})
                texts = [d.query_text for d in res.docs]
            except Exception as e:
                logger.warning(f"  KNN 查询失败：{e}")
                texts = []
            rank = texts.index(item["base"]) + 1 if item["base"] in texts else None
            ranks.append(rank)

    cache.clear_thread_cache(thread)
    total = len(ranks)
    found = [r for r in ranks if r is not None]
    recall = {}
    for k in (1, 3, 5, 8, top_k_max):
        recall[f"recall@{k}"] = round(sum(1 for r in found if r <= k) / total, 4) if total else 0
    logger.info(f"  正确条目在长榜内的比例: {len(found)}/{total}；" +
                " ".join(f"recall@{k.split('@')[1]}={v*100:.1f}%" for k, v in recall.items()))
    return {
        "total": total,
        "in_long_list": len(found),
        "rank_distribution": {
            "rank1": sum(1 for r in found if r == 1),
            "rank2_3": sum(1 for r in found if 2 <= r <= 3),
            "rank4_5": sum(1 for r in found if 4 <= r <= 5),
            "rank6_plus": sum(1 for r in found if r >= 6),
            "not_found": total - len(found),
        },
        **recall,
    }


def run_negatives(cache, thread_prefix: str) -> Dict:
    """负样本误命中率：硬负样本（同域不同问）+ 跨域负样本。"""
    logger.info("【测试 3】负样本误命中率（混合会话 12 条缓存）")
    thread = f"{thread_prefix}_neg"
    cache.clear_thread_cache(thread)
    for item in BASE_QUERIES:
        cache.store_cache(thread, item["base"], make_docs(item["id"]))

    hard_false = hard_cases = 0
    for q in HARD_NEGATIVES:
        res = cache.query_cache(thread, q)
        if res is not None:
            hard_false += 1
            logger.warning(f"  硬负样本 ⚠ 误命中（{hit_tag(res)}） {q[:34]}")
        else:
            logger.info(f"  硬负样本 ✓ 正确未命中 {q[:34]}")
    hard_cases = len(HARD_NEGATIVES)

    unrel_false = 0
    for q in UNRELATED_QUERIES:
        res = cache.query_cache(thread, q)
        if res is not None:
            unrel_false += 1
            logger.warning(f"  跨域样本 ⚠ 误命中（{hit_tag(res)}） {q[:34]}")
        else:
            logger.info(f"  跨域样本 ✓ 正确未命中 {q[:34]}")

    cache.clear_thread_cache(thread)
    return {
        "hard": {"total": hard_cases, "false_hit": hard_false,
                 "false_hit_rate": round(hard_false / hard_cases, 4) if hard_cases else 0},
        "unrelated": {"total": len(UNRELATED_QUERIES), "false_hit": unrel_false,
                      "false_hit_rate": round(unrel_false / len(UNRELATED_QUERIES), 4) if UNRELATED_QUERIES else 0},
    }


# ============================================================
# 五、有无缓存 embedding 调用率
# ============================================================

def build_stream(n_bases: int, n_variants: int) -> List[Tuple[str, str]]:
    """构造 query 流：(base_id, query)。

    顺序为「先全部原句，再逐轮改写」，模拟用户先问一轮、再换措辞追问。
    """
    stream = [(it["id"], it["base"]) for it in BASE_QUERIES[:n_bases]]
    for v in range(n_variants - 1):
        for it in BASE_QUERIES[:n_bases]:
            if v < len(it["paraphrases"]):
                stream.append((it["id"], it["paraphrases"][v]))
    return stream


def arm_no_cache(stream: List[Tuple[str, str]], width: int) -> Dict:
    """无缓存臂：每条 query 走完整检索，嵌入 `width` 条文本（原问题 + 改写查询）。

    注意：真实链路里这 `width` 条文本由 Chroma 一次性批量嵌入（1 次 HTTP，width 条文本），
    这里同样用 embed_documents 批量调用，计数口径与生产一致。
    """
    counter = EmbeddingTextCounter()
    counter.start()
    latencies = []
    try:
        for _bid, q in stream:
            # 用原问题 + 同组改写句凑满 width 条，模拟 dense_query 的多路查询；
            # 文本内容不影响「嵌入条数」这一计数目标
            texts = [q]
            for it in BASE_QUERIES:
                if len(texts) >= width:
                    break
                if it["id"] != _bid:
                    continue
                texts.extend(it["paraphrases"])
            texts = texts[:width] if len(texts) >= width else (texts + [q] * (width - len(texts)))
            t0 = time.perf_counter()
            embed_model.embed_documents(texts)
            latencies.append((time.perf_counter() - t0) * 1000)
    finally:
        counter.stop()
    return {
        "queries": len(stream),
        "embedded_texts": counter.texts,
        "http_calls": counter.calls,
        "per_query_texts": round(counter.texts / len(stream), 2) if stream else 0,
        "avg_latency_ms": round(statistics.mean(latencies), 2) if latencies else 0,
    }


def arm_with_cache(cache, stream: List[Tuple[str, str]], width: int, thread: str) -> Dict:
    """有缓存臂：每条 query 先查缓存（1 条文本），未命中才检索（width 条）+ 回写（1 条）。"""
    counter = EmbeddingTextCounter()
    counter.start()
    hits = misses = 0
    latencies = []
    try:
        cache.clear_thread_cache(thread)
        for _bid, q in stream:
            t0 = time.perf_counter()
            res = cache.query_cache(thread, q)          # 1 条文本（含 rerank 验证）
            if res is not None:
                hits += 1
            else:
                misses += 1
                texts = [q] * width
                embed_model.embed_documents(texts)      # 检索：width 条文本
                cache.store_cache(thread, q, make_docs(_bid))  # 回写：1 条文本
            latencies.append((time.perf_counter() - t0) * 1000)
    finally:
        counter.stop()
        cache.clear_thread_cache(thread)
    total = len(stream)
    return {
        "queries": total,
        "hits": hits,
        "misses": misses,
        "hit_rate": round(hits / total, 4) if total else 0,
        "embedded_texts": counter.texts,
        "http_calls": counter.calls,
        "per_query_texts": round(counter.texts / total, 2) if total else 0,
        "avg_latency_ms": round(statistics.mean(latencies), 2) if latencies else 0,
    }


def sensitivity(hit_rate: float, width: int) -> List[Dict]:
    """敏感性分析：不同改写宽度下，给定命中率能省多少 embedding。

    公式（按文本条数）：
      有缓存 = 1（查缓存） + (1 - h) × (width 检索 + 1 回写)
      无缓存 = width
    """
    rows = []
    for w in (2, 3, 4, 5):
        with_cache = 1 + (1 - hit_rate) * (w + 1)
        no_cache = w
        rows.append({
            "width": w,
            "per_query_with_cache": round(with_cache, 2),
            "per_query_no_cache": round(no_cache, 2),
            "reduction_pct": round((1 - with_cache / no_cache) * 100, 2) if no_cache else 0,
        })
    for r in rows:
        if r["width"] == width:
            r["is_measured_width"] = True
    return rows


# ============================================================
# 主流程
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Mitta 检索语义缓存专项评测（E6-B）")
    parser.add_argument("--redis-url", type=str, default=DEFAULT_REDIS_URL,
                        help=f"redis-stack URL（默认 {DEFAULT_REDIS_URL}）")
    parser.add_argument("--thread", type=str, default="eval_cache_hit", help="测试 thread 前缀")
    parser.add_argument("--bases", type=int, default=12, help="命中率测试用前 N 组（默认 12）")
    parser.add_argument("--paraphrases", type=int, default=3, help="每组用几条同义改写（默认 3）")
    parser.add_argument("--rate-bases", type=int, default=8, help="调用率测试用前 N 组（默认 8）")
    parser.add_argument("--rate-variants", type=int, default=3, help="每组在流里出现几次（默认 3）")
    parser.add_argument("--mixed-sizes", type=str, default="1,3,6,12",
                        help="混合会话的缓存条目数梯度（默认 1,3,6,12）")
    parser.add_argument("--mixed-topk", type=str, default="3,12",
                        help="混合会话的 KNN 候选数（默认 3,12；非 3 的那档只在最大规模上跑）")
    parser.add_argument("--rewrite-probe", type=int, default=6, help="改写宽度抽样条数（默认 6，0 跳过）")
    parser.add_argument("--skip-hit", action="store_true", help="跳过命中率测试")
    parser.add_argument("--skip-rate", action="store_true", help="跳过 embedding 调用率测试")
    parser.add_argument("--tag", type=str, default="", help="输出文件名后缀，用于保留多版本")
    parser.add_argument("--out-dir", type=str, default=None, help="报告输出目录（默认 reports/<今天>）")
    args = parser.parse_args()

    if args.bases < len(BASE_QUERIES):
        BASE_QUERIES[:] = BASE_QUERIES[:args.bases]

    logger.info("=" * 68)
    logger.info("Mitta 检索语义缓存专项评测（E6-B）")
    logger.info(f"Redis: {args.redis_url} | 组数: {len(BASE_QUERIES)} × {args.paraphrases} 改写")
    logger.info("=" * 68)

    if not selfcheck_counter():
        logger.error("计数口径自检失败，报告的 embedding 条数不可信，终止")
        return

    cache = CacheService(redis_db_url=args.redis_url)
    cache.open(embed_model=embed_model, online_rerank=online_rerank)
    logger.success("Redis + embed + rerank 就绪")

    result: Dict = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "script": "eval_cache_hitrate.py",
        "redis_url": args.redis_url,
        "rerank_hit_score": CACHE_RERANK_HIT_SCORE,
        "config": {
            "base_groups": len(BASE_QUERIES),
            "paraphrases_per_group": args.paraphrases,
            "mixed_sizes": args.mixed_sizes,
            "mixed_topk": args.mixed_topk,
            "hard_negatives": len(HARD_NEGATIVES),
            "unrelated_negatives": len(UNRELATED_QUERIES),
        },
    }

    # ---- 命中率 ----
    if not args.skip_hit:
        result["isolated_hit"] = run_isolated_hit(cache, args.thread, args.paraphrases)

        sizes = [int(s) for s in args.mixed_sizes.split(",") if s.strip()]
        topks = [int(t) for t in args.mixed_topk.split(",") if t.strip()]
        configs = [(s, 3) for s in sizes]
        for tk in topks:
            if tk != 3:
                configs.append((max(sizes), tk))

        mixed = {}
        for size, tk in configs:
            mixed[f"size{size}_topk{tk}"] = run_mixed_hit(
                cache, args.thread, args.paraphrases, size=size, top_k=tk
            )
        result["mixed_hit"] = mixed
        result["knn_recall"] = run_knn_recall(cache, args.thread, args.paraphrases)
        result["negatives"] = run_negatives(cache, args.thread)

    # ---- embedding 调用率 ----
    if not args.skip_rate:
        width = 4
        if args.rewrite_probe > 0:
            probe_qs = []
            for it in BASE_QUERIES[:max(1, args.rewrite_probe // 2)]:
                probe_qs.append(it["base"])
                if it["paraphrases"]:
                    probe_qs.append(it["paraphrases"][0])
            probe = probe_rewrite_width(probe_qs[:args.rewrite_probe])
            result["rewrite_probe"] = probe
            width = max(2, int(round(probe["mean"])))
            logger.info(f"实测改写宽度均值 {probe['mean']} → 检索阶段按 {width} 条文本/query 计")

        stream = build_stream(args.rate_bases, args.rate_variants)
        logger.info(f"【测试 4】embedding 调用率：{len(stream)} 条 query（{args.rate_bases} 组 × {args.rate_variants} 次）")
        no_cache = arm_no_cache(stream, width)
        with_cache = arm_with_cache(cache, stream, width, f"{args.thread}_rate")

        reduction = (1 - with_cache["embedded_texts"] / no_cache["embedded_texts"]) * 100 \
            if no_cache["embedded_texts"] else 0
        # 实测命中率对应的理论降幅：命中率是本指标的唯一自变量，单给一个数字会误导
        measured_rates = []
        if "isolated_hit" in result:
            measured_rates.append(("isolated_size1", result["isolated_hit"]["hit_rate"]))
        if "mixed_hit" in result:
            for key, m in result["mixed_hit"].items():
                if key.endswith("topk3"):
                    measured_rates.append((key, m["hit_rate"]))

        result["embedding_rate"] = {
            "stream_queries": len(stream),
            "retrieval_width": width,
            "no_cache": no_cache,
            "with_cache": with_cache,
            "reduction_pct": round(reduction, 2),
            "sensitivity": sensitivity(with_cache["hit_rate"], width),
            "reduction_at_measured_hit_rates": [
                {
                    "scenario": name,
                    "hit_rate": h,
                    "per_query_with_cache": round(1 + (1 - h) * (width + 1), 2),
                    "per_query_no_cache": width,
                    "reduction_pct": round((1 - (1 + (1 - h) * (width + 1)) / width) * 100, 2),
                }
                for name, h in measured_rates
            ],
        }

        logger.info(f"  无缓存：{no_cache['embedded_texts']} 条文本（{no_cache['per_query_texts']}/query）")
        logger.info(f"  有缓存：{with_cache['embedded_texts']} 条文本（{with_cache['per_query_texts']}/query，"
                    f"命中率 {with_cache['hit_rate']*100:.1f}%）")
        logger.info(f"  下降：{reduction:.2f}%")

    # ---- 汇总 ----
    logger.info("\n【汇总】")
    if "isolated_hit" in result:
        logger.info(f"  同义改写命中率（隔离会话）: {result['isolated_hit']['hit_rate']*100:.1f}% "
                    f"({result['isolated_hit']['hit']}/{result['isolated_hit']['total']})")
    if "mixed_hit" in result:
        for key, m in result["mixed_hit"].items():
            logger.info(f"  同义改写命中率（{key}）: {m['hit_rate']*100:.1f}% ({m['hit']}/{m['total']})，"
                        f"条目正确 {m['correct_hit_rate']*100:.1f}% / 错误 {m['wrong_hit_rate']*100:.1f}%")
    if "knn_recall" in result:
        k = result["knn_recall"]
        logger.info(f"  KNN 候选召回: recall@3={k['recall@3']*100:.1f}% | recall@5={k['recall@5']*100:.1f}% "
                    f"| recall@12={k['recall@12']*100:.1f}%")
    if "negatives" in result:
        logger.info(f"  硬负样本误命中率: {result['negatives']['hard']['false_hit_rate']*100:.1f}% | "
                    f"跨域误命中率: {result['negatives']['unrelated']['false_hit_rate']*100:.1f}%")

    result["caveats"] = [
        "命中率只对本项目 12 组技术类 query 负责，不等于任意业务语料下的命中率。",
        "query_cache 每次调用都要先嵌入 query（cache_service.py:202），因此命中路径不是 0 次 embedding，"
        "收益来自「省掉多路检索嵌入」，不是「省掉全部 embedding」。",
        "有无缓存对比中，无缓存臂只复现检索嵌入量，不含重排/LLM 改写耗时；端到端延迟以 eval_sse.py 为准。",
        "rerank 验证走 SiliconFlow 在线接口，网络抖动会同时影响命中率与延迟，多跑几次更稳。",
    ]

    suffix = f"_{args.tag}" if args.tag else ""
    out = resolve_report_path(f"cache_hitrate_eval_report{suffix}.json", out_dir=args.out_dir)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.success(f"报告已保存: {out}")

    cache.close()


if __name__ == "__main__":
    main()
