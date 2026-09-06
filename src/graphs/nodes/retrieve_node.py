"""检索节点：调用 retrieve_graph 执行知识库检索。

拆分自原 main_graph.py 的 retrieve_node 闭包函数。
依赖：retrieve_graph（编译后的检索图），通过参数注入。
"""

from loguru import logger

from graphs.state import OverAllState
from utils.doc_util import documents_to_dicts


def retrieve_node(state: OverAllState, retrieve_graph) -> OverAllState:
    """调用检索图获取知识库相关文档。

    Document 无法被 checkpointer 正确反序列化：恢复会话时会被还原成 dict，
    导致 llm_node 里 doc.page_content 报 AttributeError。
    统一在入 state 前转成 dict，llm_node 侧兼容两种形态读取。

    Args:
        state: 当前图状态，含 input_str 和 messages（历史对话）
        retrieve_graph: 编译后的检索图（依赖注入）

    Returns:
        {"retrieve_res": 检索结果（Document 已转 dict）}
    """
    input_str = state["input_str"]
    logger.info(f"执行知识库检索：{input_str}")

    history = [
        {"role": "user" if m.type == "human" else "assistant", "content": m.content}
        for m in state.get("messages", [])
        if m.type in ("human", "ai")
    ]

    retrieve_res = retrieve_graph.invoke({
        "question": input_str,
        "history": history,
    })

    # Document → dict 序列化（见函数 docstring）
    output = retrieve_res.get("output", [])
    if output and (hasattr(output[0], "page_content") or hasattr(output[0], "text")):
        retrieve_res["output"] = documents_to_dicts(output)

    return {"retrieve_res": retrieve_res}
