"""融合与重排节点：RRF 融合 + 去重 + 在线重排 + MMR 多样性选择 + 阈值过滤。

拆分自原 retrieve_graph.py 的 rrf_fusion、dedup_by_text、retrieve、rerank、filter_node。
依赖：online_rerank（在线重排函数），通过参数注入；MMR 需要 vector_store 做文本向量化。
"""

from loguru import logger

from constant.retrieval_constants import (
    RRF_K,
    MMR_STAGE,
    MMR_LAMBDA,
    MMR_PRE_LAMBDA,
    MMR_TOP_CANDIDATES,
    MMR_TOP_SELECT,
    MMR_PRE_SELECT,
    MMR_LEXICAL_JACCARD,
    RERANK_FILTER_THRESHOLD,
    FILTER_FALLBACK_TOP_K,
)
from graphs.state import RAGState
from vector.retrieve_doc import RetrievedDoc


def rrf_fusion(
    results: list[list[RetrievedDoc]],
    k: int = RRF_K,
    weights: list[float] | None = None,
) -> list[RetrievedDoc]:
    """RRF（Reciprocal Rank Fusion）排名融合：将多路检索结果合并为单一排序列表。

    公式：score(doc) = Σ w_i / (k + rank_i + 1)
    其中 rank_i 是文档在第 i 路结果中的排名（从 0 开始），w_i 是该路权重。

    RRF 的优势：不需要归一化不同检索方式的分数（向量距离 vs BM25 分数量纲不同），
    只依赖排名，鲁棒性强。k=60 是业界常用值，平衡排名权重。

    路级权重（2026-09-23）：子查询定位是"兜底补充"而非平起平坐的主查询，
    其权重压低，避免泛化子查询召回的噪声文档在 RRF 里稀释主路排名。
    weights 为 None 时各路均权 1.0，向后兼容。

    Args:
        results: 二维列表，每个子列表是一路检索的结果（已按相关性排序）
        k: RRF 常数，默认 RRF_K（60）
        weights: 各路权重，None 视为全 1.0

    Returns:
        按融合分数降序排列的文档列表（去重，同一文档只保留分数最高的实例）
    """
    scores = {}

    for i, docs in enumerate(results):
        w = weights[i] if weights and i < len(weights) else 1.0
        for rank, doc in enumerate(docs):
            # 融合 key 用向量库主键（sha256 哈希 id）；未携带时回退内容文本
            key = doc.id or doc.text
            if key not in scores:
                scores[key] = {"doc": doc, "score": 0.0}
            scores[key]["score"] += w / (k + rank + 1)

    ranked = sorted(scores.values(), key=lambda x: x["score"], reverse=True)
    # RRF 分落 metadata：pre 阶段 MMR 用 RRF 分充当"相关性"信号
    # （cross-encoder 还没跑，此时没有 relevance_score 可用）
    for item in ranked:
        doc = item["doc"]
        if doc.metadata is None:
            doc.metadata = {}
        doc.metadata["rrf_score"] = item["score"]
    return [item["doc"] for item in ranked]


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
    # 路级权重：rank_list 结构为 [原问题, 主查询, 子查询..., bm25]
    # 主路（原问题/主查询/bm25）权重 1.0，子查询降权 0.4（兜底补充，不喧宾夺主）
    n = len(rank_list)
    if n >= 3:
        weights = [1.0, 1.0] + [0.4] * (n - 3) + [1.0]
    else:
        weights = None
    merged_docs = rrf_fusion(rank_list, weights=weights)
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
    rel_scores: list[float] | None = None,
    k: int = MMR_TOP_SELECT,
    lambda_: float = MMR_LAMBDA,
) -> list[RetrievedDoc]:
    """MMR（Maximal Marginal Relevance）贪心多样性选择。

    从候选里选 k 篇"相关且彼此不语义扎堆"的文档：
      score(doc) = λ × norm(rel) - (1-λ) × max_cosine_sim(doc, 已选集合)

    向量已归一化（vector_store._embed_texts 输出），dot product 即 cosine similarity。

    Args:
        candidates: 候选文档，**必须已按 rel_scores 降序排列**（贪心首篇取 index 0）
        embeddings: 与 candidates 对齐的归一化向量
        rel_scores: 相关性分（post 阶段传 rerank 的 relevance_score；
                    pre 阶段传 RRF 融合分）。None 时回退读 metadata["relevance_score"]
        k: 最终选篇数
        lambda_: 相关性 vs 多样性权重

    Returns:
        MMR 选出的 k 篇文档（按选择顺序，首篇即相关性最高者）
    """
    if len(candidates) <= k:
        return candidates

    selected_idx = [0]  # 初始选相关性最高的第 0 篇
    # 相关性分：外部传入优先（pre 阶段用 RRF 分），否则读 metadata
    if rel_scores is None:
        rel_scores = [float(c.metadata.get("relevance_score", 0.0)) for c in candidates]
    rel_scores = [float(s) for s in rel_scores]
    # 归一化：rerank_score 量纲不固定（bge-reranker 输出 logit），RRF 分绝对值也很小，
    # 统一 min-max 归一化到 [0,1] 后才能与 cosine 相似度（同量纲）加权相减
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


def _mmr_pre_select(docs: list[RetrievedDoc], lambda_: float, k: int) -> list[RetrievedDoc]:
    """pre 阶段 MMR：对 RRF 融合后的候选池做多样性去重，只留 k 篇送进 cross-encoder。

    与 post 阶段的区别：
      - 相关性信号是 RRF 融合分（弱信号，故 λ 默认更高、更保相关）；
      - 目标是"给 rerank 喂差异化候选"而不是"决定最终上下文"，
        误删的代价由后续 rerank 兜不住（删掉就真的看不到了），所以 λ 偏保守。
      - 任何异常都直接回退为原候选池（不截断），保证不比 baseline 更差。
    """
    embeddings = _mmr_embed([d.text for d in docs])
    rel_scores = [float(d.metadata.get("rrf_score", 0.0)) for d in docs]
    return _mmr_select(docs, embeddings, rel_scores=rel_scores, k=k, lambda_=lambda_)


def _lexical_dedup_select(
    docs: list[RetrievedDoc],
    k: int = MMR_PRE_SELECT,
    threshold: float = MMR_LEXICAL_JACCARD,
) -> list[RetrievedDoc]:
    """词级去重（pre_lex 阶段）：贪心丢弃与已选文档词面高度重叠的候选。

    与 MMR 的区别：不需要 embedding，用 jieba 分词后的 **Jaccard 词面重合度**
    当"扎堆"判据。开销是纯 CPU（实测 ~5 ms，对比 pre 阶段 MMR 的 2180 ms embedding）。

    为什么够用：候选池扎堆的主因是 chunk 800/overlap 100 —— 相邻块共享 100 字正文
    **且共享同一段标题上下文**，词面重合度天然偏高；而语义不同但词面重合的文档很少。
    所以词面去重能覆盖大部分"真重复"，代价几乎为零。

    Args:
        docs: 按 RRF 分降序排列的候选（贪心首篇必留）
        k: 保留条数
        threshold: Jaccard ≥ 此值判定为重复，丢弃

    Returns:
        去重后的候选（保持原相对顺序）
    """
    if len(docs) <= k:
        return docs
    import jieba
    token_sets: list[set] = []
    for d in docs:
        try:
            toks = {t for t in jieba.lcut(d.text or "") if len(t) >= 2}
        except Exception:
            toks = set((d.text or "")[:200])
        token_sets.append(toks)

    selected: list[int] = [0]
    for i in range(1, len(docs)):
        if len(selected) >= k:
            break
        cur = token_sets[i]
        if not cur:
            selected.append(i)
            continue
        dup = False
        for j in selected:
            other = token_sets[j]
            inter = len(cur & other)
            if inter == 0:
                continue
            union = len(cur) + len(other) - inter
            if union and inter / union >= threshold:
                dup = True
                break
        if not dup:
            selected.append(i)
    return [docs[i] for i in selected]


def rerank(state: RAGState, online_rerank, vector_store=None,
           mmr_stage: str | None = None, mmr_lambda: float | None = None) -> dict:
    """在线重排 + MMR 多样性选择（pre / post / off 三档）。

    链路：
      MMR_STAGE="pre"  → MMR 去重候选池（~50 → MMR_PRE_SELECT）→ rerank → top MMR_TOP_SELECT
      MMR_STAGE="post" → rerank → top MMR_TOP_CANDIDATES → MMR 多样性选择 MMR_TOP_SELECT
      MMR_STAGE="off"  → rerank → top MMR_TOP_SELECT

    Args:
        state: 含 merged_docs（候选文档）和 rewritten_queries（用主查询做重排）
        online_rerank: 在线重排函数（依赖注入）
        vector_store: 向量存储（保留参数位，MMR 统一走全局 embed_model，未使用）
        mmr_stage: 覆盖常量 MMR_STAGE（评测脚本做 A/B 用）
        mmr_lambda: 覆盖 λ（pre 阶段覆盖 MMR_PRE_LAMBDA，post 阶段覆盖 MMR_LAMBDA）

    Returns:
        {"reranked_docs": [选出的 top K 文档，metadata 含 relevance_score]}
    """
    stage = (mmr_stage or MMR_STAGE or "off").strip().lower()
    docs = state["merged_docs"]
    if not docs:
        return {"reranked_docs": []}
    # 用改写后的主查询做重排（比原始问题更精确）
    query = state["rewritten_queries"][0]

    # ── pre 阶段：rerank 前先从候选池里剔掉语义扎堆的重复块 ──
    rerank_pool = docs
    if stage == "pre" and len(docs) > MMR_PRE_SELECT:
        try:
            lam = mmr_lambda if mmr_lambda is not None else MMR_PRE_LAMBDA
            rerank_pool = _mmr_pre_select(docs, lam, MMR_PRE_SELECT)
            logger.info(
                f"[mmr:pre] λ={lam} pool={len(docs)} → rerank 候选={len(rerank_pool)}"
            )
        except Exception as e:
            logger.warning(f"[mmr:pre] 预去重失败，回退完整候选池({len(docs)}): {e}")
            rerank_pool = docs
    elif stage == "pre_lex" and len(docs) > MMR_PRE_SELECT:
        # 零 embedding 成本的词级去重（2026-09-19 H-20260919-08 补充臂）
        try:
            rerank_pool = _lexical_dedup_select(docs, MMR_PRE_SELECT)
            logger.info(
                f"[mmr:pre_lex] Jaccard≥{MMR_LEXICAL_JACCARD} pool={len(docs)} → rerank 候选={len(rerank_pool)}"
            )
        except Exception as e:
            logger.warning(f"[mmr:pre_lex] 词级去重失败，回退完整候选池({len(docs)}): {e}")
            rerank_pool = docs

    # ── 在线重排（cross-encoder）──
    results = online_rerank(query, [doc.text for doc in rerank_pool], top_n=MMR_TOP_CANDIDATES)
    top_docs = []
    for r in results:
        doc = rerank_pool[r["index"]]
        doc.metadata["relevance_score"] = r["relevance_score"]  # 分数落 metadata，filter_node 用
        top_docs.append(doc)

    # ── post 阶段：对精排结果再做多样性选择 ──
    if stage == "post" and len(top_docs) > MMR_TOP_SELECT:
        try:
            lam = mmr_lambda if mmr_lambda is not None else MMR_LAMBDA
            embeddings = _mmr_embed([d.text for d in top_docs])
            selected = _mmr_select(top_docs, embeddings, k=MMR_TOP_SELECT, lambda_=lam)
            logger.info(
                f"[mmr:post] λ={lam} candidates={len(top_docs)} selected={len(selected)} "
                f"ids={[d.id[:8] for d in selected]}"
            )
            return {"reranked_docs": selected}
        except Exception as e:
            logger.warning(f"[mmr:post] 多样性选择失败，回退纯 rerank top{MMR_TOP_SELECT}: {e}")

    # off / MMR 失败：纯 rerank topK
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
    return {"reranked_docs": reranked_docs[:FILTER_FALLBACK_TOP_K]}
