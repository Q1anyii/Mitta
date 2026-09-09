"""历史消息清洗工具：滑动窗口裁剪 + 双向清洗 tool_calls / ToolMessage 配对。

拆分自原 main_graph.py 的 _repair_history 闭包函数。
纯函数，无外部依赖。
"""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from loguru import logger

# 默认窗口大小：最多保留的最近消息条数（约 6 轮对话，含工具链消息）。
# llm_node 每轮把整个 history 全量传入 LLM，消息随轮数线性增长、token 平方级膨胀；
# 超出窗口的早期消息直接丢弃（窗口足够容纳最近几轮的上下文与指代）。
DEFAULT_MAX_HISTORY_MESSAGES = 30


def _trim_history(history: list, max_messages: int = DEFAULT_MAX_HISTORY_MESSAGES) -> list:
    """滑动窗口裁剪历史：只保留最近 max_messages 条消息，控制单次请求 token 消耗。

    裁剪必须保证 tool_calls / ToolMessage 配对完整：
    - 若截断后窗口开头是 ToolMessage（其前置 AIMessage 被裁掉），丢弃该 ToolMessage——
      保留一个孤儿 ToolMessage 会在透传时被 API 400 拒绝；
    - 若窗口末尾是带 tool_calls 的 AIMessage（其 ToolMessage 被裁掉），属于悬空调用，
      同样需要剥离 tool_calls（由 _repair_history 兜底处理，这里不重复剥离）。

    Args:
        history: 完整历史消息列表（LangChain BaseMessage 列表）
        max_messages: 窗口大小（消息条数）

    Returns:
        裁剪后的消息列表
    """
    if not history:
        return []
    if len(history) <= max_messages:
        return list(history)

    trimmed = list(history[-max_messages:])
    # 窗口开头若是 ToolMessage（无前置 AIMessage 上下文），丢弃避免孤儿 ToolMessage 400
    while trimmed and isinstance(trimmed[0], ToolMessage):
        logger.warning("窗口裁剪：丢弃窗口开头的孤儿 ToolMessage（其前置 AIMessage 已被裁出窗口）")
        trimmed.pop(0)
    # 窗口末尾是带 tool_calls 的 AIMessage 时，其 ToolMessage 可能刚好被裁出窗口，
    # 这里不剥离 tool_calls（_repair_history 会统一处理悬空调用），只记录日志
    if trimmed and isinstance(trimmed[-1], AIMessage) and trimmed[-1].tool_calls:
        logger.warning(
            f"窗口裁剪：窗口末尾 AIMessage 仍带 tool_calls（{len(trimmed[-1].tool_calls)} 个），"
            f"其 ToolMessage 可能已被裁出窗口，交由 _repair_history 剥离悬空调用"
        )
    logger.info(f"历史消息窗口裁剪：{len(history)} -> {len(trimmed)}（max_messages={max_messages}）")
    return trimmed


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
