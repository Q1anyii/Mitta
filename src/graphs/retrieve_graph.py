import json
from typing import TYPE_CHECKING, TypedDict, List, Dict, Any, Optional

import redis
from langchain_core.documents import Document
from langchain_core.runnables.config import RunnableConfig
from langgraph.constants import START, END
from langgraph.graph.state import StateGraph
from langgraph.types import Send
from loguru import logger
from pydantic import Field, BaseModel, ConfigDict

from constant.cache_constant import INDEX_NAME, DOC_PREFIX, SPARSE_INDEX_NAME
from service.cache_service import cache_service as _cache_service
from constant.retrieval_constants import TOP_K, DISTANCE_THRESHOLD, REWRITE_PROMPT, RRF_K
from vector.vector_store import VectorStore
from vector.retrieve_doc import RetrievedDoc
import json, re
from init import model, online_rerank


def build_retrieve_graph(vector_store: VectorStore):
    # ← 原 query_rerank_graph 逻辑整体搬入
     # Redis 检索缓存：全局单例（main.py lifespan 统一 open/close），节点内直接使用，不自行管理生命周期
    cache_service = _cache_service
    class OutputState(TypedDict):
        output: List[Document]


    class RAGState(TypedDict):
        question: str
        history: List[Dict[str, str]]  # 多轮对话历史，只用于 query 改写
        rewritten_queries: List[str]  # 改写后的查询列表
        merged_docs: List[RetrievedDoc]  # 多查询召回 + RRF 融合后的候选
        reranked_docs: List[RetrievedDoc]  # 重排后的最终文档（缓存命中时为 dict 恢复的 Document）
        cache_hit: Optional[bool]
        rank_list:  list[list[RetrievedDoc]] # 稠密向量检索结果

    class QueryRewriteResult(BaseModel):
        model_config = ConfigDict(populate_by_name=True)  # 关键配置

        main_query: str = Field(..., alias="主查询")
        sub_queries: List[str] = Field(default_factory=list, alias="子查询")
        keywords: List[str] = Field(default_factory=list, alias="关键词")

    def extract_json(text: str) -> dict:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
            if match:
                return json.loads(match.group(1))
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1:
                return json.loads(text[start:end + 1])
            raise

    def check_cache(state: RAGState, config: RunnableConfig) -> dict:
        question = state["question"]
        thread_id = config["configurable"].get("thread_id", None)
        # 无 thread_id（如评估脚本/离线调用）时跳过缓存，避免 Redis 写入 None 报错
        if not thread_id:
            return {"cache_hit": False}
        query_in_cache = cache_service.query_cache(thread_id, question, 3)
        if query_in_cache:
            logger.success("缓存命中，直接返回")
            return {"reranked_docs": query_in_cache, "cache_hit": True}
        return {"cache_hit": False}

    def store_cache(state: RAGState, config: RunnableConfig) -> dict:
        thread_id = config["configurable"].get("thread_id", None)
        if not thread_id:
            return {}
        if not state.get("cache_hit"):
            # ttl 走 CacheService.store_cache 默认值（15s）；不要对 user_id 调 redis TTL——
            # TTL 只能查已存在 key 的剩余时间，user_id 不是 key，返回 -2 会导致 expire 异常
            cache_service.store_cache(thread_id, state["question"], state["reranked_docs"])
        return {}

    def dense_query(state: RAGState) -> dict:
        origin_query = state["question"]
        rewritten_queries = state["rewritten_queries"]
        all_queries = [origin_query] + list(rewritten_queries)
        rank_list = vector_store.query(all_queries, n_results=20)
        return {
            "rank_list": rank_list
        }

    def bm25_search(state: RAGState, top_k: int = 20) -> dict:
        """
        BM25 稀疏检索，返回 [(doc_id, bm25_score), ...] 按分数降序
        """

        query = state["question"]
        rank_list = state["rank_list"]

        # RedisSearch 查询语法中 : ( ) - @ 等是特殊字符，中文问句直接传会 Syntax error。
        # 用 jieba 分词后空格拼接，去除标点和特殊字符。
        import jieba
        tokens = [t.strip() for t in jieba.lcut(query) if t.strip() and len(t.strip()) > 1]
        safe_query = " ".join(tokens) if tokens else query

        try:
            result = cache_service.redis.execute_command(
                "FT.SEARCH", SPARSE_INDEX_NAME,
                safe_query,
                "NOCONTENT",
                "WITHSCORES",
                "LIMIT", "0", str(top_k),
            )
        except Exception:
            # 索引不存在或查询异常，降级：仅保留稠密检索结果（保持二维结构）
            return {"rank_list": rank_list + [[]]}

        if not isinstance(result, (list, tuple)) or len(result) < 2:
            return {"rank_list": rank_list + [[]]}

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
            except Exception:
                continue
        # 注意：必须用 append/拼接保持二维（list[list[RetrievedDoc]]），
        # 若写成 [rank_list, docs] 会把稠密结果整体包一层成三维，rrf_fusion 遍历时 doc 变成 list 直接 AttributeError
        return {
            "rank_list": rank_list + [docs]
        }

    def rewrite_query(state: RAGState) -> dict:
        history_text = "\n".join(
            f"{m['role']}: {m['content']}" for m in state.get("history", [])
        )

        prompt = REWRITE_PROMPT.format(
            question=state["question"],
            history=history_text or "无",
        )
        logger.info("正在进行Query 改写 + 重排序")

        resp = model.invoke(prompt, response_format={"type": "json_object"})
        raw_json = extract_json(resp.content)
        # 容错：LLM 偶尔返回 {"queries": [...]} 等非预期格式，Pydantic 校验失败时降级处理
        try:
            result = QueryRewriteResult(**raw_json)
            queries = [result.main_query] + result.sub_queries
        except Exception:
            logger.warning(f"Query 改写返回格式异常，尝试兼容解析: {list(raw_json.keys())}")
            if isinstance(raw_json.get("queries"), list) and raw_json["queries"]:
                queries = raw_json["queries"]
            else:
                queries = [state["question"]]

        logger.info(f"重写后问题:{queries}")
        return {"rewritten_queries": queries}

    def rrf_fusion(results: List[List[RetrievedDoc]], k: int = RRF_K) -> List[RetrievedDoc]:
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
        """按文档正文去重，保留排名靠前的那条。"""
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
        # TOP_K 和 DISTANCE_THRESHOLD 已移至 constant/retrieval_constants.py 统一管理

        rank_list = state["rank_list"]
        merged_docs = rrf_fusion(rank_list)
        merged_docs = dedup_by_text(merged_docs)
        return {"merged_docs": merged_docs}

       # rerank 中把分数写回 doc（filter 的前提）
    def rerank(state: RAGState) -> dict:
        docs = state["merged_docs"]
        if not docs:
            return {"reranked_docs": []}
        query = state["rewritten_queries"][0]
        results = online_rerank(query, [doc.text for doc in docs], top_n=5)
        top_docs = []
        for r in results:
            doc = docs[r["index"]]
            doc.metadata["relevance_score"] = r["relevance_score"]  # 分数落 metadata
            top_docs.append(doc)
        return {"reranked_docs": top_docs}

    def filter_node(state: RAGState) -> dict:
        """按重排分数过滤低相关文档（relevance_score 越高越相关）。"""
        reranked_docs = state["reranked_docs"]

        # 阈值 0.3：过滤噪声，保留中等相关以上文档
        finally_docs = [
            doc for doc in reranked_docs
            if doc.metadata.get("relevance_score", 0.0) >= 0.3
        ]

        if finally_docs:
            # ✅ 返回过滤后的结果（最多 5 条）
            return {"reranked_docs": finally_docs[:5]}

        # 兜底：过滤后为空时，返回原始 top 3（宁可不准确也不返回空）
        return {"reranked_docs": reranked_docs[:3]}                 # 空则空，不再回退

    def output_node(state: RAGState) -> dict:
        return {"output": state["reranked_docs"]}

    builder = StateGraph(state_schema=RAGState, output_schema=OutputState)

    builder.add_node("dense_query", dense_query)
    builder.add_node("bm25_search", bm25_search)
    builder.add_node("check_cache", check_cache)
    builder.add_node("store_cache", store_cache)
    builder.add_node("rewrite", rewrite_query)
    builder.add_node("retrieve", retrieve)
    builder.add_node("rerank", rerank)
    builder.add_node("filter", filter_node)
    builder.add_node("output_node", output_node)

    builder.add_edge(START, "check_cache")
    builder.add_conditional_edges(
        "check_cache",
        lambda state: "hit" if state.get("cache_hit") else "miss",
        {
            "hit": "output_node",
            "miss": "rewrite",
        },
    )
    builder.add_edge("rewrite", "dense_query")
    builder.add_edge("dense_query", "bm25_search")
    builder.add_edge("bm25_search", "retrieve")
    builder.add_edge("retrieve", "rerank")
    builder.add_edge("rerank", "store_cache")
    builder.add_edge("rerank", "filter")
    builder.add_edge("filter", "output_node")
    builder.add_edge("output_node", END)

    rerank_graph = builder.compile()
    return rerank_graph