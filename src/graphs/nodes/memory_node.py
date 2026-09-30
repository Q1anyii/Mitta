"""记忆节点：提取本轮对话中的长期信息并写入 store（按用户隔离）。

拆分自原 main_graph.py 的 memory_node 闭包函数 + _memory_cache_key 函数。
依赖：model（LLM 实例），通过参数注入。
"""

import re
import uuid
import threading

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.store.base import BaseStore
from loguru import logger
from concurrent.futures.thread import ThreadPoolExecutor
from constant.cache_constant import CACHE_MEMORY_NODE_TTL
from constant.prompt_constants import MEMORY_EXTRACT_PROMPT, NO_INFO_MARKS
from graphs.state import OverAllState
from graphs.utils.per_user_lock import get_per_user_lock

# 实例化线程池，限制最大并发8个后台任务，防止线程爆炸
executor = ThreadPoolExecutor(max_workers=8)


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
    H-20260920-01 二轮方案（fire-and-forget）：真正需要提取的轮次，把
    「读取档案 → LLM 提取/合并 → store.put」整体移入后台线程，
    节点函数立即返回，不再阻塞 SSE 完成态（原实现同步 model.invoke 1~3s，
    导致正文打完后转圈仍转几秒）。短期记忆入库由 checkpointer 在图机制内完成。
    Args:
        state: 当前图状态
        config: LangGraph 配置（含 user_id）
        store: 长期记忆存储（依赖注入）
        model: LLM 实例（依赖注入，用于记忆提取）
    """
    user_id = config["configurable"].get("user_id", "default")
    namespace = ("rag_chat", user_id)

    # 防御：异常路径/重放可能携带空 messages，避免 IndexError 污染主图
    if not state.get("messages"):
        logger.warning(f"memory_node 跳过（messages 为空）user_id={user_id}")
        return

    # 快速路径：idle（筛选出工具但模型未调用）或 unavailable（无可用工具）且无需检索时，
    # 跳过记忆提取，避免 model.invoke() 阻塞 SSE 流导致前端消息超时失效。
    if state.get("tool_status") in ("idle", "unavailable") and not state.get("needs_retrieval"):
        logger.debug(f"memory_node 跳过（{state.get('tool_status')} 轮，无新增长期信息）user_id={user_id}")
        return

    # 提前拷贝需要的变量，不要跨线程直接引用state，规避竞态风险
    input_str = state.get("input_str", "")
    ai_reply = state["messages"][-1].content

    # 后台任务：LLM 提取 + 增量合并 + store.put。失败静默打日志，不影响主图与 SSE 流。
    # 所有需要的值通过参数传入，不再在线程内读取state。
    # 串行锁：同一用户的「读档案 → LLM 合并 → store.put」原子化，
    # 防多轮 fire-and-forget 并发写互相覆盖（last-write-wins 丢信息）。
    def _extract_and_persist(uid: str, ns, in_str: str, llm_out: str):
        with get_per_user_lock(uid):
            try:
                # 读取已有档案
                item = store.get(ns, "user_profile")
                original_profile = item.value["profile"] if item else "（暂无档案）"
                old_profile = original_profile

                # 用 LLM 提取/合并长期记忆
                response = model.invoke([
                    HumanMessage(content=MEMORY_EXTRACT_PROMPT.format(
                        old_profile=old_profile,
                        input_str=in_str,
                        llm_output=llm_out,
                    ))
                ])
                new_profile = response.content.strip()

                # 本轮无新信息时（LLM 返回占位符或空串），至少把已有档案持久化。
                # 空串必须兜底：否则用户名补回逻辑会把空串变成残缺档案覆盖原档案。
                if not new_profile or new_profile in NO_INFO_MARKS:
                    new_profile = old_profile

                # 兜底：LLM 合并结果若丢失了"用户名"行，从原档案补回
                m = re.search(r"^用户名：.+$", original_profile, re.MULTILINE)
                if m and m.group(0) not in new_profile:
                    new_profile = f"{new_profile}\n{m.group(0)}"

                # 档案发生变更才写入存储
                if new_profile and new_profile != original_profile:
                    store.put(ns, "user_profile", {"profile": new_profile})
                    logger.info(f"长期记忆已更新（user_id={uid}）：{new_profile[:100]}")
            except Exception as e:
                # exc_info=True：保留堆栈，后台任务失败才能定位到具体调用链
                logger.error(f"长期记忆后台提取失败（静默，不影响对话）user_id={uid}: {e}", exc_info=True)

    # 提交任务，参数传入进去，不再捕获回调（内部已经try-except）
    executor.submit(
        _extract_and_persist,
        user_id,
        namespace,
        input_str,
        ai_reply
    )
    # 直接return，主LangGraph节点立刻结束，不会阻塞SSE
    return