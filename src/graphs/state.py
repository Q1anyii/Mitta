"""图状态定义：main_graph 和 retrieve_graph 共享的 State schema。

拆分自原 main_graph.py / retrieve_graph.py 中的内部类，
所有节点函数 import 同一个类型，避免闭包内定义导致无法单独测试。
"""

from typing import Annotated, Any, List, Dict, Optional, TypedDict

from langchain_core.documents import Document
from langgraph.graph.message import MessagesState
from pydantic import BaseModel, ConfigDict, Field


# ═══════════════════════════════════════════════════════════════
# main_graph 状态
# ═══════════════════════════════════════════════════════════════

class OverAllState(MessagesState):
    """主对话图的完整状态，在 classify/retrieve/llm/tool/memory 节点间传递。

    继承 MessagesState：messages 字段由 add_messages reducer 自动合并（LangGraph 内置），
    节点返回 {"messages": [msg]} 时会追加而非覆盖。
    """
    input_str: Annotated[str, Field(description="用户输入")]
    retrieve_res: Annotated[
        Optional[list[Any] | dict[str, Any] | Any],
        "检索结果（retrieve_graph 返回，Document 已转 dict 序列化）",
    ] = None
    needs_retrieval: Annotated[bool, Field(description="是否需要知识库检索")] = False
    tool_status: Annotated[str, Field(
        description="本轮工具状态：executed=工具被执行；unavailable=无工具可用；idle=筛选出工具但模型未调用"
    )] = "idle"


# ═══════════════════════════════════════════════════════════════
# retrieve_graph 状态
# ═══════════════════════════════════════════════════════════════

class OutputState(TypedDict):
    """检索图的输出 schema：最终返回给 main_graph 的检索结果（Document 列表）。"""
    output: List[Document]


class RAGState(TypedDict):
    """检索图的完整状态，在 check_cache/rewrite/dense/bm25/retrieve/rerank/filter 节点间传递。"""
    question: str                          # 用户原始问题
    history: List[Dict[str, str]]          # 多轮对话历史，只用于 query 改写
    rewritten_queries: List[str]           # LLM 改写后的查询列表（主查询 + 子查询）
    merged_docs: List[Any]                 # 多查询召回 + RRF 融合 + 去重后的候选文档
    reranked_docs: List[Any]               # 在线重排后的最终文档（缓存命中时为 dict 恢复的 Document）
    cache_hit: Optional[bool]              # 检索缓存是否命中（None 表示未检查）
    rank_list: list[list[Any]]             # 各查询的稠密检索结果（二维）+ BM25 结果


class QueryRewriteResult(BaseModel):
    """LLM Query 改写结果的 Pydantic 模型。

    使用 alias 映射中文键名：LLM 按 REWRITE_PROMPT 输出中文 JSON，
    Pydantic 通过 populate_by_name 自动映射到英文字段。
    """
    model_config = ConfigDict(populate_by_name=True)  # 允许用 alias（中文键）填充

    main_query: str = Field(..., alias="主查询")
    sub_queries: List[str] = Field(default_factory=list, alias="子查询")
    keywords: List[str] = Field(default_factory=list, alias="关键词")
