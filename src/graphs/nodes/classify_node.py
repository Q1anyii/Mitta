"""分类节点：判断本轮问题是否需要知识库检索。

拆分自原 main_graph.py 的 classify_node 闭包函数。
依赖：model（LLM 实例），通过参数注入。
"""

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger

from constant.prompt_constants import CLASSIFIER_PROMPT
from graphs.state import OverAllState


def classify_node(state: OverAllState, model) -> OverAllState:
    """判断本轮问题是否需要知识库检索（仅在需要时走 retrieval_node）。

    Args:
        state: 当前图状态，含 input_str
        model: LLM 实例（依赖注入）

    Returns:
        {"needs_retrieval": bool}
    """
    response = model.invoke([
        SystemMessage(content=CLASSIFIER_PROMPT),
        HumanMessage(content=state["input_str"]),
    ])
    needs_retrieval = response.content.strip().lower().startswith("yes")
    logger.info(f"分类结果（needs_retrieval={needs_retrieval}）：{state['input_str'][:50]}")
    return {"needs_retrieval": needs_retrieval}
