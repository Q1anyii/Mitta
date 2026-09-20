"""条件路由函数：classify_node 之后和 llm_node 之后的分支决策。

拆分自原 main_graph.py 的 route 和 route_after_llm 闭包函数。
纯函数，无外部依赖。
"""

from langgraph.constants import END
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
        # Send 任务不继承父 state，persona 必须显式传递：
        # 否则 llm_node 里 state.get("persona") 为 None → 兜底 cappie，
        # 手选 crazy/kind/manager 全部答成帽子米塔（H-20260919-09 根因）
        "persona": state.get("persona"),
    }
    if state.get("needs_retrieval"):
        return [Send("retrieve_node", payload)]
    return [Send("llm_node", payload)]


def route_after_llm(state: OverAllState) -> str:
    """llm_node 之后：有工具调用则执行 ToolNode，否则直接 END（正文流完）。

    memory_node 自 H-20260920-01 起移出主图（长期记忆提取改由 chat_service
    在图 stream 结束后后台执行），因此无工具分支不再路由到记忆节点，直接 END。

    Args:
        state: 当前图状态

    Returns:
        "tool_node" 或 END
    """
    last = state["messages"][-1]
    target = "tool_node" if getattr(last, "tool_calls", None) else END
    logger.info(f"llm_node 路由：{target}（last={type(last).__name__}）")
    return target
