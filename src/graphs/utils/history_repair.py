"""历史消息清洗工具：双向清洗 tool_calls / ToolMessage 配对。

拆分自原 main_graph.py 的 _repair_history 闭包函数。
纯函数，无外部依赖。
"""

from langchain_core.messages import AIMessage, ToolMessage
from loguru import logger


def _repair_history(history: list) -> list:
    """双向清洗历史，保证 tool_calls / ToolMessage 配对完整。

    OpenAI 兼容 API 同时校验两个方向：
    1. 带 tool_calls 的 assistant 消息必须被 ToolMessage 响应（悬空调用 400）；
    2. tool 消息必须是对前置 tool_calls 的响应（孤儿 ToolMessage 同样 400）。
    中断残留（AIMessage 已落 checkpoint、工具未执行）与 tool_node 缓存复用旧调用 id
    都会破坏配对，透传前必须双向清洗。

    Args:
        history: 历史消息列表

    Returns:
        清洗后的消息列表
    """
    declared_ids = {
        tc.get("id")
        for m in history
        if isinstance(m, AIMessage) and m.tool_calls
        for tc in m.tool_calls
    }
    responded_ids = {
        m.tool_call_id
        for m in history
        if isinstance(m, ToolMessage) and m.tool_call_id
    }
    repaired = []
    for m in history:
        if isinstance(m, AIMessage) and m.tool_calls:
            missing = [tc for tc in m.tool_calls if tc.get("id") not in responded_ids]
            if missing:
                logger.warning(f"清洗悬空 tool_calls：{missing} 无对应 ToolMessage，已剥离")
                m = m.model_copy(update={
                    "tool_calls": [tc for tc in m.tool_calls if tc.get("id") in responded_ids]
                })
        elif isinstance(m, ToolMessage) and m.tool_call_id not in declared_ids:
            logger.warning(f"清洗孤儿 ToolMessage（tool_call_id={m.tool_call_id} 无前置声明），已剥离")
            continue
        repaired.append(m)
    return repaired
