"""主对话图（Main Graph）：LangGraph 编排层。

只负责组装：创建依赖 → partial 绑定 → add_node → add_edge → compile。
所有节点逻辑在 graphs/nodes/ 下，工具函数在 graphs/utils/ 下，状态在 graphs/state.py。

图流程：
    START → classify_node → (route) → retrieve_node → llm_node → (route_after_llm)
                                                          ↓              ↓
                                                      memory_node    tool_node
                                                          ↓              ↓
                                                         END         llm_node（循环）
"""

from functools import partial

from langchain_core.tools import BaseTool
from langchain_core.tools.base import ToolException
from langgraph.types import CachePolicy
from langgraph.store.base import BaseStore
from langgraph.constants import START, END
from langgraph.graph.state import StateGraph
from langgraph.prebuilt import ToolNode
from loguru import logger

from graphs.state import OverAllState
from graphs.tool_filter import ToolFilter
from graphs.nodes.classify_node import classify_node
from graphs.nodes.retrieve_node import retrieve_node
from graphs.nodes.llm_node import llm_node
from graphs.nodes.memory_node import memory_node, _memory_cache_key
from graphs.nodes.routes import route, route_after_llm
from constant.cache_constant import CACHE_MEMORY_NODE_TTL


def _tool_error_message(e: Exception) -> str:
    """工具异常 → 对 LLM 可操作的错误提示（ToolNode handle_tool_errors 回调）。

    langgraph 1.1.x 默认的 handle_tool_errors 只兜底参数校验错误
    （ToolInvocationError），MCP 工具执行异常（如 search_files 的 path 误传文件
    触发 ENOTDIR）会原样抛出让整图中断；此处统一转成 status="error" 的
    ToolMessage 回传模型，由模型自行纠正参数（改目录路径 / 换 read_file 等）。
    """
    if isinstance(e, ToolException):
        if "ENOTDIR" in str(e):
            return (
                "工具参数错误：path 必须是目录，不能是文件。"
                "请改为目录路径，或改用 read_file 工具读取该文件。"
                f"原始错误：{e}"
            )
        return f"工具执行失败：{e}"
    return f"工具执行失败：{repr(e)}"


def build_main_graph(
    retrieve_graph,
    pool,
    checkpointer,
    store,
    model=None,
    system_prompt: str = None,
    cache=None,
    mcp_tools: list[BaseTool] | None = None,
):
    """构建并编译主对话图。

    所有节点函数通过 functools.partial 绑定依赖后注册到图，
    节点本身是纯函数（定义在 graphs/nodes/），可单独单元测试。

    Args:
        retrieve_graph: 编译后的检索图（RAG 子系统）
        pool: PostgreSQL 连接池
        checkpointer: LangGraph checkpoint 持久化
        store: LangGraph 长期记忆存储
        model: LLM 实例（依赖注入）
        system_prompt: 基础系统提示词（依赖注入）
        cache: LangGraph 缓存（可选）
        mcp_tools: MCP 工具列表（用户自定义）

    Returns:
        编译后的 LangGraph 可调用对象
    """
    # 兼容旧调用：未注入 model/system_prompt 时延迟导入（双轨运行期）
    if model is None or system_prompt is None:
        from init import model as _model, system_prompt as _prompt
        model = model or _model
        system_prompt = system_prompt or _prompt

    # 工具：ToolNode 绑定全量安全工具（按 name 路由执行），
    # LLM 侧在 llm_node 里按本轮 query 运行时筛选后 bind_tools
    tool_filter = ToolFilter(selector_llm=model)
    tools = list(mcp_tools or [])  # build 期无用户 query，不做筛选，直接全量绑定路由
    # handle_tool_errors 必须显式配置：langgraph 1.1.x 默认只兜底参数校验错误，
    # MCP 工具执行异常会原样抛出让整图中断（SSE 断流）；
    # 自定义回调把错误转成 status="error" 的 ToolMessage 回传 LLM 自纠
    tool_node = ToolNode(tools, handle_tool_errors=_tool_error_message)

    # get_user_system_prompt 从 init 导入（纯函数，依赖 MySQL user_profile 表）
    from init import get_user_system_prompt

    # ── 用 partial 绑定依赖，得到符合 LangGraph 节点签名 (state, config) -> state 的函数 ──
    classify_node_bound = partial(classify_node, model=model)
    retrieve_node_bound = partial(retrieve_node, retrieve_graph=retrieve_graph)
    llm_node_bound = partial(
        llm_node,
        store=store, model=model, system_prompt=system_prompt,
        tool_filter=tool_filter, tools=tools,
        get_user_system_prompt=get_user_system_prompt,
    )
    memory_node_bound = partial(memory_node, store=store, model=model)

    # ── 图构建 ──
    builder = StateGraph(state_schema=OverAllState)
    builder.add_node("classify_node", classify_node_bound)
    # retrieve_node：CachePolicy 已停用，检索结果缓存由 retrieve_graph 内部
    # CacheService（Redis + RediSearch）按 thread_id + 问题语义管理
    builder.add_node("retrieve_node", retrieve_node_bound)
    builder.add_node("llm_node", llm_node_bound)
    # tool_node：不启用节点级缓存——CachePolicy 命中时不执行节点，直接复用上次返回的
    # ToolMessage（携带旧 tool_call_id），与当前轮 AI 消息的新 tool_calls id 不匹配，
    # 透传给 API 会双向 400（悬空调用 / 孤儿 ToolMessage）
    builder.add_node("tool_node", tool_node)
    # memory_node：仅 executed/unavailable 轮写入缓存（见 _memory_cache_key），
    # 命中时跳过 LLM 记忆提取与 store 写入，省一次模型调用
    builder.add_node(
        "memory_node",
        memory_node_bound,
        cache_policy=CachePolicy(ttl=CACHE_MEMORY_NODE_TTL, key_func=_memory_cache_key),
    )

    builder.add_edge(START, "classify_node")
    builder.add_conditional_edges(
        "classify_node",
        route,
        ["retrieve_node", "llm_node"],
    )
    builder.add_edge("retrieve_node", "llm_node")
    builder.add_conditional_edges(
        "llm_node",
        route_after_llm,
        ["tool_node", "memory_node"],
    )
    builder.add_edge("tool_node", "llm_node")  # 工具执行结果回到 LLM，生成最终回答
    builder.add_edge("memory_node", END)

    # 创建连接池（open=True 表示立即打开连接）
    # 必须开启 autocommit：迁移脚本含 CREATE INDEX CONCURRENTLY，不能在事务块中执行
    try:
        pool.check()
    except Exception as e:
        logger.error(f"数据库连接失败，请检查 .env 的 POSTGRESQL_DB_URL 与 PostgreSQL 服务")
        logger.error(f"真实错误：{e}")
        raise

    checkpointer.setup()
    store.setup()

    main_graph = builder.compile(checkpointer=checkpointer, store=store, cache=cache)

    return main_graph
