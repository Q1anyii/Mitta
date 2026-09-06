"""记忆节点：提取本轮对话中的长期信息并写入 store（按用户隔离）。

拆分自原 main_graph.py 的 memory_node 闭包函数 + _memory_cache_key 函数。
依赖：model（LLM 实例），通过参数注入。
"""

import re
import uuid

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.store.base import BaseStore
from loguru import logger

from constant.cache_constant import CACHE_MEMORY_NODE_TTL
from constant.prompt_constants import MEMORY_EXTRACT_PROMPT, NO_INFO_MARKS
from graphs.state import OverAllState


def _memory_cache_key(state: dict) -> str:
    """memory_node 缓存键计算函数（LangGraph CachePolicy.key_func）。

    仅当本轮工具被执行（executed）或执行失败/无工具可用（unavailable）
    时返回确定性 key（可命中）；idle 轮（筛选出工具但模型未调用）返回随机键，永不命中、
    不与其他轮次共享缓存。key 含消息轮次（len(msgs)），避免跨轮同文误命中导致
    store 记忆写入被跳过；命中时跳过 LLM 记忆提取与 store.put（仅限 TTL 内同轮重复到达）。

    Args:
        state: 图状态 dict

    Returns:
        缓存键字符串
    """
    if not isinstance(state, dict):
        return f"nocache-{uuid.uuid4().hex}"
    if state.get("tool_status") not in ("executed", "unavailable"):
        return f"nocache-{uuid.uuid4().hex}"
    msgs = state.get("messages", []) if isinstance(state.get("messages", []), list) else []
    last_ai = ""
    for m in reversed(msgs):
        if isinstance(m, AIMessage) and m.content:
            last_ai = m.content if isinstance(m.content, str) else str(m.content)
            break
    return f"{len(msgs)}|{state.get('input_str', '')}|{last_ai}"


def memory_node(state: OverAllState, config: RunnableConfig, store: BaseStore, model) -> None:
    """将本轮对话中的长期信息提取并写入 store（按用户隔离）。

    快速路径：idle 轮（筛选出工具但模型未调用，通常是简单闲聊）且无需检索时，
    直接跳过记忆提取，避免 model.invoke() 阻塞 SSE 流导致前端消息超时失效。
    用户名基础档案已由 llm_node 在回答前写入，闲聊场景通常无新增长期信息。

    Args:
        state: 当前图状态
        config: LangGraph 配置（含 user_id）
        store: 长期记忆存储（依赖注入）
        model: LLM 实例（依赖注入，用于记忆提取）
    """
    user_id = config["configurable"].get("user_id", "default")
    namespace = ("rag_chat", user_id)

    # 快速路径：idle 闲聊轮跳过记忆提取
    if state.get("tool_status") == "idle" and not state.get("needs_retrieval"):
        logger.debug(f"memory_node 跳过（idle 闲聊轮）user_id={user_id}")
        return

    # 读取已有档案
    item = store.get(namespace, "user_profile")
    original_profile = item.value["profile"] if item else "（暂无档案）"
    old_profile = original_profile

    # 用户名基础档案已由 llm_node 在组装提示词前写入（首轮对话即落库），
    # 这里只负责增量提取与防丢失兜底，不再重复解析 Redis token

    # 用 LLM 提取/合并长期记忆（AI 回答已由 add_messages 合并为完整消息）
    ai_reply = state["messages"][-1].content
    response = model.invoke([
        HumanMessage(content=MEMORY_EXTRACT_PROMPT.format(
            old_profile=old_profile,
            input_str=state["input_str"],
            llm_output=ai_reply,
        ))
    ])
    new_profile = response.content.strip()

    # 本轮无新信息时（LLM 返回占位符），至少把已有档案持久化
    if new_profile in NO_INFO_MARKS:
        new_profile = old_profile

    # 兜底：LLM 合并结果若丢失了"用户名"行，从原档案补回（llm_node 已保证原档案含该行）
    m = re.search(r"^用户名：.+$", original_profile, re.MULTILINE)
    if m and m.group(0) not in new_profile:
        new_profile = f"{new_profile}\n{m.group(0)}"

    # 与「合并前」档案比较：首次对话（无档案→含用户名）也会触发写入
    if new_profile and new_profile != original_profile:
        store.put(namespace, "user_profile", {"profile": new_profile})
        logger.info(f"长期记忆已更新（user_id={user_id}）：{new_profile[:100]}")
