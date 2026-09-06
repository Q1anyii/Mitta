"""核心生成节点：组装提示词 → 工具筛选 → LLM 流式生成 → 合并 chunk。

拆分自原 main_graph.py 的 llm_node 闭包函数（原 113 行）。
依赖：model, system_prompt, tool_filter, tools, store, get_user_system_prompt，通过参数注入。
内部调用：_get_username(config), _ensure_username_profile(store, ...), _repair_history(history)。
"""

import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.store.base import BaseStore
from loguru import logger

from graphs.state import OverAllState
from graphs.tool_filter import ToolFilter
from graphs.utils.history_repair import _repair_history
from graphs.utils.user_profile import _ensure_username_profile, _get_username


def llm_node(
    state: OverAllState,
    config: RunnableConfig,
    store: BaseStore,
    model,
    system_prompt: str,
    tool_filter: ToolFilter,
    tools: list[BaseTool],
    get_user_system_prompt,
) -> OverAllState:
    """核心生成节点：组装上下文 → 筛选工具 → LLM 生成 → 返回消息。

    流程：
    1. 检索分支：从 retrieve_res 提取文档文本，组装检索资料提示词
    2. 长期记忆：先把 username 并入档案并立即落库，再读取组装提示词
    3. 短期记忆：checkpointer 恢复的历史对话，双向清洗 tool_calls/ToolMessage 配对
    4. 工具筛选：规则命中 + 语义检索并集，只把候选工具暴露给 LLM
    5. 流式生成：model.stream 累积 chunk，用 AIMessageChunk.__add__ 合并（tool_calls 分块传输）
    6. 工具状态判定：executed/unavailable/idle，供 memory_node 缓存策略使用

    Args:
        state: 当前图状态
        config: LangGraph 配置（含 user_id, user_info）
        store: 长期记忆存储（依赖注入）
        model: LLM 实例（依赖注入）
        system_prompt: 基础系统提示词（依赖注入）
        tool_filter: 工具筛选器（依赖注入）
        tools: 全量工具列表（依赖注入）
        get_user_system_prompt: 获取用户级 system prompt 的函数（依赖注入）

    Returns:
        {"messages": [HumanMessage, AIMessage], "tool_status": str}
    """
    logger.success("llm_node is runed")
    input_str = state["input_str"]
    retrieval_res = state.get("retrieve_res")

    # ── 1. 组装用户消息（检索分支 / 无需检索分支）──
    if retrieval_res and "output" in retrieval_res:
        # 检索分支：在线重排结果已按相关性降序，直接取文档文本。
        # 兼容 Document 对象与 dict 两种形态（checkpoint 恢复/旧缓存里是 dict）
        raw_docs = retrieval_res.get("output", [])
        docs = [
            doc.page_content if hasattr(doc, "page_content") else doc.get("page_content", "")
            for doc in raw_docs
        ]
        if docs:
            context = "\n\n".join(f"[文档 {i + 1}] {doc}" for i, doc in enumerate(docs[:5]))
        else:
            context = "（知识库中未检索到相关内容）"
        user_content = (
            f"请严格依据下面检索到的资料回答用户问题，资料中没有的内容不要编造。\n\n"
            f"【检索资料】\n{context}\n\n"
            f"【用户问题】\n{input_str}"
        )
    else:
        # 无需检索分支：直接回答
        user_content = input_str

    # ── 2. 长期记忆：先把 username 并入档案并立即落库，再读取组装提示词 ──
    # 若等回答完 memory_node 才写入，首轮 AI 会先回答不认识、记忆随后才落库
    user_id = config["configurable"].get("user_id", "default")
    username = _get_username(config)
    long_term = _ensure_username_profile(store, user_id, username)

    # ── 3. 短期记忆：checkpointer 按 thread_id 恢复的历史对话 ──
    history = state.get("messages", [])

    # ── 4. 组装 system prompt：基础默认 + 用户自定义 + 长期记忆 ──
    # get_user_system_prompt 内部从 MySQL user_profile 表按 user_id 读取用户自定义内容
    system_content = get_user_system_prompt(user_id, system_prompt)
    if long_term and long_term != "（暂无档案）":
        system_content += f"\n\n【用户长期记忆】\n{long_term}"

    messages = [SystemMessage(content=system_content)] + _repair_history(list(history))
    messages.append(HumanMessage(content=user_content))

    # ── 5. 运行时工具筛选：规则命中 + 语义检索并集 ──
    # 多轮增强：仅当输入含指代/承接信号时才拼接最近一轮 AI 回复，弥补指代消解
    # （如"继续""用刚才那个工具"）；话题切换时拼接反而污染检索信号
    last_ai = next(
        (m.content for m in reversed(history) if isinstance(m, AIMessage) and m.content),
        "",
    )
    if last_ai and re.search(r"(继续|刚才|那个|这个|它|同样|跟刚才)", input_str):
        # 截断防 token 膨胀：AI 回复只取前 200 字符，主信号仍是用户输入（放最前）
        filter_query = f"{input_str}\n{last_ai[:200]}"
    else:
        filter_query = input_str  # 话题切换/首轮：纯用户输入，检索信号纯净

    selected_tools = tool_filter.select_tools(filter_query, tools)
    if selected_tools:
        model_with_tools = model.bind_tools(selected_tools)
    else:
        # 两路均未命中：不 bind 空列表（OpenAI 兼容 API 会 400），
        # 改用裸模型并注入提示，让 AI 如实告知无法处理
        messages.append(SystemMessage(
            content="注意：当前没有可用的工具。若用户的请求依赖工具能力（如查文件、查数据库、"
                    "操作外部服务），请如实告知暂时无法处理，不要编造结果或假装已执行。"
        ))
        model_with_tools = model

    # ── 6. 流式生成：累积 chunk，用 AIMessageChunk.__add__ 合并 ──
    # 注意：节点不能返回生成器——langgraph 1.x 会把生成器当单条消息转换，
    # 报 "Unsupported message type: generator"
    chunks = []
    for chunk in model_with_tools.stream(messages):
        chunks.append(chunk)

    # 合并所有 chunk：流式模式下 tool_calls 分块传输（首块含 name/id，后续块含 arguments 分片），
    # 最后一个 chunk 的 tool_calls 通常为空（只有 finish_reason），
    # 必须用 AIMessageChunk.__add__ 累积合并，否则工具调用会被丢弃导致 tool_calls=[]
    final_chunk = None
    for c in chunks:
        final_chunk = c if final_chunk is None else final_chunk + c
    content = final_chunk.content if final_chunk else ""
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    tool_calls = final_chunk.tool_calls if final_chunk else []
    logger.info(
        f"llm_node 生成完成：tool_calls={[tc['name'] for tc in tool_calls]} "
        f"content_len={len(content)}"
    )
    ai_reply = AIMessage(content=content, tool_calls=tool_calls)

    # ── 7. 本轮工具状态（memory_node 缓存策略的依据）──
    # - 有 tool_calls → executed（工具将被 ToolNode 执行）
    # - 无 tool_calls 但上一步已执行过工具（messages 末尾是 ToolMessage）→ executed
    # - 无工具可用（筛选为空，裸模型兜底回答）→ unavailable
    # - 其余（筛选出工具但模型未调用）→ idle，不参与 memory_node 缓存
    tool_status = "idle"
    if tool_calls:
        tool_status = "executed"
    elif history and isinstance(history[-1], ToolMessage):
        tool_status = "executed"
    elif not selected_tools:
        tool_status = "unavailable"

    return {"messages": [HumanMessage(content=input_str), ai_reply], "tool_status": tool_status}
