"""
检索图（Retrieve Graph）：RAG 检索子系统的 LangGraph 编排层。

只负责组装：创建依赖 → partial 绑定 → add_node → add_edge → compile。
所有节点逻辑在 graphs/nodes/retrieve/ 下，状态在 graphs/state.py。

整体流程（缓存未命中时）：
    START → check_cache → rewrite → dense_query → bm25_search → retrieve → rerank
                                                                          ↓
                                                              store_cache + filter → output_node → END

缓存命中时：
    START → check_cache → output_node → END（跳过全部检索/重排，直接返回缓存结果）
"""

from functools import partial

from langchain_core.runnables.config import RunnableConfig
from langgraph.constants import START, END
from langgraph.graph.state import StateGraph
from loguru import logger

from graphs.state import RAGState, OutputState
from graphs.nodes.retrieve.cache_nodes import check_cache, store_cache
from graphs.nodes.retrieve.query_nodes import dense_query, bm25_search, rewrite_query
from graphs.nodes.retrieve.fusion_nodes import retrieve, rerank, filter_node
from graphs.nodes.retrieve.output_node import output_node
from service.cache_service import cache_service as _cache_service
from vector.vector_store import VectorStore


def build_retrieve_graph(vector_store: VectorStore, model=None, online_rerank=None):
    """构建并编译检索图。

    所有节点函数通过 functools.partial 绑定依赖后注册到图，
    节点本身是纯函数（定义在 graphs/nodes/retrieve/），可单独单元测试。

    Args:
        vector_store: 向量存储实例（ChromaDB），用于稠密向量检索
        model: LLM 实例（依赖注入，用于 Query 改写）
        online_rerank: 在线重排函数（依赖注入）

    Returns:
        编译后的 LangGraph 可调用对象，invoke 时传入 {"question": str, "history": list}。
    """
    # Redis 检索缓存：全局单例（main.py lifespan 统一 open/close），节点内直接使用
    cache_service = _cache_service
    # 兼容旧调用：未注入 model/online_rerank 时延迟导入（双轨运行期）
    if model is None or online_rerank is None:
        from init import model as _model, online_rerank as _rerank
        model = model or _model
        online_rerank = online_rerank or _rerank

    # ── 用 partial 绑定依赖，得到符合 LangGraph 节点签名的函数 ──
    check_cache_bound = partial(check_cache, cache_service=cache_service)
    store_cache_bound = partial(store_cache, cache_service=cache_service)
    dense_query_bound = partial(dense_query, vector_store=vector_store)
    bm25_search_bound = partial(bm25_search, cache_service=cache_service)
    rewrite_query_bound = partial(rewrite_query, model=model)
    rerank_bound = partial(rerank, online_rerank=online_rerank)

    # ── 图构建 ──
    builder = StateGraph(state_schema=RAGState, output_schema=OutputState)

    # 注册所有节点
    builder.add_node("dense_query", dense_query_bound)
    builder.add_node("bm25_search", bm25_search_bound)
    builder.add_node("check_cache", check_cache_bound)
    builder.add_node("store_cache", store_cache_bound)
    builder.add_node("rewrite", rewrite_query_bound)
    builder.add_node("retrieve", retrieve)          # 纯函数，无需绑定
    builder.add_node("rerank", rerank_bound)
    builder.add_node("filter", filter_node)         # 纯函数，无需绑定
    builder.add_node("output_node", output_node)    # 纯函数，无需绑定

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
