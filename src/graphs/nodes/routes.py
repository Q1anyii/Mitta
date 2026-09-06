"""条件路由函数：classify_node 之后和 llm_node 之后的分支决策。

拆分自原 main_graph.py 的 route 和 route_after_llm 闭包函数。
纯函数，无外部依赖。
"""

from langgraph.types import Send
from loguru import logger

from graphs.state import OverAllState


def route(state: OverAllState) -> list[Send]:
    """classify_node 之后：需要检索才 Send 到 retrieve_node，否则直接 Send 到 llm_node。

    Send 任务不会继承父 state，必须把节点所需的数据显式放进 payload。

    Args:
        state: 当前图状态

    Returns:
        [Send("retrieve_node", payload)] 或 [Send("llm_node", payload)]
    """
    payload = {
        "input_str": state["input_str"],
        "messages": state.get("messages", []),  # 历史对话（短期记忆）
    }
    if state.get("needs_retrieval"):
        return [Send("retrieve_node", payload)]
    return [Send("llm_node", payload)]


def route_after_llm(state: OverAllState) -> str:
    """llm_node 之后：有工具调用则执行 ToolNode，否则进入记忆节点收尾。

    Args:
        state: 当前图状态

    Returns:
        "tool_node" 或 "memory_node"
    """
    last = state["messages"][-1]
    target = "tool_node" if getattr(last, "tool_calls", None) else "memory_node"
    logger.info(f"llm_node 路由：{target}（last={type(last).__name__}）")
    return target
