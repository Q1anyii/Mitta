"""核心生成节点：组装提示词 → 工具筛选 → LLM 流式生成 → 合并 chunk。

拆分自原 main_graph.py 的 llm_node 闭包函数（原 113 行）。
依赖：model, system_prompt, tool_filter, tools, store, get_user_system_prompt，通过参数注入。
内部调用：_get_username(config), _ensure_username_profile(store, ...), _repair_history(history)。
"""

import json
import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.store.base import BaseStore
from loguru import logger

from graphs.state import OverAllState
from graphs.tool_filter import ToolFilter
from constant.persona_constant import DEFAULT_PERSONA, get_persona
from graphs.utils.history_repair import _repair_history, _trim_history
from graphs.utils.user_profile import _ensure_username_profile, _get_username

# 检索资料最大文档数：超出丢弃（优先保留最相关的排前文档）
# 2026-09-19 P1 放宽（H-20260919-07）：5→8，与 rerank/filter 放宽对齐
MAX_RETRIEVAL_DOCS = 8
# 单篇检索文档最大字符数：超出截断，防止超长文档撑爆单次请求 token
MAX_DOC_CHARS = 2000
# 单轮请求内工具调用次数上限（per-turn，不是 per-session）：
# 只统计「本次用户请求」内的工具调用，用户发新消息即重新计数。
# 取 8 而非 4：正常复杂任务（多步查询/分析）可能需 5-7 次工具调用，
# 上限只作兜底，真正的死循环由下方"失败熔断 / 重复调用检测"提前截停。
MAX_TOOL_ROUNDS = 8
# 同一工具连续失败几次后，本轮内禁止再调该工具。
# 场景（2026-09-19 实测）：模型反复调 sequentialthinking，每次都超时，
# 8 次额度全被这一个坏工具吃光，用户拿到的是"次数到上限"而不是答案。
# 连续失败 2 次即足以判定"这个工具本轮坏了"，没必要等它把额度耗完。
MAX_TOOL_FAILURES = 2
# 工具名（含 server 前缀/别名）出现在 query 里时，视为用户/业务显式点名要用的工具。
# 这类工具不参与"失败熔断"与"重复调用"的禁用判定（见下方 _disabled_tools）。
_SOFT_BAN_EXEMPT = frozenset()  # 预留：需要豁免熔断的工具名可加进来


def _turn_anchor(history: list) -> int:
    """本轮起点：最后一条 HumanMessage 的索引 + 1；无 HumanMessage 返回 0。

    ★ 计数器必须从这里切开，不能从整个 history 数。
    checkpointer 恢复的 history 是**整个会话记录**（跨轮累积），
    早先版本直接 sum(ToolMessage) 会误判为「会话累计用量」：
    聊到第 5-6 轮时历史里已积满 8 条 ToolMessage，本轮一次工具还没调
    就被 force_stop 拦下（用户可见症状：「本轮的工具调用次数已经用满，我没法再重试」）。
    轮次上限的语义是「单轮最多调几次」，会话级累计不构成死循环风险——
    用户每发一条新消息都是一次重试机会，不存在跨轮死循环。
    """
    for i in range(len(history) - 1, -1, -1):
        if isinstance(history[i], HumanMessage):
            return i + 1
    return 0


# 工具失败信号：ToolMessage.status == "error"（langgraph 标准），
# 或内容以既有错误前缀开头（DynamicToolNode 的 _tool_error_message 产出）。
_TOOL_ERROR_PREFIXES = (
    "工具执行失败",
    "工具参数错误",
    "抓取失败",
    "搜索失败",
    "读取失败",
    "git 命令",
)
# 循环检测/次数上限的提示语——由本节点注入，不能被当成工具失败信号。
_NODE_NOTICE_MARKERS = ("本轮请求中工具调用次数已达上限", "连续多次调用同一工具")


def _is_tool_failure(msg: ToolMessage) -> bool:
    """判断一条 ToolMessage 是否代表工具执行失败。

    ★ 必须先排除本节点自己注入的提示语：那些提示是 SystemMessage 内容，
    但历史修复（_repair_history）可能把孤立的 tool 结果包装成 ToolMessage，
    内容里带着"次数已达上限"字样，若误判成失败会让熔断逻辑自我强化。
    """
    content = msg.content if isinstance(msg.content, str) else str(msg.content or "")
    if any(marker in content for marker in _NODE_NOTICE_MARKERS):
        return False
    if getattr(msg, "status", None) == "error":
        return True
    return content.lstrip().startswith(_TOOL_ERROR_PREFIXES)


def _failed_tool_names(history: list, turn_start: int) -> dict[str, int]:
    """统计本轮内各工具**连续失败**的次数（按时间顺序，成功即清零）。

    为什么要按"连续"而不是"累计"：一个工具失败 1 次后成功、后又失败 1 次，
    这是正常的不稳定网络行为，不该熔断；只有**连着挂**才说明它这轮彻底不可用。
    返回值只保留连续失败数 >= MAX_TOOL_FAILURES 的工具，交给调用方禁用。
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
            streak[name] = 0  # 成功一次即清零：偶尔抖动不算坏工具
    return {n: c for n, c in streak.items() if c >= MAX_TOOL_FAILURES}


def llm_node(
    state: OverAllState,
    config: RunnableConfig,
    store: BaseStore,
    model,
    system_prompt: str,
    tool_filter: ToolFilter,
    tools: list[BaseTool],
    get_user_system_prompt,
    lazy_loader=None,
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
            # 防 token 膨胀：最多取前 5 篇、每篇截断到 2000 字符（保留头部与关键词），
            # 检索结果本身已按相关性降序，截断不会丢失最相关部分
            docs = [d[:MAX_DOC_CHARS] for d in docs[:MAX_RETRIEVAL_DOCS]]
            context = "\n\n".join(f"[文档 {i + 1}] {doc}" for i, doc in enumerate(docs))
        else:
            context = "（知识库中未检索到相关内容）"
        user_content = (
            f"请严格依据下面检索到的资料回答用户问题，资料中没有的内容不要编造。\n\n"
            f"【引用要求】\n"
            f"- 事实性陈述（数字、接口、配置、机制、结论）后必须标注来源角标，如 [1][2]，"
            f"角标编号对应下方【检索资料】的 [文档 i]；\n"
            f"- 若所有资料均无法支撑某个事实，明确说\"知识库暂未覆盖该内容\"，不要用常识补全；\n"
            f"- 角标只标事实句，客套话/过渡句/总结句不加。\n\n"
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
    # 滑动窗口裁剪：历史随轮数线性增长，若全量传入 LLM，每轮 token 消耗随消息数
    # 平方级膨胀（N 轮 × 每轮全量 N 条）。只保留最近窗口内消息（默认 30 条 ≈ 6 轮），
    # 且保证 tool_calls/ToolMessage 配对完整，早期消息直接丢弃。
    history = _trim_history(list(state.get("messages", [])))

    # ── 4. 组装 system prompt：基础默认 + 用户自定义 + 人格 prompt + 长期记忆 ──
    # get_user_system_prompt 内部从 MySQL user_profile 表按 user_id 读取用户自定义内容
    system_content = get_user_system_prompt(user_id, system_prompt)

    # ── 4.1 人格注入：按 state.persona 选人格 prompt + 工具白名单 ──
    # 所有人格（含默认 cappie）都追加人格 prompt：cappie 也叠 PROMPT_CAPPIE 语气层，
    # 让默认技术问答带米塔味但仍严谨可靠。顺序不变——原 system_prompt / 用户自定义 /
    # 长期记忆在前，人格 prompt 在后（语气层不推翻事实层、隐私铁律）。
    persona_key = state.get("persona") or DEFAULT_PERSONA
    persona = get_persona(persona_key)
    # is_default_persona 供 194 行"默认人格 + 主动无工具"裸模型分支判断，勿删
    is_default_persona = persona_key == DEFAULT_PERSONA
    system_content += f"\n\n{persona['prompt']}"
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
    # ── 5.1 懒加载触发：语义命中未连接的第三方 MCP 工具 → 拉起连接 → 重试筛选 ──
    # 首调会阻塞当前线程等待连接（低频第三方工具首调慢可接受）；
    # 连接成功后工具注入可变工具池（tools 为同一 list 引用），重试本轮即可用。
    pending_hits = list(getattr(tool_filter, "last_pending_hits", []) or [])
    if pending_hits and lazy_loader is not None:
        server_name = lazy_loader.server_of(pending_hits[0])
        if server_name:
            logger.info(f"工具筛选命中懒加载 server [{server_name}]（{pending_hits}），触发连接")
            if lazy_loader.trigger(server_name):
                # 工具池已扩充：重新筛选，本轮即可使用新工具
                selected_tools = tool_filter.select_tools(filter_query, tools)
    # ── 5.2 人格白名单：套在 tool_filter 结果之上，不动 ToolFilter 本身 ──
    # cappie(None) 不过滤；kind/crazy/manager 按 allowed_tools 收缩；[] 表示完全不给工具。
    # 注：白名单按 name 精确匹配；name 与真实工具名不符时过滤结果变少（安全方向，不会误调写工具）。
    allowed = persona["allowed_tools"]
    if allowed is not None:
        allowed_set = set(allowed)
        selected_tools = [t for t in selected_tools if t.name in allowed_set]
    logger.info(
        f"[persona] key={persona_key} label={persona['label']} "
        f"tools_in={len(tools)} tools_filtered={len(selected_tools)} "
        f"white_list={'none(全量)' if allowed is None else f'{len(allowed)}个'}"
    )

    # 工具调用死循环防护（三道防线，均只统计**本轮**）：
    # (a) 次数上限：本轮起点之后的工具执行次数（ToolMessage 或同轮内 tool_calls）
    #     达到 MAX_TOOL_ROUNDS 后不再 bind 工具，注入终止提示让模型直接回答（硬性结束循环）；
    # (b) 连续重复调用检测：最近两次工具调用（name+args 完全相同）说明模型在同
    #     一动作上空转（无新信息产生），立即判定死循环提前截停，不必等满 8 次；
    # (c) 失败熔断（2026-09-19 新增）：同一工具**连续失败** MAX_TOOL_FAILURES 次后，
    #     本轮内把它从可 bind 列表里摘掉。此前只有 (a)(b)，于是一个超时的工具会被反复
    #     重试直到吃满 8 次额度——用户看到的是"次数到上限"，而真正该说的是"这个工具坏了"。
    #     注意 (c) 是**摘工具**不是**停整轮**：坏工具摘掉后其余工具仍可用，模型能换路走。
    # 注意：起点之后的 ToolMessage 覆盖 tool_node 已执行的调用；起点之后 AI 的
    # tool_calls 计入尚未执行的那批（两者不会重叠：本节点返回后 tool_node 才执行）。
    # 两者取 max 而非相加，避免同一批调用被重复计数——一次误计就可能让第 8 次调用被拒。
    turn_start = _turn_anchor(history)
    turn_tools = [m for m in history[turn_start:] if isinstance(m, ToolMessage)]
    pending_calls = sum(
        len(m.tool_calls) for m in history[turn_start:]
        if isinstance(m, AIMessage) and m.tool_calls
    )
    tool_rounds = max(len(turn_tools), pending_calls)

    # (c) 失败熔断：先算出本轮该禁用的工具，再从候选里摘掉
    broken_tools = _failed_tool_names(history, turn_start)
    if broken_tools and selected_tools:
        before = len(selected_tools)
        selected_tools = [
            t for t in selected_tools
            if t.name not in broken_tools or t.name in _SOFT_BAN_EXEMPT
        ]
        logger.warning(
            f"工具失败熔断：{sorted(broken_tools)} 本轮已禁用"
            f"（连续失败 >= {MAX_TOOL_FAILURES} 次），候选 {before} -> {len(selected_tools)}"
        )

    def _is_repeating() -> bool:
        # 只看本轮调用序列：连续两次同名同参调用才是死循环信号。
        # 跨轮不算——用户完全可能在新一轮重复上一轮的同一请求（那就是重试）。
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
        # 被熔断的工具已从候选摘除，它再重复也不构成"模型空转"——模型是在别处找路。
        # 但若连摘除后的候选都还在重复，说明模型真卡住了，仍要截停。
        if calls[-1][0] in broken_tools and calls[-1] == calls[-2]:
            return True
        return calls[-1] == calls[-2]

    force_stop = tool_rounds >= MAX_TOOL_ROUNDS or _is_repeating()
    if selected_tools and not force_stop:
        model_with_tools = model.bind_tools(selected_tools)
    else:
        # 两路均未命中（或已达工具轮次上限）：不 bind 空列表（OpenAI 兼容 API 会 400），
        # 改用裸模型并注入对应提示
        if force_stop:
            reason = (
                f"次数达上限（{tool_rounds}/{MAX_TOOL_ROUNDS}）" if tool_rounds >= MAX_TOOL_ROUNDS
                else "检测到连续重复调用"
            )
            logger.warning(f"本轮强制停止工具调用：{reason}")
            messages.append(SystemMessage(
                content="注意：本轮请求中工具调用次数已达上限，请不要再调用任何工具，"
                        "直接基于你已有的上下文信息回答用户的问题。"
            ))
        elif broken_tools:
            # 所有候选工具都被熔断摘光了：明确告知模型"这些工具本轮不可用"，
            # 而不是含糊说"没有工具"，否则模型会以为自己能力缺失（而非工具故障）
            logger.warning(f"本轮候选工具全部被熔断禁用：{sorted(broken_tools)}")
            messages.append(SystemMessage(
                content=f"注意：以下工具本轮已连续失败多次、暂时不可用：{'、'.join(sorted(broken_tools))}。"
                        "请不要再尝试调用它们，改用其他方式回答用户，或如实说明这部分暂时做不到。"
            ))
        elif not is_default_persona and allowed == []:
            # 人格主动无工具（如 crazy）：人格 prompt 已自带能力边界说明，
            # 不注入"当前没有可用的工具"系统提示，避免与人格台词层设定冲突
            logger.info(f"[persona] {persona_key} 人格主动无工具，裸模型对话")
        else:
            messages.append(SystemMessage(
                content="注意：当前没有可用的工具。若用户的请求依赖工具能力（如查文件、查数据库、"
                        "操作外部服务），请如实告知暂时无法处理，不要编造结果或假装已执行。"
            ))
        model_with_tools = model

    # ── 5.5 深度思考模式：根据用户在前端选择的开关动态 bind ──
    # thinking_mode/reasoning_effort 从 chat_service._build_stream_config 写入 config.configurable，
    # 不在 model 初始化时固定（container.py 的 model 保持普通模式），
    # 这样 classify_node/memory_node 不会被拖慢，只有主回答节点按需开启
    thinking_mode = config["configurable"].get("thinking_mode", False)
    reasoning_effort = config["configurable"].get("reasoning_effort", "low")
    logger.info(f"[thinking-debug] thinking_mode={thinking_mode} reasoning_effort={reasoning_effort}")
    if thinking_mode:
        # .bind() 返回新的 Runnable，在已有 bind_tools 基础上叠加 extra_body
        model_with_tools = model_with_tools.bind(
            extra_body={"thinking": {"type": "enabled"}},
            reasoning_effort=reasoning_effort,
        )
        # 调试：打印 bind 后的 model 配置，确认 extra_body 是否真的传入
        try:
            bound_kwargs = getattr(model_with_tools, "kwargs", {})
            logger.info(f"[thinking-debug] bound kwargs={bound_kwargs}")
            # 打印底层 model 的配置
            if hasattr(model_with_tools, "model"):
                inner = model_with_tools.model
                logger.info(f"[thinking-debug] inner model={type(inner).__name__} "
                            f"extra_body={getattr(inner, 'extra_body', 'N/A')} "
                            f"model_kwargs={getattr(inner, 'model_kwargs', 'N/A')}")
        except Exception as e:
            logger.info(f"[thinking-debug] failed to inspect model: {e}")

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
    # 熔断硬约束：次数上限/重复调用时，不靠提示词让模型"自觉不调"——
    # 模型仍可能幻觉出 tool_calls（尤其裸模型时）。代码层直接剥空，强制 tool_node 不执行。
    if force_stop and tool_calls:
        logger.warning(
            f"熔断硬剥离：模型仍试图调用工具 {[tc['name'] for tc in tool_calls]}，已强制清空"
        )
        tool_calls = []
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

    # ── 8. 工具循环轮次去重用户消息 ──
    # 历史中已存在 ToolMessage（本轮处于工具循环中）时，用户问题已在上下文里，
    # 本轮只追加 ai_reply，不再重复插入 HumanMessage——否则模型每轮都看到
    # "用户让我调用工具"被重新强调一次，永远不收敛（工具死循环的直接催化剂）。
    in_tool_loop = bool(history) and isinstance(history[-1], ToolMessage)
    if in_tool_loop:
        return {"messages": [ai_reply], "tool_status": tool_status}
    return {"messages": [HumanMessage(content=input_str), ai_reply], "tool_status": tool_status}
