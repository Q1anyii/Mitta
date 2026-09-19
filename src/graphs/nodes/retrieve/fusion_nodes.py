"""融合与重排节点：RRF 融合 + 去重 + 在线重排 + MMR 多样性选择 + 阈值过滤。

拆分自原 retrieve_graph.py 的 rrf_fusion、dedup_by_text、retrieve、rerank、filter_node。
依赖：online_rerank（在线重排函数），通过参数注入；MMR 需要 vector_store 做文本向量化。
"""

from loguru import logger

from constant.retrieval_constants import (
    RRF_K,
    MMR_ENABLED,
    MMR_LAMBDA,
    MMR_TOP_CANDIDATES,
    MMR_TOP_SELECT,
    RERANK_FILTER_THRESHOLD,
)
from graphs.state import RAGState
from vector.retrieve_doc import RetrievedDoc


def rrf_fusion(results: list[list[RetrievedDoc]], k: int = RRF_K) -> list[RetrievedDoc]:
    """RRF（Reciprocal Rank Fusion）排名融合：将多路检索结果合并为单一排序列表。

    公式：score(doc) = Σ 1 / (k + rank_i + 1)
    其中 rank_i 是文档在第 i 路结果中的排名（从 0 开始）。

    RRF 的优势：不需要归一化不同检索方式的分数（向量距离 vs BM25 分数量纲不同），
    只依赖排名，鲁棒性强。k=60 是业界常用值，平衡排名权重。

    Args:
        results: 二维列表，每个子列表是一路检索的结果（已按相关性排序）
        k: RRF 常数，默认 RRF_K（60）

    Returns:
        按融合分数降序排列的文档列表（去重，同一文档只保留分数最高的实例）
    """
    scores = {}

    for docs in results:
        for rank, doc in enumerate(docs):
            # 融合 key 用向量库主键（sha256 哈希 id）；未携带时回退内容文本
            key = doc.id or doc.text
            if key not in scores:
                scores[key] = {"doc": doc, "score": 0.0}
            scores[key]["score"] += 1.0 / (k + rank + 1)

    return [
        item["doc"]
        for item in sorted(
            scores.values(),
            key=lambda x: x["score"],
            reverse=True,
        )
    ]


def dedup_by_text(docs: list[RetrievedDoc]) -> list[RetrievedDoc]:
    """按文档正文去重，保留排名靠前的那条。

    稠密检索和 BM25 可能召回同一文档（不同 id 但内容相同），
    去重避免重排时重复计算和最终结果重复。

    Args:
        docs: RRF 融合后的文档列表（已排序）

    Returns:
        去重后的文档列表
    """
    seen = set()
    result = []
    for d in docs:
        # BM25 结果 text 为空，用 id 去重；向量结果用 text 去重
        key = d.text if d.text else d.id
        if key and key not in seen:
            seen.add(key)
            result.append(d)
    return result


def retrieve(state: RAGState) -> dict:
    """检索融合节点：RRF 融合 + 去重。

    将稠密向量检索（多路查询）和 BM25 稀疏检索的结果通过 RRF 算法融合，
    然后按正文去重，得到候选文档列表。

    Args:
        state: 含 rank_list（二维检索结果）

    Returns:
        {"merged_docs": [融合去重后的候选文档]}
    """
    rank_list = state["rank_list"]
    merged_docs = rrf_fusion(rank_list)
    merged_docs = dedup_by_text(merged_docs)
    return {"merged_docs": merged_docs}


def _mmr_embed(texts: list[str]) -> list[list[float]]:
    """对候选文本批量编码并 L2 归一化（dot product 即 cosine）。

    直接用全局 embed_model（bge-m3），不依赖 vector_store 实现差异
    （Chroma 实现无 _embed_texts，Milvus 有；统一走 embed_model 最稳）。
    """
    from init import embed_model  # 延迟 import 避免循环依赖
    raw = embed_model.embed_documents(texts)
    out = []
    for v in raw:
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        out.append([x / norm for x in v])
    return out


def _mmr_select(
    candidates: list[RetrievedDoc],
    embeddings: list[list[float]],
    k: int = MMR_TOP_SELECT,
    lambda_: float = MMR_LAMBDA,
) -> list[RetrievedDoc]:
    """MMR（Maximal Marginal Relevance）贪心多样性选择。

    从 rerank topN 候选里选 k 篇"相关且彼此不语义扎堆"的文档：
      score(doc) = λ × rerank_score - (1-λ) × max_cosine_sim(doc, 已选集合)

    向量已归一化（vector_store._embed_texts 输出），dot product 即 cosine similarity。

    Args:
        candidates: 已按 rerank_score 降序排列的候选文档（metadata 含 relevance_score）
        embeddings: 与 candidates 对齐的归一化向量
        k: 最终选篇数
        lambda_: 相关性 vs 多样性权重

    Returns:
        MMR 选出的 k 篇文档（按选择顺序，首篇即 rerank 分最高者）
    """
    if len(candidates) <= k:
        return candidates

    selected_idx = [0]  # 初始选 rerank 分最高的第 0 篇
    # 预取每篇的 relevance_score
    rel_scores = [float(c.metadata.get("relevance_score", 0.0)) for c in candidates]
    # 归一化分：rerank_score 本身量纲不固定（bge-reranker 输出 logit），用 min-max 归一化到 [0,1]
    smin, smax = min(rel_scores), max(rel_scores)
    span = smax - smin if smax > smin else 1.0
    norm_rel = [(s - smin) / span for s in rel_scores]

    while len(selected_idx) < k:
        best_idx = None
        best_score = -float("inf")
        selected_vecs = [embeddings[i] for i in selected_idx]
        for i in range(len(candidates)):
            if i in selected_idx:
                continue
            # 与已选集合的最大 cosine 相似度（向量已归一化，dot 即 cosine）
            cur = embeddings[i]
            max_sim = max(
                sum(a * b for a, b in zip(cur, sv))
                for sv in selected_vecs
            )
            score = lambda_ * norm_rel[i] - (1 - lambda_) * max_sim
            if score > best_score:
                best_score = score
                best_idx = i
        if best_idx is None:
            break
        selected_idx.append(best_idx)

    return [candidates[i] for i in selected_idx]


def rerank(state: RAGState, online_rerank, vector_store=None) -> dict:
    """在线重排 + MMR 多样性选择。

    用 SiliconFlow bge-reranker-v2-m3 API 对候选文档重新排序，
    然后（MMR_ENABLED 时）从 topN 候选里贪心选 k 篇"相关且彼此不语义扎堆"的文档。
    MMR 解决多点分散题 key_points 覆盖低的问题：同章节相邻段落语义重复，
    rerank 只按相关度排序会把扎堆段落都排前面，挤掉其他关键点的文档。

    Args:
        state: 含 merged_docs（候选文档）和 rewritten_queries（用主查询做重排）
        online_rerank: 在线重排函数（依赖注入）
        vector_store: 向量存储（依赖注入，用于 MMR 阶段对候选文本批量编码；
                      None 或 MMR_ENABLED=False 时回退为纯 rerank topK）

    Returns:
        {"reranked_docs": [选出的 top K 文档，metadata 含 relevance_score]}
    """
    docs = state["merged_docs"]
    if not docs:
        return {"reranked_docs": []}
    # 用改写后的主查询做重排（比原始问题更精确）
    query = state["rewritten_queries"][0]
    # 拿 MMR_TOP_CANDIDATES 篇候选（MMR 阶段再从中选 MMR_TOP_SELECT 篇）
    results = online_rerank(query, [doc.text for doc in docs], top_n=MMR_TOP_CANDIDATES)
    top_docs = []
    for r in results:
        doc = docs[r["index"]]
        doc.metadata["relevance_score"] = r["relevance_score"]  # 分数落 metadata，filter_node 用
        top_docs.append(doc)

    # MMR 多样性选择：开关开启且候选数>MMR_TOP_SELECT 时执行；否则纯 rerank topK
    if MMR_ENABLED and len(top_docs) > MMR_TOP_SELECT:
        try:
            embeddings = _mmr_embed([d.text for d in top_docs])
            selected = _mmr_select(top_docs, embeddings)
            logger.info(
                f"[mmr] λ={MMR_LAMBDA} candidates={len(top_docs)} selected={len(selected)} "
                f"ids={[d.id[:8] for d in selected]}"
            )
            return {"reranked_docs": selected}
        except Exception as e:
            logger.warning(f"[mmr] 多样性选择失败，回退纯 rerank top{MMR_TOP_SELECT}: {e}")

    # 默认 / MMR 失败 / 开关关闭：纯 rerank topK
    return {"reranked_docs": top_docs[:MMR_TOP_SELECT]}


def filter_node(state: RAGState) -> dict:
    """按重排分数过滤低相关文档。

    阈值 RERANK_FILTER_THRESHOLD（2026-09-19 P1 放宽到 0.15）：过滤明显噪声，
    保留中等相关以上文档。过滤后为空时兜底返回原始 top 3（宁可不准确也不返回空）。

    Args:
        state: 含 reranked_docs（重排后的文档，metadata 含 relevance_score）

    Returns:
        {"reranked_docs": [过滤后的文档，最多 MMR_TOP_SELECT 条]}
    """
    reranked_docs = state["reranked_docs"]

    # 阈值改为常量引用（2026-09-19），不再硬编码
    finally_docs = [
        doc for doc in reranked_docs
        if doc.metadata.get("relevance_score", 0.0) >= RERANK_FILTER_THRESHOLD
    ]

    if finally_docs:
        return {"reranked_docs": finally_docs[:MMR_TOP_SELECT]}

    # 兜底：过滤后为空时，返回原始 top 3（宁可不准确也不返回空）
    return {"reranked_docs": reranked_docs[:3]}
