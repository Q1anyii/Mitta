"""条件路由函数：classify_node 之后和 llm_node 之后的分支决策。

拆分自原 main_graph.py 的 route 和 route_after_llm 闭包函数。
纯函数，无外部依赖。
"""

from langgraph.types import Send
from loguru import logger

from graphs.state import OverAllState


def route(state: OverAllState) -> str:
    """
    if else条件不必采用Send动态删除，但后续升级架构（Supervisor可重构为Send）

    """
    if state.get("needs_retrieval"):
        return "retrieve_node"
    return "llm_node"


def route_after_llm(state: OverAllState) -> str:
    """llm_node 之后：有工具调用则执行 ToolNode，否则进入记忆节点收尾。

    memory_node 保留在主图内（H-20260920-01 二轮方案：节点内部 fire-and-forget，
    快速返回），无工具分支照旧路由到 memory_node。

    Args:
        state: 当前图状态

    Returns:
        "tool_node" 或 "memory_node"
    """
    last = state["messages"][-1]
    target = "tool_node" if getattr(last, "tool_calls", None) else "memory_node"
    logger.info(f"llm_node 路由：{target}（last={type(last).__name__}）")
    return target
