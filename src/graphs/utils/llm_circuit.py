"""工具循环防护：次数上限、重复调用检测、失败熔断。

纯函数模块，无外部依赖，可独立测试。llm_node 只负责编排，
把"本轮该不该继续调工具"的判定全部委托到这里。
"""

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

# 检索资料最大文档数：超出丢弃（优先保留最相关的排前文档）
MAX_RETRIEVAL_DOCS = 8
# 单篇检索文档最大字符数：超出截断，防止超长文档撑爆单次请求 token
MAX_DOC_CHARS = 2000
# 单轮请求内工具调用次数上限（per-turn，不是 per-session）
MAX_TOOL_ROUNDS = 8
# 同一工具连续失败几次后，本轮内禁止再调该工具
MAX_TOOL_FAILURES = 2
# 工具名（含 server 前缀/别名）出现在 query 里时，视为用户/业务显式点名要用的工具
_SOFT_BAN_EXEMPT = frozenset()

# 工具失败信号：ToolMessage.status == "error"，或内容以既有错误前缀开头
_TOOL_ERROR_PREFIXES = (
    "工具执行失败",
    "工具参数错误",
    "抓取失败",
    "搜索失败",
    "读取失败",
    "git 命令",
)
# 循环检测/次数上限的提示语——由本节点注入，不能被当成工具失败信号
_NODE_NOTICE_MARKERS = ("本轮请求中工具调用次数已达上限", "连续多次调用同一工具")


def turn_anchor(history: list) -> int:
    """本轮起点：最后一条 HumanMessage 的索引 + 1；无 HumanMessage 返回 0。

    计数器必须从这里切开，不能从整个 history 数——checkpointer 恢复的 history
    是整个会话记录，直接 sum(ToolMessage) 会误判为「会话累计用量」。
    """
    for i in range(len(history) - 1, -1, -1):
        if isinstance(history[i], HumanMessage):
            return i + 1
    return 0


def _is_tool_failure(msg: ToolMessage) -> bool:
    """判断一条 ToolMessage 是否代表工具执行失败。

    必须先排除本节点自己注入的提示语，否则熔断逻辑会自我强化。
    """
    content = msg.content if isinstance(msg.content, str) else str(msg.content or "")
    if any(marker in content for marker in _NODE_NOTICE_MARKERS):
        return False
    if getattr(msg, "status", None) == "error":
        return True
    return content.lstrip().startswith(_TOOL_ERROR_PREFIXES)


def failed_tool_names(history: list, turn_start: int) -> dict[str, int]:
    """统计本轮内各工具连续失败次数（成功即清零）。

    返回值只保留连续失败数 >= MAX_TOOL_FAILURES 的工具。
    """
    streak: dict[str, int] = {}
    for m in history[turn_start:]:
        if not isinstance(m, ToolMessage):
            continue
        name = getattr(m, "name", None) or ""
        if not name:
            continue
        if _is_tool_failure(m):
            streak[name] = streak.get(name, 0) + 1
        else:
            streak[name] = 0
    return {n: c for n, c in streak.items() if c >= MAX_TOOL_FAILURES}


def _is_repeating(history: list, turn_start: int, broken_tools: dict) -> bool:
    """连续两次同名同参调用才是死循环信号；跨轮不算。"""
    calls = []
    for m in history[turn_start:]:
        if isinstance(m, AIMessage) and m.tool_calls:
            for tc in m.tool_calls:
                calls.append((
                    tc.get("name"),
                    json.dumps(tc.get("args", {}), sort_keys=True, ensure_ascii=False),
                ))
    if len(calls) < 2:
        return False
    if calls[-1][0] in broken_tools and calls[-1] == calls[-2]:
        return True
    return calls[-1] == calls[-2]


def compute_circuit(history: list, selected_tools: list) -> dict:
    """计算本轮工具循环防护状态。

    Returns:
        {
            "turn_start": int,
            "tool_rounds": int,       # 本轮已执行/待执行工具调用次数
            "broken_tools": dict,     # 连续失败需熔断的工具
            "force_stop": bool,       # 次数上限或重复调用，强制不再调工具
            "stop_reason": str,       # 触发原因（日志用），未触发为空
        }
    """
    ts = turn_anchor(history)
    turn_tools = [m for m in history[ts:] if isinstance(m, ToolMessage)]
    pending_calls = sum(
        len(m.tool_calls) for m in history[ts:]
        if isinstance(m, AIMessage) and m.tool_calls
    )
    tool_rounds = max(len(turn_tools), pending_calls)

    broken = failed_tool_names(history, ts)
    # 从候选里摘掉连续失败的工具（用户显式点名的豁免）
    if broken and selected_tools:
        selected_tools[:] = [
            t for t in selected_tools
            if t.name not in broken or t.name in _SOFT_BAN_EXEMPT
        ]

    repeating = _is_repeating(history, ts, broken)
    force_stop = tool_rounds >= MAX_TOOL_ROUNDS or repeating

    reason = ""
    if force_stop:
        reason = (
            f"次数达上限（{tool_rounds}/{MAX_TOOL_ROUNDS}）"
            if tool_rounds >= MAX_TOOL_ROUNDS
            else "检测到连续重复调用"
        )

    return {
        "turn_start": ts,
        "tool_rounds": tool_rounds,
        "broken_tools": broken,
        "force_stop": force_stop,
        "stop_reason": reason,
    }
