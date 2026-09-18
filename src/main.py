import asyncio
# DeepSeek reasoning_content 兼容补丁：必须在任何 ChatOpenAI/init_chat_model
# 创建模型之前应用，否则 langchain-openai 会丢弃思维链字段
from utils.deepseek_patch import apply_patch as _apply_deepseek_patch
_apply_deepseek_patch()

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse as FastAPIJSONResponse
from loguru import logger

from container import AppDependencies
from config import validate_config, load_mcp_server_configs
from mcp_client.client import McpLazyLoader, init_mcp_holders
from mcp_client.mcp_server.agent_server import mcp
from routers.auth_router import router as auth_router
from routers.chat_router import router as chat_router
from routers.knowledge_router import router as knowledge_router
from routers.mcp_router import router as mcp_router
from routers.system_router import router as system_router
from routers.user_router import router as user_router
from service.cache_service import cache_service
from service.chat_service import chat_service
from service.file_upload_service import file_upload_service
from service.login_service import login_service
from service.user_profile_service import user_profile_service
from utils.tools_util import safety_filter, tools_embedding

# 注意：.env 加载由 config.py 统一处理，无需重复 load_dotenv()


def _make_pending_tools(metas: list[dict]) -> list:
    """把懒加载第三方工具 schema 转成「不可执行占位工具」，仅用于工具语义索引。

    真实工具未连接时无 BaseTool 对象；用 schema 构造占位工具喂给 tools_embedding，
    使 ToolFilter 语义层能召回第三方工具名（命中后由 llm_node 触发真实连接）。
    """
    from langchain_core.tools import StructuredTool

    def _noop(**kwargs):
        return "该工具当前未加载，请提示用户稍后重试或换用其他方式。"

    pending = []
    for m in metas:
        try:
            pending.append(StructuredTool.from_function(
                func=_noop,
                name=m["name"],
                description=m["description"],
                tags=m.get("tags") or None,
            ))
        except Exception as e:
            logger.warning(f"懒加载工具占位构造失败 {m.get('name')}: {e}")
    return pending


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化资源，关闭时释放资源。"""

    # ===== 启动阶段：yield 之前 =====
    # 第一步：校验必填环境变量，缺失时直接报错（快速失败，不拖到首个请求才 500）
    validate_config()
    # 工具常驻事件循环：MCP session 创建与工具调用必须同一循环——同步图在
    # StreamingResponse 线程池执行工具时会临时新建 loop，跨循环调用 session 会
    # 失败/挂起（Windows 下 mcp 库 cancel scope 泄漏还会注入 CancelledError 中断整图），
    # 故 MCP 连接与工具调用全部提交到本循环（见 mcp_client.make_sync_tool）
    tool_loop = asyncio.new_event_loop()
    threading.Thread(target=tool_loop.run_forever, daemon=True, name="mcp-tool-loop").start()
    # 方案B：分组 + 分级启动——第一方核心（filesystem/mitta-tools/sqlite/memory/...）常驻，
    # 第三方扩展（context7/dbhub）懒加载：进程不常驻，命中工具时按需连接
    servers = load_mcp_server_configs()
    mcp_holders = asyncio.run_coroutine_threadsafe(
        init_mcp_holders(servers, groups=["first_party"]), tool_loop
    ).result(timeout=130)
    mcp_tools = [t for h in mcp_holders for t in h.tools]
    filtered_tools = []
    lazy_loader = None
    if mcp_tools:
        filtered_tools = safety_filter(mcp_tools)
        # 第三方懒加载器：闪连预热工具 schema（供语义索引），不保持进程
        lazy_loader = McpLazyLoader(servers, tool_loop, safety_fn=safety_filter).warmup()
        # 工具向量索引 = 第一方真实工具 + 第三方 schema 占位（语义可召回，执行时才连接）
        index_tools = list(filtered_tools) + _make_pending_tools(lazy_loader.tool_metas())
        tools_embedding(index_tools)
        logger.success(
            f"已加载{len(filtered_tools)}个MCP 工具（第一方{len(mcp_holders)}类；"
            f"懒加载第三方{len(lazy_loader.tool_metas())}个工具已入语义索引）"
        )
    logger.info("正在初始化 LangGraph 资源...")
    # 创建依赖容器：所有外部依赖（LLM/Embedding/重排/System Prompt）统一在这里创建，
    # 通过参数注入到各服务和图中，替代原 init.py 全局初始化
    deps = AppDependencies()
    chat_service.open(filtered_tools, tool_loop=tool_loop, deps=deps, lazy_loader=lazy_loader)
    login_service.open()
    cache_service.open(embed_model=deps.embed_model, online_rerank=deps.online_rerank)
    user_profile_service.open()
    file_upload_service.open()
    logger.success("资源初始化完成")
    yield                                # ===== 应用运行期间（yield 挂起）=====
    # ===== 关闭阶段：yield 之后 =====
    logger.info("正在释放资源...")
    # MCP 子进程连接需在服务关闭前释放（工具闭包依赖 session）；连接创建在工具
    # 常驻循环上，关闭必须提交到该循环，否则跨循环 await 报错
    if mcp_holders:
        asyncio.run_coroutine_threadsafe(_close_mcp_holders(mcp_holders), tool_loop).result(timeout=10)
    if lazy_loader is not None:
        # 释放已连接的懒加载第三方进程（首方连接已随 mcp_holders 关闭）
        asyncio.run_coroutine_threadsafe(lazy_loader.aclose(), tool_loop).result(timeout=10)
    tool_loop.call_soon_threadsafe(tool_loop.stop)
    chat_service.close(timeout=10)
    login_service.close(timeout=10)
    cache_service.close()
    user_profile_service.close()
    file_upload_service.close()
    logger.info("资源已释放")


async def _close_mcp_holders(holders: list) -> None:
    """在工具常驻循环内按序关闭全部 MCP 连接（连接/工具调用同循环，关闭也必须同循环）。"""
    for holder in holders:
        await holder.close()


app = FastAPI(title="Mitta AI", lifespan=lifespan)

# CORS：放行 Tauri 桌面端与本地开发 origin。
# Tauri 2.x 在 Windows/Linux 上 origin 是 http://tauri.localhost，macOS 是 tauri://localhost。
# 线上 Nginx 同源（前端与 API 同域）不需要 CORS，本地直连后端调试需要。
# JWT 走 Authorization header，不用 cookie，故 allow_credentials=False，origin 可精确列出。
from fastapi.middleware.cors import CORSMiddleware
# Tauri dev 模式下 origin 是 http://localhost:<随机端口>（每次启动端口不同），
# 打包后是 http://tauri.localhost / tauri://localhost。用正则统一覆盖，不用每次加新端口。
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(http://(localhost|127\.0\.0\.1)(:\d+)?|tauri://localhost|https?://tauri\.localhost)$",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载 MCP 服务器端点（fastmcp 3.x：http_app 返回 Starlette app，2.x 的 streamable_http_app 已改名）
# 外部 MCP 客户端（Claude Desktop 等）通过 http://localhost:8000/mcp 调用 agent 能力
app.mount("/mcp", mcp.http_app())

# 注册请求限流中间件（对 /api/chat/ 等消耗 LLM 配额的接口限流）
from middleware.rate_limit_middleware import RateLimitMiddleware
app.add_middleware(RateLimitMiddleware)


# ============================================================
# 全局异常处理：统一返回结构化错误，避免暴露堆栈信息
# ============================================================

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """全局异常捕获：所有未处理的异常统一返回 500 + 结构化错误。

    - 记录完整异常信息到日志（含堆栈），便于排查
    - 返回给客户端的信息不包含堆栈，只返回通用错误提示
    - HTTPException 由 FastAPI 默认处理，不会进入此处理器
    """
    logger.exception(f"未处理的异常 | path={request.url.path} | method={request.method}")
    return FastAPIJSONResponse(
        status_code=500,
        content={
            "ok": False,
            "detail": "服务器内部错误，请稍后重试或联系管理员",
            "error_type": type(exc).__name__,
        },
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """HTTPException 统一包装为 {ok, detail} 格式，与业务接口响应风格一致。"""
    return FastAPIJSONResponse(
        status_code=exc.status_code,
        content={"ok": False, "detail": exc.detail},
        headers=exc.headers,
    )


# ============================================================
# 路由注册：按模块拆分到 routers/ 目录
# 注意：system_router 必须最后注册（SPA 兜底路由 /{full_path:path} 会匹配所有未捕获路径）
# ============================================================
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(user_router)
app.include_router(mcp_router)
app.include_router(knowledge_router)
app.include_router(system_router)  # 必须最后注册


if __name__ == "__main__":
    import os
    import uvicorn
    # 端口默认 8000，可通过环境变量 MITTA_API_PORT 覆盖。
    # Windows 上 WSL2/Hyper-V 的 winnat 会动态保留一段端口（如 7449-8248），落在
    # 保留段内的端口绑定会报 WinError 10013；本地开发时在 .env 设 MITTA_API_PORT=18000
    # 即可避开（服务器容器内无此限制，保持默认 8000）。
    port = int(os.getenv("MITTA_API_PORT", "8000"))
    uvicorn.run("main:app", host="localhost", port=port, reload=True)
