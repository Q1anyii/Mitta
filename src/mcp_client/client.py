"""
MCP客户端管理层
目标：对接MCP服务，输出LangChain BaseTool供给LangGraph Agent使用。

参考MCP官方简单demo无法直接工程使用，会存在：
1. stdio异步生成器GC回收导致连接静默断开；
2. Windows平台子进程失败CancelledError污染事件循环；
3. 多MCP服务需要单服务故障降级；
4. 子进程资源泄漏问题。

本模块在MCP、langchain_mcp_adapters官方API基础上，借助AI完成工程健壮性封装；
已经完成调试验证，理解各个防御逻辑对应的故障场景。

核心分层：
- McpToolHolder：MCP工具简易封装，屏蔽协议细节
- McpServerConnection：单个MCP服务完整生命周期管理
- init_mcp_holders：批量初始化、故障降级
"""

import asyncio
import os
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.tools import load_mcp_tools
from loguru import logger
from mcp import ClientSession, StdioServerParameters, stdio_client
from mcp.client.sse import sse_client

from mcp_client.mcp_tool_holder import McpToolHolder


# 工具调用超时（秒）：MCP 工具执行超时抛 TimeoutError，由 ToolNode 转错误消息，
# 不会中断整条对话链路；远程工具较慢时可按需调大
TOOL_CALL_TIMEOUT = 30

# MCP 服务器名 → 规则层 tags 映射：让 rule_based_filter 能根据 query 关键词命中对应工具
# tags 含中英文关键词，覆盖用户常见表述（如"写文件""读目录""git提交"）
SERVER_TAGS = {
    "filesystem": ["文件", "目录", "读写", "读", "写", "file", "filesystem", "folder", "path", "路径"],
    "git": ["git", "提交", "版本", "仓库", "commit", "diff", "log", "分支", "branch"],
    "fetch": ["网页", "url", "抓取", "fetch", "http", "链接", "网站"],
    "sqlite": ["数据库", "sql", "查询", "sqlite", "db", "数据"],
    "sequential-thinking": ["思考", "推理", "分步", "排错", "分析", "thinking"],
    "memory": ["记忆", "知识图谱", "实体", "关系", "memory", "记录"],
    "context7": ["文档", "api", "库", "版本", "参数", "context7", "最新文档", "docs"],
    "markitdown": ["转换", "markdown", "pdf", "word", "excel", "图片", "文件转", "markitdown", "转成"],
    "dbhub": ["数据库", "sql", "查询", "postgres", "mysql", "dbhub", "表", "数据"],
    "crawl4ai": ["爬虫", "网页", "pdf", "抓取", "crawl", "爬取"],
    "chroma": ["向量", "知识库", "检索", "chroma", "相似", "embedding", "集合"],
    "basic-memory": ["记忆", "笔记", "知识", "实体", "markdown", "关系", "memory"],
    "mitta-tools": ["网络搜索", "搜索", "网页", "抓取", "git", "提交", "分支", "commit", "diff", "log", "文件", "项目", "search", "web", "代码", "版本"],
}

# 工具级 tags：对特定工具做个性化关键词注入（优先于 server 级 tags）。
# MCP 服务器按 server 注入同一组 tags 时粒度太粗，git_status/git_diff 等
# 难以被"哪些文件被修改"这类 query 命中；按工具补充语义关键词提升规则层召回。
TOOL_TAGS: dict[str, list[str]] = {
    "git_status": ["修改", "变更", "状态", "工作区", "未提交", "改动", "changed", "modified"],
    "git_diff": ["差异", "改动", "变更", "对比", "diff", "修改内容"],
    "git_log": ["提交历史", "日志", "commit history", "log"],
    "git_branch": ["分支", "branch", "新分支", "创建分支"],
    "git_checkout": ["切换", "checkout", "分支", "switch"],
    "web_search": ["搜索", "查询", "搜一下", "查找", "最新", "search", "web", "网络", "新闻"],
    "fetch_url": ["抓取", "网页", "url", "链接", "内容", "页面", "fetch", "抓"],
    "search_project_files": ["搜索文件", "文件", "检索", "查找文件", "todo", "search files", "文件名"],
    "get_project_info": ["项目结构", "代码结构", "目录结构", "项目概览", "模块", "project info"],
}


def make_sync_tool(async_tool: BaseTool, loop: asyncio.AbstractEventLoop, timeout: int = TOOL_CALL_TIMEOUT, server_name: str = "") -> BaseTool:
    """async 工具 → sync 工具：调用提交到工具常驻事件循环。

    langchain_mcp_adapters 的 load_mcp_tools 生成 async 工具，闭包捕获绑定
    创建时事件循环的 ClientSession。同步图（ToolNode）在线程池线程执行时会
    临时新建事件循环调用 async 工具，跨循环操作 session 会失败/挂起
    （Windows 下 mcp 库 cancel scope 泄漏还会注入 CancelledError 中断整图）。
    包装后所有调用提交到与 session 同循环的工具常驻循环，规避跨循环；
    timeout 防挂死，超时由 ToolNode 转错误消息，不中断对话链路。

    server_name：注入基于服务器名的 tags，供 ToolFilter 规则层命中（MCP 工具原生无 tags）。
    """
    from langchain_core.tools import StructuredTool

    async def _ainvoke(**kwargs):
        return await async_tool.ainvoke(kwargs)

    def _invoke(**kwargs):
        future = asyncio.run_coroutine_threadsafe(_ainvoke(**kwargs), loop)
        return future.result(timeout=timeout)

    # 基于服务器名注入 tags：规则层 rule_based_filter 靠 tags 命中，MCP 工具原生无 tags
    tags = list(SERVER_TAGS.get(server_name, []))
    # 工具级个性化 tags 优先（按工具名覆盖），提升"哪些文件被修改"等弱关键词 query 的规则命中
    tool_tags = TOOL_TAGS.get(async_tool.name)
    if tool_tags:
        tags = list(set(tags + tool_tags))
    # 合并原工具的 tags（如有）
    if getattr(async_tool, "tags", None):
        tags = list(set(tags + list(async_tool.tags)))

    return StructuredTool.from_function(
        func=_invoke,
        name=async_tool.name,
        description=async_tool.description or "",
        args_schema=async_tool.args_schema,
        metadata=async_tool.metadata or {},  # 保留 destructiveHint 等安全元数据
        tags=tags if tags else None,
    )


class McpServerConnection:
    """连接单个 MCP 服务器：生命周期管理 + 工具加载（每个连接独立 AsyncExitStack）"""

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self._stack = AsyncExitStack()
        self.session: ClientSession | None = None
        self.tools: list[BaseTool] = []          # LangChain 工具（供图 bind_tools / ToolNode）
        self.holders: list[McpToolHolder] = []   # 按工具的统一调用封装（可选）

    @staticmethod
    def _build_env(extra: dict[str, str]) -> dict[str, str]:
        """构造子进程环境：继承父进程全部环境变量，并把解释器同目录的 Scripts
        （conda/venv 下的 uvx.exe、pip.exe 等）注入 PATH 最前。

        注意：该注入不影响 CreateProcess 的可执行文件搜索（它按父进程 PATH
        搜），只影响子进程内部再启动命令时的解析；命令本身能否启动由
        _resolve_command 用绝对路径保证。
        """
        env = dict(os.environ)
        env.update(extra)
        scripts = str(Path(sys.executable).resolve().parent / "Scripts")
        if scripts not in env.get("PATH", "").split(os.pathsep):
            env["PATH"] = scripts + os.pathsep + env.get("PATH", "")
        return env

    @staticmethod
    def _resolve_command(command: str) -> str:
        """解析启动命令：Windows 下 CreateProcess 按父进程 PATH 搜索可执行文件
        （env 里的 PATH 不影响搜索），PyCharm/未激活终端里 conda Scripts 不在
        PATH，裸命令 uvx 会 WinError 2。若命令在解释器 Scripts 目录存在，
        补全为绝对路径。
        """
        if os.name != "nt" or os.path.isabs(command):
            return command
        scripts = Path(sys.executable).resolve().parent / "Scripts"
        for candidate in (scripts / command, scripts / f"{command}.exe"):
            if candidate.is_file():
                return str(candidate)
        return command

    async def open(self) -> None:
        """建立连接、初始化会话并加载全部工具。

        连接失败统一转为 ConnectionError 抛出（含 Windows 下 mcp 库抛出的内部
        CancelledError），由调用方降级处理，不阻塞主流程。
        """
        server_type = self.cfg.get("type", "stdio")
        if server_type == "stdio":
            # 容器环境下用户配置的 cwd 由 Windows 路径转换而来（/app/user_files/...），
            # 这些目录首次使用时并不存在，而 asyncio 子进程在 cwd 不存在时会直接抛
            # FileNotFoundError，导致整个 MCP 连接失败。启动前自动补齐工作目录。
            cwd = self.cfg.get("cwd")
            if cwd:
                try:
                    os.makedirs(cwd, exist_ok=True)
                except OSError as _e:
                    logger.warning(f"MCP [{self.cfg.get('name')}] 创建工作目录失败 {cwd}: {_e}")
            # filesystem 类 MCP 通过 args 传入“允许访问的目录”，这些目录不存在时
            # 服务端会启动失败；对 args 中的绝对路径一并补齐
            args = self.cfg.get("args", [])
            joined_args = " ".join(str(a) for a in args).lower()
            if "filesystem" in joined_args:
                for a in args:
                    a_str = str(a)
                    if a_str.startswith("/") and not a_str.startswith("-"):
                        try:
                            os.makedirs(a_str, exist_ok=True)
                        except OSError:
                            pass
            params = StdioServerParameters(
                command=self._resolve_command(self.cfg["command"]),
                args=args,
                cwd=cwd,
                env=self._build_env(self.cfg.get("env") or {}),
            )
            # 预检：服务器脚本必须存在。避免子进程启动失败触发 mcp 库在
            # Windows 上的取消作用域泄漏 bug（anyio cancel scope 跨任务退出），
            # 该 bug 会污染同一事件循环中后续服务器的连接
            # 注意：只对 python/python3/py 等直接运行脚本的命令做检查；
            # uvx/npx/pipx 等包管理器的 args[0] 是包名，不是本地脚本路径
            _SCRIPT_RUNNERS = {"python", "python3", "py"}
            if params.command in _SCRIPT_RUNNERS and params.args and not params.args[0].startswith("-"):
                script = Path(params.cwd or ".") / params.args[0]
                if not script.exists():
                    raise ConnectionError(f"MCP 服务器脚本不存在：{script}")
            cm = stdio_client(params)
        elif server_type == "sse":
            cm = sse_client(self.cfg["url"])
        else:
            raise ValueError(f"不支持的 MCP 服务器类型: {server_type}")

        try:
            read, write = await self._enter_context(cm)
            session = await self._enter_context(ClientSession(read, write))
            await session.initialize()
        except (Exception, asyncio.CancelledError) as e:
            raise ConnectionError(
                f"MCP 服务器 [{self.cfg.get('name', server_type)}] 连接失败：{e}"
            ) from e
        self.session = session

        # LangChain 工具：适配器自动完成 JSON Schema → pydantic 转换，闭包捕获 session
        self.tools = await load_mcp_tools(session)
        # 同步包装：ToolNode 在线程池线程执行，async 工具跨事件循环调用 session 会失败，
        # 统一提交到当前（工具常驻）事件循环，与 session 创建循环保持一致（见 make_sync_tool）
        # 传入 server_name 注入 tags，供 ToolFilter 规则层命中
        server_name = self.cfg.get('name', '')
        self.tools = [make_sync_tool(t, asyncio.get_running_loop(), server_name=server_name) for t in self.tools]
        # 同时保留按工具名的统一调用封装
        tools_result = await session.list_tools()
        for tool in tools_result.tools:
            self.holders.append(McpToolHolder(
                name=tool.name,
                description=tool.description or "",
                input_schema=tool.inputSchema,
                session=session,
                server_name=self.cfg.get("name", server_type),
            ))

    async def _enter_context(self, cm) -> Any:
        """进入 async 上下文管理器并登记到退出栈；进入失败时显式关闭底层生成器。

        成功路径必须交给 self._stack.enter_async_context 登记：stdio_client 是
        async generator，若只有局部变量持有，open() 返回后会被 GC 提前 aclose，
        导致会话连接静默关闭（call_tool 报 Connection closed）。

        失败路径（Windows 下子进程启动失败时 mcp 库抛 CancelledError）下
        enter_async_context 不会登记退出回调，这里手动 aclose() 让内部任务
        正常退出，并吞掉其清理阶段的异常（mcp/anyio 已知跨任务 cancel scope 问题）。
        """
        try:
            return await self._stack.enter_async_context(cm)
        except BaseException:
            closer = getattr(cm, "aclose", None)
            if closer is not None:
                try:
                    await closer()
                except BaseException:
                    pass
            raise

    async def close(self) -> None:
        """按启动逆序释放连接（先关会话再关传输）。

        mcp SDK 在 Windows asyncio 下关闭 stdio 传输时可能抛 cancel scope
        相关 RuntimeError（库已知问题），关闭失败连接已无可用性，吞掉即可。
        """
        try:
            await self._stack.aclose()
        except BaseException:
            pass


async def init_mcp_holders(
    servers: list[dict[str, Any]], timeout: int = 120, groups: list[str] | None = None
) -> list[McpServerConnection]:
    """按配置连接全部 MCP 服务器，返回连接列表。

    - connections[i].tools：LangChain 工具列表（供图使用）
    - connections[i].holders：按工具的统一调用封装（可选）
    - 关闭：for conn in connections: await conn.close()

    单个服务器连接失败只跳过该服务器并告警，不影响其他服务器与主流程
    （图内工具列表缺少远程工具时自动降级为纯 LLM 回答）。

    Args:
        servers: MCP 服务器配置列表
        timeout: 单个服务器连接超时时间（秒），默认 120 秒。
                 冷启动时 uvx/npx 首次运行需下载依赖（数十 MB），15 秒极易超时
                 导致全部服务器被跳过，故调大默认值。
                 防止 MCP 服务器启动后 stdio 通信无响应时阻塞整个后端启动。
    """
    if groups is not None:
        groups_set = set(groups)
        servers = [c for c in servers if c.get("group", "first_party") in groups_set]
        logger.info(f"MCP 分组启动：仅连接 {groups_set}，共 {len(servers)} 台")

    async def _connect_one(cfg: dict[str, Any]) -> McpServerConnection | None:
        """连接单个服务器（每个连接在独立 task 中执行）。

        必须在独立 task 里调用 open()：mcp 库 stdio_client 的 anyio cancel
        scope 归属创建它的 task，Windows 下子进程快速失败时 scope 跨任务退出
        泄漏的取消只作用于本任务（已失败，无影响），不会像顺序版那样注入到
        后续连接的 await 点甚至 lifespan 协程。
        """
        server_name = cfg.get('name', cfg.get('type', 'unknown'))
        conn = McpServerConnection(cfg)
        try:
            # 超时保护：单个 MCP 服务器连接超时后跳过，不阻塞主流程
            await asyncio.wait_for(conn.open(), timeout=timeout)
            return conn
        except asyncio.TimeoutError:
            logger.warning(
                f"MCP 服务器 [{server_name}] 连接超时（{timeout}s），已跳过。"
                f"请检查该服务器是否能正常响应 stdio 通信。"
            )
        except (Exception, asyncio.CancelledError) as e:
            # 必须捕获 CancelledError：mcp 库泄漏的取消会异步注入到 await 点，
            # 捕获后降级为告警跳过，避免污染主流程（本任务隔离后即使漏网也
            # 只影响本任务）。
            logger.warning(
                f"MCP 服务器 [{server_name}] 连接失败，已跳过：{e}"
            )
        await conn.close()   # 兜底释放 open() 已登记的部分资源
        return None

    # 并发连接全部服务器：启动更快，且单个失败被任务隔离，互不污染
    results = await asyncio.gather(*(_connect_one(cfg) for cfg in servers))
    return [c for c in results if c is not None]


async def demo_call(servers: list[dict[str, Any]]) -> None:
    """调试入口：连接后调用每个服务器的第一个工具，验证链路后关闭"""
    connections = await init_mcp_holders(servers)
    for conn in connections:
        for holder in conn.holders:
            # 仅演示：用空参数调用第一个工具（工具实际入参需按 input_schema 提供）
            logger.info(f"调用 {holder.server_name}.{holder.name} ...")
            await holder.call({})
    for conn in connections:
        await conn.close()


class McpLazyLoader:
    """第三方 MCP 懒加载器（方案B：分组 + 分级启动）。

    设计目标（1.6G 内存服务器约束）：
    - 第三方扩展 server 进程不常驻（context7/dbhub 等低频工具，常驻浪费内存）；
    - 启动期「闪连预热」：临时连接拿工具 schema（供工具向量索引语义召回），
      随即关闭，不保持进程；
    - 运行期「命中触发」：ToolFilter 语义层命中未连接第三方工具时，
      由 llm_node 调用 trigger() 同步拉起真实连接，工具注入可变工具池，
      本轮重试筛选后即可用。

    失败语义：预热失败 / 连接失败的 server 进入 _failed，不再重试，
    不阻塞主链路（对应工具降级为不可用，LLM 如实告知）。
    """

    def __init__(
        self,
        servers: list[dict[str, Any]],
        tool_loop: asyncio.AbstractEventLoop,
        timeout: int = 120,
        safety_fn=None,
    ):
        self._lazy_cfgs: dict[str, dict[str, Any]] = {
            cfg["name"]: cfg for cfg in servers if cfg.get("lazy")
        }
        self._tool_loop = tool_loop
        self._timeout = timeout
        self._safety_fn = safety_fn
        self._connections: dict[str, McpServerConnection] = {}
        self._loaded_tools: list[BaseTool] = []
        self._server_of_tool: dict[str, str] = {}
        self._tool_metas: list[dict] = []
        self._failed: set[str] = set()
        self._loading: set[str] = set()

    # ---------- 预热：闪连拿 schema，不保持进程 ----------
    async def _warmup_one(self, name: str, cfg: dict) -> list[dict] | None:
        conn = McpServerConnection(cfg)
        try:
            await asyncio.wait_for(conn.open(), timeout=min(self._timeout, 30))
        except (Exception, asyncio.CancelledError) as e:
            logger.warning(f"MCP 懒加载预热 [{name}] 失败（标记不可用）: {e}")
            self._failed.add(name)
            await conn.close()
            return None
        metas = []
        try:
            for h in conn.holders:
                metas.append({
                    "name": h.name,
                    "description": h.description or "",
                    "tags": SERVER_TAGS.get(name, []),
                })
                self._server_of_tool[h.name] = name
        finally:
            await conn.close()  # 闪连即关：只取 schema，不常驻进程
        return metas

    def warmup(self) -> "McpLazyLoader":
        """启动期闪连全部懒加载 server，收集工具 schema（不阻塞主流程超时）。"""
        if not self._lazy_cfgs:
            return self

        async def _run():
            for name, cfg in self._lazy_cfgs.items():
                metas = await self._warmup_one(name, cfg)
                if metas:
                    self._tool_metas.extend(metas)

        fut = asyncio.run_coroutine_threadsafe(_run(), self._tool_loop)
        try:
            fut.result(timeout=min(60, 15 + 15 * len(self._lazy_cfgs)))
        except Exception as e:
            logger.warning(f"MCP 懒加载预热未完成（不阻塞启动）: {e}")
        logger.info(
            f"MCP 懒加载：{len(self._lazy_cfgs)} 台第三方，预热 {len(self._tool_metas)} 个工具 schema"
        )
        return self

    # ---------- 查询 ----------
    def tool_metas(self) -> list[dict]:
        """预热拿到的第三方工具 schema（供工具向量索引，不入执行池）。"""
        return list(self._tool_metas)

    def server_of(self, tool_name: str) -> str | None:
        return self._server_of_tool.get(tool_name)

    def is_pending_tool(self, tool_name: str) -> bool:
        """工具属于懒加载 server 且当前未连接（未加载到执行池）。"""
        server = self._server_of_tool.get(tool_name)
        if not server:
            return False
        if server in self._failed:
            return False
        if server in self._connections:
            return False
        return True

    def loaded_tools(self) -> list[BaseTool]:
        return list(self._loaded_tools)

    # ---------- 触发：命中即真实连接 ----------
    def trigger(self, server_name: str) -> bool:
        """同步触发真实连接（阻塞当前线程直到完成），成功后工具注入池。

        在线程池线程（同步图）里调用：连接提交到工具常驻循环，
        与第一方连接同 loop，保证 session 与工具调用同循环。
        """
        if server_name in self._failed or server_name in self._connections:
            return server_name in self._connections
        if server_name in self._loading:
            logger.warning(f"MCP 懒加载 [{server_name}] 连接中，跳过重复触发")
            return False
        cfg = self._lazy_cfgs.get(server_name)
        if not cfg:
            return False
        self._loading.add(server_name)
        conn: McpServerConnection | None = None
        try:
            async def _do():
                c = McpServerConnection(cfg)
                try:
                    await asyncio.wait_for(c.open(), timeout=self._timeout)
                    return c
                except (Exception, asyncio.CancelledError):
                    await c.close()
                    return None

            fut = asyncio.run_coroutine_threadsafe(_do(), self._tool_loop)
            conn = fut.result(timeout=self._timeout + 10)
        except Exception as e:
            logger.warning(f"MCP 懒加载 [{server_name}] 连接异常: {e}")
            return False
        finally:
            self._loading.discard(server_name)
        if conn is None:
            self._failed.add(server_name)
            logger.warning(f"MCP 懒加载 [{server_name}] 连接失败，标记不可用")
            return False
        tools = conn.tools
        if self._safety_fn is not None:
            tools = self._safety_fn(tools) or []
        self._connections[server_name] = conn
        self._loaded_tools.extend(tools)
        logger.success(
            f"MCP 懒加载：server [{server_name}] 已连接，新增 {len(tools)} 个工具"
        )
        return True

    async def aclose(self) -> None:
        """关闭所有已连接的懒加载 server（进程级释放）。"""
        for conn in self._connections.values():
            try:
                await conn.close()
            except BaseException:
                pass
        self._connections.clear()
        self._loaded_tools.clear()
        logger.info(f"MCP 懒加载：已释放 {len(self._lazy_cfgs)} 台第三方连接")
