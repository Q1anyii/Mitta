"""融合与重排节点：RRF 融合 + 去重 + 在线重排 + 阈值过滤。

拆分自原 retrieve_graph.py 的 rrf_fusion、dedup_by_text、retrieve、rerank、filter_node。
依赖：online_rerank（在线重排函数），通过参数注入。
"""

from loguru import logger

from constant.retrieval_constants import RRF_K
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


def rerank(state: RAGState, online_rerank) -> dict:
    """在线重排：用 SiliconFlow bge-reranker-v2-m3 API 对候选文档重新排序。

    重排是 RAG 质量的关键：向量检索只保证语义粗召回，
    重排模型用交叉编码器（Cross-Encoder）精确计算 query-doc 相关性，
    显著提升 top-k 准确率。

    Args:
        state: 含 merged_docs（候选文档）和 rewritten_queries（用主查询做重排）
        online_rerank: 在线重排函数（依赖注入）

    Returns:
        {"reranked_docs": [重排后的 top 5 文档，metadata 含 relevance_score]}
    """
    docs = state["merged_docs"]
    if not docs:
        return {"reranked_docs": []}
    # 用改写后的主查询做重排（比原始问题更精确）
    query = state["rewritten_queries"][0]
    # online_rerank 内部调用 SiliconFlow API，返回 [{"index": int, "relevance_score": float}, ...]
    results = online_rerank(query, [doc.text for doc in docs], top_n=5)
    top_docs = []
    for r in results:
        doc = docs[r["index"]]
        doc.metadata["relevance_score"] = r["relevance_score"]  # 分数落 metadata，filter_node 用
        top_docs.append(doc)
    return {"reranked_docs": top_docs}


def filter_node(state: RAGState) -> dict:
    """按重排分数过滤低相关文档。

    阈值 0.3：过滤噪声，保留中等相关以上文档。
    过滤后为空时兜底返回原始 top 3（宁可不准确也不返回空，避免 LLM 无上下文可用）。

    Args:
        state: 含 reranked_docs（重排后的文档，metadata 含 relevance_score）

    Returns:
        {"reranked_docs": [过滤后的文档，最多 5 条]}
    """
    reranked_docs = state["reranked_docs"]

    # 阈值 0.3：过滤噪声，保留中等相关以上文档
    finally_docs = [
        doc for doc in reranked_docs
        if doc.metadata.get("relevance_score", 0.0) >= 0.3
    ]

    if finally_docs:
        # 返回过滤后的结果（最多 5 条）
        return {"reranked_docs": finally_docs[:5]}

    # 兜底：过滤后为空时，返回原始 top 3（宁可不准确也不返回空）
    return {"reranked_docs": reranked_docs[:3]}
