"""
检索图（Retrieve Graph）：RAG 检索子系统的 LangGraph 编排。

整体流程（缓存未命中时）：
    START → check_cache → rewrite → dense_query → bm25_search → retrieve → rerank
                                                                          ↓
                                                              store_cache + filter → output_node → END

缓存命中时：
    START → check_cache → output_node → END（跳过全部检索/重排，直接返回缓存结果）

核心设计：
1. 双路召回：稠密向量检索（ChromaDB，语义匹配）+ BM25 稀疏检索（RedisSearch，关键词匹配），
   两路结果通过 RRF（Reciprocal Rank Fusion）融合，兼顾语义相关性和关键词精确匹配。
2. Query 改写：用 LLM 将用户原始问题 + 多轮历史改写为主查询 + 子查询 + 关键词，
   解决多轮对话中的指代消解和查询扩展问题。
3. 在线重排：融合后的候选文档用 SiliconFlow bge-reranker-v2-m3 在线 API 重排，
   按相关性分数降序，再过滤低相关文档。
4. 检索缓存：Redis + RediSearch 向量索引，按 thread_id + LSH 语义桶存储，
   命中时用重排模型验证语义等价性（防止同义问题误命中），TTL 默认 15 秒。
5. 缓存是优化而非正确性依赖：Redis 不可用/超时全部降级为未命中，不阻塞主链路。
"""

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
    """构建并编译检索图。

    Args:
        vector_store: 向量存储实例（ChromaDB），用于稠密向量检索。

    Returns:
        编译后的 LangGraph 可调用对象，invoke 时传入 {"question": str, "history": list}。
    """
    # Redis 检索缓存：全局单例（main.py lifespan 统一 open/close），节点内直接使用，不自行管理生命周期
    cache_service = _cache_service

    # ── 状态定义 ──────────────────────────────────────────────
    class OutputState(TypedDict):
        """图的输出 schema：最终返回给 main_graph 的检索结果（Document 列表）。"""
        output: List[Document]

    class RAGState(TypedDict):
        """检索图的完整状态，在各节点间传递。"""
        question: str                          # 用户原始问题
        history: List[Dict[str, str]]          # 多轮对话历史，只用于 query 改写
        rewritten_queries: List[str]           # LLM 改写后的查询列表（主查询 + 子查询）
        merged_docs: List[RetrievedDoc]        # 多查询召回 + RRF 融合 + 去重后的候选文档
        reranked_docs: List[RetrievedDoc]      # 在线重排后的最终文档（缓存命中时为 dict 恢复的 Document）
        cache_hit: Optional[bool]              # 检索缓存是否命中（None 表示未检查）
        rank_list: list[list[RetrievedDoc]]    # 各查询的稠密检索结果（二维：每个查询一个列表）+ BM25 结果

    class QueryRewriteResult(BaseModel):
        """LLM Query 改写结果的 Pydantic 模型。

        使用 alias 映射中文键名：LLM 按 REWRITE_PROMPT 输出中文 JSON，
        Pydantic 通过 populate_by_name 自动映射到英文字段。
        """
        model_config = ConfigDict(populate_by_name=True)  # 允许用 alias（中文键）填充

        main_query: str = Field(..., alias="主查询")
        sub_queries: List[str] = Field(default_factory=list, alias="子查询")
        keywords: List[str] = Field(default_factory=list, alias="关键词")

    # ── 工具函数 ──────────────────────────────────────────────
    def extract_json(text: str) -> dict:
        """从 LLM 输出中提取 JSON 对象，兼容三种格式：
        1. 纯 JSON 字符串
        2. ```json ... ``` 代码块包裹
        3. 文本中夹杂 JSON（取第一个 { 到最后一个 }）

        Args:
            text: LLM 原始输出文本。

        Returns:
            解析后的 dict。

        Raises:
            json.JSONDecodeError: 三种格式都无法解析时抛出。
        """
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

    # ── 节点函数 ──────────────────────────────────────────────
    def check_cache(state: RAGState, config: RunnableConfig) -> dict:
        """检查检索缓存是否命中。

        缓存键 = thread_id + LSH 语义桶（query 向量的随机投影哈希），
        命中后还需用在线重排模型验证候选问题与当前问题语义等价（防止同义问题误命中）。

        Args:
            state: 当前图状态，含 question。
            config: LangGraph 配置，含 configurable.thread_id。

        Returns:
            命中时返回 {"reranked_docs": [...], "cache_hit": True}；
            未命中返回 {"cache_hit": False}。
        """
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
        """将本次检索结果写入缓存（仅缓存未命中时执行）。

        缓存 TTL 走 CacheService.store_cache 默认值（15 秒），
        适合短时间内重复提问同一问题的场景（如用户连续发送相同消息）。

        Args:
            state: 当前图状态，含 question 和 reranked_docs。
            config: 含 thread_id。

        Returns:
            空 dict（不修改状态）。
        """
        thread_id = config["configurable"].get("thread_id", None)
        if not thread_id:
            return {}
        if not state.get("cache_hit"):
            # ttl 走 CacheService.store_cache 默认值（15s）；不要对 user_id 调 redis TTL——
            # TTL 只能查已存在 key 的剩余时间，user_id 不是 key，返回 -2 会导致 expire 异常
            cache_service.store_cache(thread_id, state["question"], state["reranked_docs"])
        return {}

    def dense_query(state: RAGState) -> dict:
        """稠密向量检索：用原始问题 + 改写后的所有子查询分别检索 ChromaDB。

        每个查询召回 top 20，结果保持二维结构（list[list[RetrievedDoc]]），
        后续 rrf_fusion 按列表遍历做排名融合。

        Args:
            state: 含 question 和 rewritten_queries。

        Returns:
            {"rank_list": [[doc1, doc2, ...], [...], ...]} — 每个查询的结果列表。
        """
        origin_query = state["question"]
        rewritten_queries = state["rewritten_queries"]
        # 原始问题 + 所有改写查询分别检索，增加召回率
        all_queries = [origin_query] + list(rewritten_queries)
        rank_list = vector_store.query(all_queries, n_results=20)
        return {
            "rank_list": rank_list
        }

    def bm25_search(state: RAGState, top_k: int = 20) -> dict:
        """BM25 稀疏检索：用 RedisSearch 对知识库全文做关键词匹配。

        与稠密向量检索互补：向量检索擅长语义匹配（"如何修电脑" ≈ "电脑维修方法"），
        BM25 擅长精确关键词匹配（专有名词、错误码、型号等）。

        Args:
            state: 含 question 和 rank_list（稠密检索结果）。
            top_k: BM25 返回的最大文档数。

        Returns:
            {"rank_list": 原稠密结果 + [BM25 结果列表]} — 追加到二维列表末尾。
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
                "NOCONTENT",      # 不返回文档内容，只返回 doc_id 和分数
                "WITHSCORES",     # 返回 BM25 分数
                "LIMIT", "0", str(top_k),
            )
        except Exception:
            # 索引不存在或查询异常，降级：仅保留稠密检索结果（保持二维结构）
            return {"rank_list": rank_list + [[]]}

        if not isinstance(result, (list, tuple)) or len(result) < 2:
            return {"rank_list": rank_list + [[]]}

        docs = []
        # FT.SEARCH 返回格式：[总数, doc_id1, score1, doc_id2, score2, ...]
        # 从索引 1 开始，步长 2 遍历 (doc_id, score) 对
        for i in range(1, len(result) - 1, 2):
            try:
                raw_id = result[i]
                if isinstance(raw_id, bytes):
                    raw_id = raw_id.decode("utf-8")
                doc_id = raw_id.replace(DOC_PREFIX, "")  # 去掉文档前缀，得到纯 id
                score = float(result[i + 1])
                # 从 Redis Hash 中读取文档正文（BM25 索引只存索引，内容存在 HASH 中）
                content = cache_service.redis.hget(raw_id, "content")
                text = content.decode("utf-8") if isinstance(content, bytes) else (content or "")
                docs.append(RetrievedDoc(
                    id=doc_id,
                    text=text,
                    distance=0.0,  # BM25 没有向量距离，用 0 占位，实际分数存 metadata
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
        """Query 改写：用 LLM 将原始问题 + 多轮历史改写为结构化查询。

        输出格式（中文 JSON，由 QueryRewriteResult 映射）：
        {
            "主查询": "改写后的核心问题",
            "子查询": ["扩展查询1", "扩展查询2"],
            "关键词": ["关键词1", "关键词2"]
        }

        解决的问题：
        - 指代消解："它怎么样" → "M1 MacBook Pro 性能怎么样"
        - 查询扩展："怎么学 Python" → ["Python 入门教程", "Python 学习路线", "Python 实战项目"]

        Args:
            state: 含 question 和 history。

        Returns:
            {"rewritten_queries": [主查询, 子查询1, 子查询2, ...]}
        """
        history_text = "\n".join(
            f"{m['role']}: {m['content']}" for m in state.get("history", [])
        )

        prompt = REWRITE_PROMPT.format(
            question=state["question"],
            history=history_text or "无",
        )
        logger.info("正在进行Query 改写 + 重排序")

        # response_format={"type": "json_object"} 要求模型返回合法 JSON（DeepSeek 支持）
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
                # 最终兜底：用原始问题，不阻塞检索
                queries = [state["question"]]

        logger.info(f"重写后问题:{queries}")
        return {"rewritten_queries": queries}

    def rrf_fusion(results: List[List[RetrievedDoc]], k: int = RRF_K) -> List[RetrievedDoc]:
        """RRF（Reciprocal Rank Fusion）排名融合：将多路检索结果合并为单一排序列表。

        公式：score(doc) = Σ 1 / (k + rank_i + 1)
        其中 rank_i 是文档在第 i 路结果中的排名（从 0 开始）。

        RRF 的优势：不需要归一化不同检索方式的分数（向量距离 vs BM25 分数量纲不同），
        只依赖排名，鲁棒性强。k=60 是业界常用值，平衡排名权重。

        Args:
            results: 二维列表，每个子列表是一路检索的结果（已按相关性排序）。
            k: RRF 常数，默认 RRF_K（60）。

        Returns:
            按融合分数降序排列的文档列表（去重，同一文档只保留分数最高的实例）。
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
            docs: RRF 融合后的文档列表（已排序）。

        Returns:
            去重后的文档列表。
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
            state: 含 rank_list（二维检索结果）。

        Returns:
            {"merged_docs": [融合去重后的候选文档]}
        """
        # TOP_K 和 DISTANCE_THRESHOLD 已移至 constant/retrieval_constants.py 统一管理
        rank_list = state["rank_list"]
        merged_docs = rrf_fusion(rank_list)
        merged_docs = dedup_by_text(merged_docs)
        return {"merged_docs": merged_docs}

    # rerank 中把分数写回 doc（filter 的前提）
    def rerank(state: RAGState) -> dict:
        """在线重排：用 SiliconFlow bge-reranker-v2-m3 API 对候选文档重新排序。

        重排是 RAG 质量的关键：向量检索只保证语义粗召回，
        重排模型用交叉编码器（Cross-Encoder）精确计算 query-doc 相关性，
        显著提升 top-k 准确率。

        Args:
            state: 含 merged_docs（候选文档）和 rewritten_queries（用主查询做重排）。

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
            state: 含 reranked_docs（重排后的文档，metadata 含 relevance_score）。

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
            # ✅ 返回过滤后的结果（最多 5 条）
            return {"reranked_docs": finally_docs[:5]}

        # 兜底：过滤后为空时，返回原始 top 3（宁可不准确也不返回空）
        return {"reranked_docs": reranked_docs[:3]}                 # 空则空，不再回退

    def output_node(state: RAGState) -> dict:
        """输出节点：将最终检索结果包装为 OutputState 格式返回。

        Args:
            state: 含 reranked_docs（最终文档列表）。

        Returns:
            {"output": [Document, ...]} — 符合 OutputState schema。
        """
        return {"output": state["reranked_docs"]}

    # ── 图构建 ──────────────────────────────────────────────
    builder = StateGraph(state_schema=RAGState, output_schema=OutputState)

    # 注册所有节点
    builder.add_node("dense_query", dense_query)
    builder.add_node("bm25_search", bm25_search)
    builder.add_node("check_cache", check_cache)
    builder.add_node("store_cache", store_cache)
    builder.add_node("rewrite", rewrite_query)
    builder.add_node("retrieve", retrieve)
    builder.add_node("rerank", rerank)
    builder.add_node("filter", filter_node)
    builder.add_node("output_node", output_node)

    # 边：START → check_cache（入口先查缓存）
    builder.add_edge(START, "check_cache")
    # 条件边：缓存命中直接跳 output_node，未命中走完整检索流程
    builder.add_conditional_edges(
        "check_cache",
        lambda state: "hit" if state.get("cache_hit") else "miss",
        {
            "hit": "output_node",
            "miss": "rewrite",
        },
    )
    # 完整检索链路：rewrite → dense → bm25 → 融合 → 重排
    builder.add_edge("rewrite", "dense_query")
    builder.add_edge("dense_query", "bm25_search")
    builder.add_edge("bm25_search", "retrieve")
    builder.add_edge("retrieve", "rerank")
    # 重排后并行：store_cache（写缓存）+ filter（过滤）
    # LangGraph 中同一节点的多条出边会并行执行
    builder.add_edge("rerank", "store_cache")
    builder.add_edge("rerank", "filter")
    # filter → output_node → END
    builder.add_edge("filter", "output_node")
    builder.add_edge("output_node", END)

    # 编译图（检索图不需要 checkpointer/store/cache，无状态）
    rerank_graph = builder.compile()
    return rerank_graph
