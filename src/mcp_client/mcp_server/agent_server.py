import sys
from pathlib import Path

# stdio 子进程以 cwd=本目录启动，需手动把项目 src/ 加入 sys.path 才能 import service.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastmcp import FastMCP

from mcp_client.mcp_server.mcp_auth_middleware import mcp_user_id_var
from service.login_service import login_service
from service.chat_service import chat_service     # 子进程内新建单例，需调用 open() 后方可用

mcp = FastMCP("agent_server")

@mcp.tool()
async def chat(query: str, thread_id: str = "mcp-default") -> str:
    """助理对话：含知识库检索、短期记忆（thread_id 隔离）、长期记忆（user_id 隔离）"""
    # P0-5: user_id 不从入参取，强制用 JWT 鉴权中间件注入的身份；stdio 本地模式 fallback
    uid = mcp_user_id_var.get() or "mcp-user"
    return await chat_service.a_invoke(uid, thread_id, query)   # 已有异步壳，流式降级为整段返回

@mcp.tool()
async def get_current_user() -> dict:
    """查询用户账户信息（复用 login_service，补全现有空壳实现）"""
    # P0-5: 身份来自 JWT 中间件，调用方无法指定他人 user_id
    uid = mcp_user_id_var.get() or "mcp-user"
    row = login_service.get_user_by_id(uid)
    return {"user_id": row["user_id"], "username": row["username"], "create_time": row["create_time"]} | {"error": "用户不存在"}

# @mcp.tool()
# async def summarize(thread_id: str, user_id: str) -> str:
#     """按会话压缩上下文：取 checkpointer 历史（chat_service.get_history_session）交给 model 压缩"""
#     ...


if __name__ == "__main__":
    # stdio 子进程入口：缺了它脚本执行完就退出，客户端握手直接失败
    mcp.run(transport="stdio")
