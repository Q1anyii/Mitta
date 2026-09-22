"""并行检索编排节点（2026-09-19 H-20260919-11）。

背景
====
原串行链路：rewrite → dense_query(4 路) → bm25 → RRF → rerank
实测（21 条项目评测集，2026-09-19）：改写 2226~2694 ms 占约 45%，
稠密 1280 ms，BM25 12 ms，重排 760 ms，端到端混合检索 4~5.6 s。

真实依赖关系
============
- `rewrite`        依赖 question + history              → 无前置依赖，可立即发起
- `dense(原问题)`  依赖 question                         → **不依赖改写**，可立即发起
- `bm25`           依赖 question                         → 不依赖改写，可立即发起
- `dense(改写后)`  依赖 rewrite 的产出                   → 必须等改写

关键发现：生产 dense_query 的查询集合是 `[原问题] + [主查询] + 子查询`，
其中「原问题」那一路根本不需要等改写，却因为写在同一个节点里被一起串行化了。
拆开后第一阶段就能省下 1 路稠密的等待时间。

为什么用线程池而不是 LangGraph 并行边
=====================================
主图走 `asyncio.to_thread(graph.invoke(...))`，是同步调用；
LangGraph 的同步 superstep 对同一批无依赖节点是**串行执行**的，
只改边不换执行器不会变快（要真并发得把节点改成 async + 改 ainvoke，
牵动 main_graph / retrieve_node / chat_service 全链路，风险大）。
所以在单个节点内部用 ThreadPoolExecutor 做扇出，收益确定、回退简单。

编排形状
========
    阶段一（并发）：rewrite ∥ dense(原问题) ∥ bm25
    阶段二（并发）：dense(主查询) ∥ dense(子查询1) ∥ dense(子查询2)   [等改写结果]
    阶段三（串行）：RRF 融合 → rerank → filter（仍由后续图节点完成）

降级策略
========
任一路超时/异常都只丢弃该路结果，不抛异常、不中断整条链路：
- rewrite 失败 → 退化为只用原问题检索（等价单路 + BM25）
- dense 任一路失败 → 该路贡献 0 条候选
- BM25 失败 → 稀疏路为空（RedisSearch 不可用时常见）
- 全部稠密路失败 → 只剩 BM25，仍继续走 rerank/filter
注意：Python 无法强制杀线程，超时是「不再等待该 future 的结果」，
线程会在后台跑完（结果被丢弃），不会泄漏到状态里。
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import List

from loguru import logger

from constant.retrieval_constants import (
    REWRITE_TIMEOUT_SEC,
    DENSE_TIMEOUT_SEC,
    SPARSE_TIMEOUT_SEC,
    RETRIEVE_PARALLEL_WORKERS,
    VECTOR_N_RESULTS,
    BM25_TOP_K,
)
from graphs.nodes.retrieve.query_nodes import run_bm25, run_rewrite
from graphs.state import RAGState
from vector.retrieve_doc import RetrievedDoc


def _safe_result(future, timeout: float, name: str, default):
    """取 future 结果，超时或异常都返回 default（单路降级，不中断整体）。"""
    try:
        return future.result(timeout=timeout)
    except FutureTimeout:
        logger.warning(f"[parallel] {name} 超时（>{timeout}s），丢弃该路结果")
        return default
    except Exception as e:
        logger.warning(f"[parallel] {name} 失败，丢弃该路结果: {e}")
        return default


def parallel_retrieve(
    state: RAGState,
    model,
    vector_store,
    cache_service,
    n_results: int = VECTOR_N_RESULTS,
    bm25_top_k: int = BM25_TOP_K,
) -> dict:
    """并行检索：改写 ∥ 原问题稠密 ∥ BM25，改写完成后再并发跑改写后的多路稠密。

    Args:
        state: 含 question / history
        model: LLM（改写用）
        vector_store: 向量库
        cache_service: Redis（BM25 + 改写缓存用）
        n_results: 每路稠密召回条数
        bm25_top_k: BM25 召回条数

    Returns:
        {
          "rewritten_queries": [主查询, 子查询...],   # rerank 节点要用来做精排
          "rank_list": [[doc...], ...],              # 二维，交给 retrieve 节点做 RRF
        }
    """
    question = state["question"]
    history_text = "\n".join(
        f"{m['role']}: {m['content']}" for m in state.get("history", [])
    )
    t0 = __import__("time").perf_counter()

    def dense_one(q: str) -> List[RetrievedDoc]:
        res = vector_store.query([q], n_results=n_results)
        return res[0] if res else []

    with ThreadPoolExecutor(max_workers=RETRIEVE_PARALLEL_WORKERS) as ex:
        # ── 阶段一：三条无依赖的路同时发起 ──
        f_rewrite = ex.submit(run_rewrite, question, history_text, model, cache_service)
        f_dense_origin = ex.submit(dense_one, question)
        f_bm25 = ex.submit(run_bm25, question, cache_service, bm25_top_k)

        # 改写是阶段二的唯一前置，先等它（超时则降级为只用原问题）
        rewritten = _safe_result(f_rewrite, REWRITE_TIMEOUT_SEC, "rewrite", [question])
        if not rewritten:
            rewritten = [question]
        # 与生产保持一致的上限：原问题 + 主查询 + 子查询 = 4 路稠密
        rw_queries = list(rewritten)[:3]

        # ── 阶段二：改写后的多路稠密彼此并行 ──
        f_dense_rw = [ex.submit(dense_one, q) for q in rw_queries]

        rank_list: List[List[RetrievedDoc]] = [
            _safe_result(f_dense_origin, DENSE_TIMEOUT_SEC, "dense(原问题)", [])
        ]
        for q, f in zip(rw_queries, f_dense_rw):
            rank_list.append(_safe_result(f, DENSE_TIMEOUT_SEC, f"dense(改写:{q[:12]})", []))
        rank_list.append(_safe_result(f_bm25, SPARSE_TIMEOUT_SEC, "bm25", []))

    elapsed_ms = (__import__("time").perf_counter() - t0) * 1000
    logger.info(
        f"[parallel] 改写+{len(rank_list)} 路召回完成 "
        f"({elapsed_ms:.0f}ms) 命中条数={[len(r) for r in rank_list]}"
    )
    return {"rewritten_queries": rewritten, "rank_list": rank_list}
