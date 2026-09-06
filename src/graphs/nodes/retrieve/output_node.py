"""输出节点：将最终检索结果包装为 OutputState 格式返回。

拆分自原 retrieve_graph.py 的 output_node 闭包函数。
纯函数，无外部依赖。
"""

from graphs.state import RAGState


def output_node(state: RAGState) -> dict:
    """输出节点：将最终检索结果包装为 OutputState 格式返回。

    Args:
        state: 含 reranked_docs（最终文档列表）

    Returns:
        {"output": [Document, ...]} — 符合 OutputState schema
    """
    return {"output": state["reranked_docs"]}
