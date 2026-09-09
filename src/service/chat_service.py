import asyncio
import json
import os
import threading as _threading
import chromadb

from pathlib import Path
from langgraph.store.postgres import PostgresStore
from psycopg_pool import ConnectionPool
from langchain_core.messages import BaseMessage, AIMessage, AIMessageChunk, ToolMessage
from loguru import logger
from langgraph.cache.redis import RedisCache  # 可能需要 langgraph-checkpoint-redis 扩展

from config import load_vector_db_config
from graphs.main_graph import build_main_graph
from graphs.retrieve_graph import build_retrieve_graph
from init import COLLECTION_NAME, CustomPostgresSaver
from service.cache_service import cache_service
from service.file_upload_service import file_upload_service
from vector.vector_store import create_vector_store


def _format_sse(data) -> str:
    """格式化 SSE 事件为标准格式。

    Args:
        data: dict（自动 json.dumps）或字符串（如 "[DONE]"）

    Returns:
        "data: {...}\n\n" 格式的 SSE 事件字符串
    """
    if isinstance(data, str):
        return f"data: {data}\n\n"
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _process_graph_chunk(chunk, meta) -> str | None:
    """处理 LangGraph stream 的单个 chunk，返回 SSE 事件字符串或 None。

    stream_mode="messages" 会捕获图中所有 LLM 调用的 token 事件，
    包括 classify_node 的 yes/no 与 memory_node 的记忆提取输出，
    必须按 meta["langgraph_node"] 过滤，只输出 llm_node 的增量，
    否则分类器的 "no" 会混入流式回答出现在前端。

    Args:
        chunk: LangGraph 输出的消息 chunk（AIMessageChunk / ToolMessage 等）
        meta: 包含 langgraph_node 等元信息

    Returns:
        SSE 事件字符串；过滤掉的 chunk 返回 None
    """
    node = meta.get("langgraph_node")

    # llm_node：输出深度思考内容 + 文本内容 + 检测工具调用开始
    if node == "llm_node" and isinstance(chunk, AIMessageChunk):
        events = []
        # 深度思考内容：DeepSeek thinking 模式返回 reasoning_content，
        # LangChain 可能放在直接属性或 additional_kwargs 中，两种都检测
        reasoning = (
            getattr(chunk, "reasoning_content", None)
            or getattr(chunk, "reasoning", None)
            or (chunk.additional_kwargs or {}).get("reasoning_content")
            or (chunk.additional_kwargs or {}).get("reasoning")
        )
        if reasoning:
            events.append(_format_sse({"reasoning": reasoning}))
        # 输出文本内容（content 可能是 str 或 list[dict]，多模态模型返回 list）
        if chunk.content:
            content = chunk.content
            if isinstance(content, list):
                content = "".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in content
                )
            if content:
                events.append(_format_sse({"content": content}))
        return "".join(events) if events else None

    # llm_node 完整消息（非 chunk）：LangGraph stream_mode="messages" 在节点结束时
    # 会输出节点返回的完整 AIMessage，此时 tool_calls 已由 llm_node 内部合并完整
    # （含全部参数）。流式 chunk 阶段只发 reasoning/content（见上），工具调用参数
    # 必须在这里发送，否则前端只能拿到首块的空 args（"工具调用内容为空"的根因）
    if node == "llm_node" and isinstance(chunk, AIMessage) and not isinstance(chunk, AIMessageChunk):
        if chunk.tool_calls:
            events = []
            for tc in chunk.tool_calls:
                tool_name = tc.get("name", "")
                if tool_name:
                    events.append(_format_sse({
                        "tool_call_start": {
                            "name": tool_name,
                            "args": tc.get("args") or {},
                        }
                    }))
            if events:
                return "".join(events)
        return None

    # tool_node：工具执行结果，发送工具调用结束事件（供前端关闭加载动画）
    if node == "tool_node" and isinstance(chunk, ToolMessage):
        # ToolMessage.content 可能是 str 或 list[dict]（多模态格式），
        # 列表格式需提取 text 字段拼接，否则前端显示原始 JSON
        tool_content = chunk.content
        if isinstance(tool_content, list):
            tool_content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in tool_content
            )
        return _format_sse({
            "tool_call_end": {
                "name": chunk.name,
                "content": str(tool_content)[:300],  # 截断防止工具输出过大
            }
        })

    # classify_node / memory_node 等其他节点：过滤，不输出到前端
    return None


class ChatService:

    POSTGRESQL_DB_URL = os.getenv("POSTGRESQL_DB_URL")
    persist_path: str | Path
    db_url: str


    def __init__(self, db_url=None):
        self.db_url = db_url or os.getenv("POSTGRESQL_DB_URL")
        # 资源占位，open() 里真正创建，close() 里释放
        self.vector_store = None
        self.pool = self.checkpointer = self.store = None
        self.main_graph = None            # 主对话图
        self.retrieve_graph = None     # 改写+重排图
        self.cache = None
        self.redis_client = None
        # 上传文件解析内容缓存：key="{user_id}:{file_id}"，value={"name":..., "content":...}
        # 上传后立即解析并存入，发送消息时从缓存读取拼接到 input_str，避免重复解析
        self._file_content_cache: dict[str, dict] = {}
        # 按用户隔离的 MCP 图缓存：key=user_id，value=(config_hash, graph, mcp_connections)
        # 用户更新 MCP 配置后，下次对话自动重建图（检测 hash 变化）
        self._user_graph_cache: dict[str, tuple[str, object, list]] = {}
        # 正在后台重建用户图的 user_id 集合，避免重复构建
        self._rebuilding_users: set[str] = set()
        # 后台构建拿到 0 工具的失败时间戳：user_id -> monotonic 时间。
        # 零工具不写缓存，用它做失败退避，冷却期内不重复触发后台连接
        self._mcp_build_fail_at: dict[str, float] = {}
        # 零工具失败后的重试冷却时间（秒）
        self._mcp_retry_cooldown = 60.0
        # 全局 MCP 工具（启动时加载的默认服务器），与用户工具合并
        self._global_mcp_tools: list = []
        # 工具常驻事件循环（MCP session 创建与调用必须同循环）
        self._tool_loop = None
        # 进行中的生成任务注册表：thread_id -> {"user_id", "started_at"}
        # 前端刷新后据此判断"该会话回复是否仍在后台生成"，从而自动轮询续接
        # （刷新不中断生成：客户端断连只停推送，worker 线程继续跑完图提交 checkpoint）
        self._active_generations: dict[str, dict] = {}
        self._active_generations_lock = _threading.Lock()

    def open(self, mcp_tools: list | None = None, tool_loop=None, deps=None):
        self._global_mcp_tools = mcp_tools or []
        self._tool_loop = tool_loop
        self._deps = deps  # AppDependencies 容器（依赖注入），None 时各构建函数降级到全局 import
        # 创建向量库：注入 embedding_function，未提供 deps 时 create_vector_store 内部降级
        vec_cfg = load_vector_db_config()
        embed_fn = deps.embedding_function if deps else None
        self.vector_store = create_vector_store(vec_cfg, embedding_function=embed_fn)
        self.pool = ConnectionPool(
            conninfo=self.db_url,
            kwargs={"autocommit": True},
            min_size=1,
            max_size=10,
            timeout=5,  # 借连接 5 秒快速失败，不干等 30 秒
            open=True,
        )  # ← self.
        try:
            self.pool.check()
            logger.success("PostgreSQL连接池初始化成功")
        except Exception as e:
            logger.error(f"PostgreSQL数据库连接失败：{e}")
            raise
        self.checkpointer = CustomPostgresSaver(self.pool)  # ← self.
        self.store = PostgresStore(self.pool)  # ← self.
        self.checkpointer.setup()
        self.store.setup()
        # 图级缓存后端：compile(cache=...) 传入 RedisCache 后，图中所有 CachePolicy 标记的
        # 节点（如 retrieve_node）命中/写入都走 Redis（键前缀 langgraph:cache:，带 TTL），
        # 多 worker 间共享；Redis 不可用时 RedisCache 内部静默降级为不缓存
        self.cache = RedisCache(cache_service.redis)  # ← self.，且 compile 用它
        # 构建检索图：注入 model 和 online_rerank，未提供 deps 时函数内部降级
        rg_model = deps.model if deps else None
        rg_rerank = deps.online_rerank if deps else None
        self.retrieve_graph = build_retrieve_graph(self.vector_store, model=rg_model, online_rerank=rg_rerank)
        # 构建主图：注入 model 和 system_prompt，未提供 deps 时函数内部降级
        mg_model = deps.model if deps else None
        mg_prompt = deps.system_prompt if deps else None
        self.main_graph = build_main_graph(
            retrieve_graph=self.retrieve_graph,
            pool=self.pool,
            checkpointer=self.checkpointer,
            store=self.store,
            model=mg_model,
            system_prompt=mg_prompt,
            cache=self.cache,
            mcp_tools=mcp_tools,
        )
        # 初始化 MCP 配置数据库服务（PostgreSQL 存储，按用户隔离）
        try:
            from service.mcp_config_service import init_mcp_config_service
            init_mcp_config_service(self.pool)
            logger.success("MCP 配置数据库服务已初始化（PostgreSQL）")
        except Exception as e:
            logger.warning(f"MCP 配置数据库服务初始化失败（不影响核心功能）: {e}")


    def close(self, timeout:int =10):
        if self.pool:
            self.pool.close(timeout=timeout)
            logger.info("PostgreSQL连接池已关闭")


    def _get_user_graph(self, user_id: str):
        """获取用户专属的对话图（含用户自定义 MCP 工具），带缓存。

        用户无自定义 MCP 配置时返回全局图；有配置时构建独立图实例并缓存。
        配置变更（hash 变化）时自动重建。
        """
        from config import load_mcp_server_configs
        from mcp_client.client import init_mcp_holders
        from utils.tools_util import safety_filter, tools_embedding
        import asyncio
        import hashlib
        import json

        # 加载用户 MCP 配置
        user_servers = load_mcp_server_configs(user_id=user_id)
        if not user_servers:
            return self.main_graph  # 无用户配置，用全局图

        # 计算配置 hash，检测变更
        config_hash = hashlib.md5(json.dumps(user_servers, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

        # 命中缓存且配置未变
        cached = self._user_graph_cache.get(user_id)
        if cached and cached[0] == config_hash:
            return cached[1]

        # 缓存未命中：触发后台异步构建，先返回全局图（不阻塞 SSE 流式响应）。
        # 首次请求可能用全局图，后台构建完成后下次请求生效。
        # 有旧缓存时先用旧图响应，避免配置变更后首次请求降级到全局图。
        if cached:
            self._rebuild_user_graph_async(user_id, user_servers, config_hash)
            return cached[1]
        # 失败退避：上次后台构建拿到 0 工具且仍在冷却期内，直接返回全局图，
        # 避免坏 server / 冷启动期间每个请求都重复触发后台连接
        import time as _time
        fail_at = self._mcp_build_fail_at.get(user_id)
        if fail_at is not None and (_time.monotonic() - fail_at) < self._mcp_retry_cooldown:
            return self.main_graph
        self._rebuild_user_graph_async(user_id, user_servers, config_hash)
        return self.main_graph

    def _rebuild_user_graph_async(self, user_id, user_servers, config_hash):
        """后台异步构建用户专属图（含 MCP 工具），不阻塞流式响应。

        构建完成后写入 _user_graph_cache，下一次请求命中缓存。
        同一 user_id 同时只允许一个重建任务（_rebuilding_users 去重）。
        """
        if user_id in self._rebuilding_users:
            return
        self._rebuilding_users.add(user_id)

        def _build():
            import asyncio as _asyncio
            try:
                from mcp_client.client import init_mcp_holders
                from utils.tools_util import safety_filter, tools_embedding

                connections = []
                user_tools = []
                if self._tool_loop:
                    try:
                        connections = _asyncio.run_coroutine_threadsafe(
                            init_mcp_holders(user_servers, timeout=120), self._tool_loop
                        ).result(timeout=130)
                        user_tools = [t for conn in connections for t in conn.tools]
                        if user_tools:
                            user_tools = safety_filter(user_tools)
                            tools_embedding(user_tools)
                            logger.info(f"用户 [{user_id}] 后台加载 {len(user_tools)} 个 MCP 工具（{len(connections)} 个服务器）")
                        else:
                            logger.warning(f"用户 [{user_id}] MCP 连接成功但无工具（服务器数={len(connections)}）")
                    except Exception as e:
                        server_names = [s.get('name', '?') for s in user_servers]
                        logger.warning(f"用户 [{user_id}] 后台 MCP 加载失败（服务器={server_names}），降级全局工具: {type(e).__name__}: {e}")

                # 防御：配置了 MCP server 却一个工具都没拿到（多为冷启动下载慢/
                # 临时连接失败）。此时【绝不写缓存】——否则空工具图会被永久命中，
                # 之后配置 hash 不变就再也不会重连，表现为"配了 MCP 却读不到"。
                # 记录失败时间用于退避，关闭本次连接，下次请求（冷却后）重试。
                if user_servers and not user_tools:
                    import time as _time
                    self._mcp_build_fail_at[user_id] = _time.monotonic()
                    if connections and self._tool_loop:
                        try:
                            _asyncio.run_coroutine_threadsafe(
                                self._close_connections(connections), self._tool_loop
                            ).result(timeout=5)
                        except Exception:
                            pass
                    logger.warning(
                        f"用户 [{user_id}] 配置了 {len(user_servers)} 个 MCP server 但拿到 0 工具，"
                        f"本次不缓存，{self._mcp_retry_cooldown:.0f}s 后自动重试"
                    )
                    return

                # 成功拿到工具：清除失败标记
                self._mcp_build_fail_at.pop(user_id, None)
                all_tools = self._global_mcp_tools + user_tools
                user_graph = build_main_graph(
                    retrieve_graph=self.retrieve_graph,
                    pool=self.pool,
                    checkpointer=self.checkpointer,
                    store=self.store,
                    cache=self.cache,
                    mcp_tools=all_tools,
                )
                # 构建完成后关闭旧连接（如果有）
                old_cache = self._user_graph_cache.get(user_id)
                if old_cache and self._tool_loop:
                    try:
                        _asyncio.run_coroutine_threadsafe(
                            self._close_connections(old_cache[2]), self._tool_loop
                        ).result(timeout=5)
                    except Exception:
                        pass
                self._user_graph_cache[user_id] = (config_hash, user_graph, connections)
                logger.info(f"用户 [{user_id}] 专属图后台构建完成（{len(user_tools)} 个用户工具）")
            except Exception as e:
                logger.exception(f"用户 [{user_id}] 后台图构建失败: {e}")
            finally:
                self._rebuilding_users.discard(user_id)

        import threading
        threading.Thread(target=_build, daemon=True).start()

    @staticmethod
    async def _close_connections(connections):
        for conn in connections:
            try:
                await conn.close()
            except Exception:
                pass

    def invoke(self, user_id, thread_id, query) -> str:
        config = {
            "configurable": {"thread_id": thread_id, "user_id": user_id},
            "metadata": {"user_id": user_id},  # 随 checkpoint 写入 metadata
        }
        # 使用用户专属图（含自定义 MCP 工具），无配置时自动降级为全局图
        graph = self._get_user_graph(user_id)
        result = graph.invoke({"input_str": query}, config=config)
        ai_msg = result["messages"][-1]
        return ai_msg.content

    async def a_invoke(self, user_id, thread_id, input_str) -> str:
        """异步版 invoke：同步调用丢进线程池，不阻塞事件循环。"""
        return await asyncio.to_thread(self.invoke, user_id, thread_id, input_str)

    def parse_and_cache_file(self, file_id: int, user_id: str) -> dict:
        """上传文件后立即解析文本内容并缓存（阻塞执行，解析完成才返回）。

        支持 txt/md/csv/json/xml/html/py/js 等纯文本格式；PDF/docx 等二进制格式
        暂不支持解析，返回 content=None。解析结果存入 _file_content_cache，
        发送消息时从缓存读取拼接到 input_str。

        Args:
            file_id: 文件 ID（上传接口返回）
            user_id: 用户 ID

        Returns:
            {"file_id": int, "file_name": str, "content": str|None, "parsed": bool}
        """
        cache_key = f"{user_id}:{file_id}"
        # 已缓存则直接返回，避免重复解析
        if cache_key in self._file_content_cache:
            return self._file_content_cache[cache_key]

        # 获取文件元信息
        file_info = file_upload_service.get_file(file_id, user_id)
        file_name = file_info.get("file_name", f"file_{file_id}") if file_info else f"file_{file_id}"

        # 解析文本内容
        content = file_upload_service.extract_text_from_file(file_id, user_id)
        parsed = content is not None and len(content) > 0

        result = {
            "file_id": file_id,
            "file_name": file_name,
            "content": content if parsed else None,
            "parsed": parsed,
        }
        self._file_content_cache[cache_key] = result
        logger.info(f"文件解析完成 file_id={file_id}, user_id={user_id}, parsed={parsed}, content_len={len(content) if content else 0}")
        return result

    def _build_input_with_files(self, input_str: str, file_ids: list[int], user_id: str) -> str:
        """将用户输入与上传文件解析内容拼接，作为最终 input_str 传入 llm_node。

        拼接格式：
            用户输入：{input_str}

            --- 以下为用户上传的文件内容 ---
            【文件名1】
            {文件内容1}

            【文件名2】
            {文件内容2}

        Args:
            input_str: 用户原始输入
            file_ids: 上传文件 ID 列表
            user_id: 用户 ID

        Returns:
            拼接后的完整输入文本
        """
        if not file_ids:
            return input_str

        file_sections = []
        for fid in file_ids:
            cache_key = f"{user_id}:{fid}"
            cached = self._file_content_cache.get(cache_key)
            # 缓存未命中则实时解析（兜底）
            if not cached:
                cached = self.parse_and_cache_file(fid, user_id)
            if cached.get("parsed") and cached.get("content"):
                file_sections.append(f"【{cached['file_name']}】\n{cached['content']}")

        if not file_sections:
            return input_str

        file_block = "--- 以下为用户上传的文件内容 ---\n" + "\n\n".join(file_sections)
        if input_str.strip():
            return f"{input_str}\n\n{file_block}"
        return file_block

    def clear_file_cache(self, user_id: str, file_id: int = None):
        """清除文件解析缓存（删除文件时调用）。

        Args:
            user_id: 用户 ID
            file_id: 文件 ID，None 表示清除该用户所有缓存
        """
        if file_id is not None:
            self._file_content_cache.pop(f"{user_id}:{file_id}", None)
        else:
            keys_to_remove = [k for k in self._file_content_cache if k.startswith(f"{user_id}:")]
            for k in keys_to_remove:
                del self._file_content_cache[k]

    def _build_stream_config(self, user_id, thread_id, user_info, thinking_mode=False, reasoning_effort="low") -> dict:
        """构建流式对话的 LangGraph config。

        请求级用户上下文随 config 传入图（工具通过 RunnableConfig 参数读取），
        不依赖 contextvars：StreamingResponse 每次 next() 都在新线程/新 context 执行，
        contextvars 的 set/reset 会跨 context 报错且 get() 拿不到值。

        Args:
            user_id: 用户 ID
            thread_id: 会话 ID
            user_info: 用户上下文信息

        Returns:
            LangGraph config dict
        """
        return {
            "configurable": {
                "thread_id": thread_id,
                "user_id": user_id,
                "user_info": user_info,
                # 深度思考配置：随 config 传入图，llm_node 中读取并动态 bind
                "thinking_mode": thinking_mode,
                "reasoning_effort": reasoning_effort,
            },
            "metadata": {"user_id": user_id},  # 随 checkpoint 写入 metadata
        }

    def stream(self, user_id, thread_id, input_str, user_info=None, file_ids: list[int] = None, thinking_mode: bool = False, reasoning_effort: str = "low"):
        """流式对话生成（SSE）。

        编排逻辑：拼接文件内容 → 构建 config → 后台线程遍历图 → 队列转发 → 过滤节点 → 格式化 SSE 事件。
        节点过滤和 SSE 格式化由模块级 _process_graph_chunk / _format_sse 处理。

        【断连不中断生成】图执行放在独立守护线程，SSE 生成器只消费队列。
        客户端刷新/断开（GeneratorExit）时只停止推送，后台线程继续跑完图并
        提交 checkpoint——否则 AI 回复（含工具调用链）会随连接断开被 LangGraph
        取消执行，刷新后历史里只剩用户消息（"刷新后会话内容清空"的根因）。

        Args:
            user_id: 用户 ID
            thread_id: 会话 ID
            input_str: 用户输入文本
            user_info: 用户上下文信息
            file_ids: 上传文件 ID 列表，解析内容会拼接到 input_str 传入 llm_node
            thinking_mode: 是否开启深度思考模式（前端用户选择）
            reasoning_effort: 推理强度 low/high/max（仅 thinking_mode=True 时生效）

        Yields:
            SSE 事件字符串（"data: ...\n\n" 格式）
        """
        import queue as _queue
        import threading as _threading

        # 1. 拼接用户输入与上传文件解析内容（文件内容作为上下文传入 LLM）
        if file_ids:
            input_str = self._build_input_with_files(input_str, file_ids, user_id)
            logger.info(f"拼接文件内容后 input_str 长度: {len(input_str)}, 文件数: {len(file_ids)}")
            # 文件内容已拼入本次消息，清除内存解析缓存避免累积（数据库文件记录保留）
            for fid in file_ids:
                self._file_content_cache.pop(f"{user_id}:{fid}", None)

        config = self._build_stream_config(user_id, thread_id, user_info, thinking_mode, reasoning_effort)

        # 2. 后台线程跑图：chunk → SSE 事件字符串 → 队列
        #    图必须走 _get_user_graph（用户专属图，含自定义 MCP 工具），
        #    不能直接用 self.main_graph，否则数据库中的用户 MCP 配置不会被加载
        #    （invoke 非流式路径已正确使用，stream 此前漏掉导致工具=0）
        event_queue: _queue.Queue = _queue.Queue()
        _SENTINEL = object()

        def _run_graph():
            try:
                graph = self._get_user_graph(user_id)
                for chunk, meta in graph.stream(
                    {"input_str": input_str},
                    config=config,
                    stream_mode="messages",
                ):
                    event = _process_graph_chunk(chunk, meta)
                    if event:  # None 表示该 chunk 被过滤（classify/memory 节点）
                        event_queue.put(event)
                event_queue.put(_SENTINEL)
            except Exception as e:
                # 图执行异常：记录完整堆栈并推送错误事件，
                # 避免 SSE 静默断流导致前端报"发送消息失败"而日志无迹
                logger.exception(f"对话流生成异常（thread_id={thread_id}）：{e}")
                event_queue.put(_format_sse({"error": str(e), "error_type": type(e).__name__}))
                event_queue.put(_SENTINEL)
            finally:
                # 无论成功/异常/断连，worker 结束都从注册表移除——
                # 前端刷新后若查不到该会话的进行中任务，即认为回复已落库/已失败
                with self._active_generations_lock:
                    self._active_generations.pop(thread_id, None)
                    logger.debug(f"[gen-registry] worker 结束注销 thread_id={thread_id} 剩余={list(self._active_generations.keys())}")

        # 注册进行中任务：前端可查询"该会话是否仍在后台生成"，决定是否轮询续接
        with self._active_generations_lock:
            self._active_generations[thread_id] = {"user_id": user_id, "started_at": _threading.get_ident()}
            logger.debug(f"[gen-registry] 注册 thread_id={thread_id} 当前={list(self._active_generations.keys())}")

        worker = _threading.Thread(target=_run_graph, daemon=True, name=f"mcp-stream-{thread_id[-12:]}")
        worker.start()

        # 3. 主生成器：消费队列转发 SSE。客户端断开时只退出推送，
        #    不 stop 后台线程——图继续执行并提交 checkpoint，刷新后历史完整。
        try:
            while True:
                item = event_queue.get()
                if item is _SENTINEL:
                    break
                yield item
            yield _format_sse("[DONE]")
        except GeneratorExit:
            # 客户端断开连接时 StreamingResponse 会关闭生成器：静默退出，
            # 后台 worker 线程继续跑完图（daemon 线程，进程退出时自动终止）
            raise

    def get_history_session(self, thread_id: str):
        config = {
            "configurable": {
                "thread_id": thread_id
            }
        }
        snapshot = self.main_graph.get_state(config)
        if not snapshot or len(snapshot) == 0:
            logger.error(f"会话:{thread_id}记录不存在")
            return {"code": 404, "message": f"会话:{thread_id}记录不存在"}
        state_data = snapshot.values
        history_messages = state_data.get("messages", [])
        if not history_messages:
            logger.error(f"会话:{thread_id}记录不存在")
            return []
        history_session = []
        for message in history_messages:
            history_session.append(
                {
                    "role": f"{message.type}",
                    "content": message.content
                }
            )
        return history_session

    def rollback_session(self, thread_id: str, query: str):
        """回滚会话到指定轮次：删除该轮用户消息及其后的全部消息（旧 AI 回复、工具链）。

        供前端"重新生成"调用：重新生成前先删除后端 checkpoint 中该轮的旧回复，
        避免旧回复残留在历史中导致新回复叠加、token 重复累积。

        Args:
            thread_id: 会话 ID
            query: 该轮用户消息文本（定位锚点，取最后一个匹配项）

        Returns:
            (flag: bool, message: str)
        """
        config = {"configurable": {"thread_id": thread_id}}
        try:
            snapshot = self.main_graph.get_state(config)
            if not snapshot or len(snapshot) == 0:
                return False, f"会话:{thread_id}记录不存在"
            messages = snapshot.values.get("messages", [])
            if not messages:
                return False, "会话无消息可回滚"

            # 从后往前找最后一个 content 等于 query 的 HumanMessage（该轮起点）
            target_idx = None
            for i in range(len(messages) - 1, -1, -1):
                m = messages[i]
                if m.type == "human" and str(m.content) == query:
                    target_idx = i
                    break
            if target_idx is None:
                return False, f"未找到该轮用户消息（query 前 20 字：{query[:20]}）"

            # 删除该轮起点及其后的所有消息（AI 回复、ToolMessage 工具链）
            from langgraph.graph.message import RemoveMessage
            remove = [RemoveMessage(id=m.id) for m in messages[target_idx:]]
            # update_state 触发 add_messages reducer：RemoveMessage 删除指定 id 的消息，
            # 其他消息保留——不能直接传裁剪后列表（add_messages 是追加语义，不会覆盖）
            self.main_graph.update_state(config, {"messages": remove})
            logger.info(f"会话:{thread_id} 回滚成功：删除 {len(remove)} 条消息（第 {target_idx} 条起）")
            return True, f"已回滚该轮回复，删除 {len(remove)} 条消息"
        except Exception as e:
            logger.error(f"回滚会话{thread_id}异常：{e}", exc_info=True)
            return False, f"回滚失败：{str(e)}"



    def get_thread_user_id(self, thread_id: str):
        """查询会话归属用户（用于 history/delete 接口的归属校验）"""
        # CustomPostgresSaver 扩展参数：SQL 层 WHERE thread_id = %s 精确定位该会话（最新在前），
        # 不再全量遍历所有线程
        for item in self.checkpointer.list(thread_id=thread_id):
            # LangGraph 1.x：config 的 metadata 落在 CheckpointTuple.metadata（checkpoints 表 metadata 列），
            # checkpoint JSON 内部没有 metadata 字段；configurable 只持久化 thread_id，也不含 user_id
            owner = None
            if isinstance(item.metadata, dict):
                owner = item.metadata.get("user_id")
            if not owner and isinstance(item.checkpoint, dict):
                owner = item.checkpoint.get("metadata", {}).get("user_id")  # 兼容旧版本存储
            if owner:
                return str(owner)
        return None

    def is_generation_active(self, thread_id: str) -> bool:
        """该会话是否仍有生成任务在后台运行。

        前端刷新后调用：若返回 True，说明 AI 回复仍在后台生成（客户端断连不中断
        生成），应轮询 history 直到拿到完整回复；返回 False 则回复已落库或已失败，
        直接读 history 即可。

        Args:
            thread_id: 会话 ID

        Returns:
            bool: 是否正在后台生成
        """
        with self._active_generations_lock:
            return thread_id in self._active_generations

    def get_memory(self, user_id: str):
        store = self.store
        memory = ""
        item = store.get(("rag_chat", user_id), "user_profile")
        if item and item.value.get("profile"):
            memory = item.value["profile"]
        return memory

    def delete_session_by_id(self, thread_id: str):
        checkpointer = self.checkpointer
        flag = False
        try:
            checkpointer.delete_thread(thread_id)
            logger.info(f"删除会话:{thread_id}成功")
            flag = True
            return flag, f"删除会话:{thread_id}成功"
        except KeyError:
            # LangGraph checkpointer: thread不存在抛出 KeyError
            logger.warning(f"删除会话:{thread_id}，会话记录不存在")
            return flag, f"会话:{thread_id}记录不存在"
        except Exception as e:
            logger.error(f"删除会话{thread_id}异常，err={repr(e)}", exc_info=True)
            return flag, f"删除会话失败：{str(e)}"


    def get_user_sessions(self, user_id: str):
        checkpointer = self.checkpointer

        from langgraph.checkpoint.base import CheckpointTuple

        latest_by_thread: dict[str, CheckpointTuple] = {}

        # CustomPostgresSaver 扩展参数 user_id：数据库层执行 metadata @> '{"user_id": ...}' 过滤，
        # 只返回该用户的 checkpoint，避免全表扫描后在 Python 层逐个跳过
        for item in checkpointer.list(None, user_id=user_id):
            tid = item.config["configurable"]["thread_id"]
            # 兼容旧版本存储：checkpoint JSON 内可能没有 metadata 字段
            owner = None
            if isinstance(item.metadata, dict):
                owner = item.metadata.get("user_id")
            if not owner and isinstance(item.checkpoint, dict):
                owner = item.checkpoint.get("metadata", {}).get("user_id")  # 兼容旧版本存储
            if owner != user_id:
                continue
            if tid not in latest_by_thread:
                latest_by_thread[tid] = item

        sessions = []
        for tid, item in latest_by_thread.items():
            messages = item.checkpoint["channel_values"].get("messages", [])
            first_user = next((m for m in messages if m.type == "human"), None)
            sessions.append({
                "thread_id": tid,
                "title": first_user.content[:20] if first_user else "新会话",
                "last_updated": item.checkpoint["ts"],
            })
        sessions.sort(key=lambda s: s["last_updated"], reverse=True)
        return sessions

    def check_db_health(self):
        import psycopg
        from psycopg_pool import PoolTimeout
        pool = self.pool
        try:
            with pool.connection() as conn:  # 从池中借连接（空闲不足会抛 PoolTimeout）
                conn.execute("SELECT 1")  # 真正发一条查询验证链路
            return {"status": "ok", "db": True}
        except PoolTimeout:
            logger.warning("数据库连接池已满或无法建立连接")
            return {"status": "degraded", "db": False}
        except psycopg.OperationalError as e:  # psycopg3 的异常就在 psycopg 顶层
            logger.error(f"数据库不可用: {e}")
            return {"status": "degraded", "db": False}

chat_service = ChatService()


