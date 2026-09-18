"""打印运行时第一方 MCP 工具的真实 name（H-20260919-01 任务③校准用）。

复现 main.py lifespan 的工具装配链（init_mcp_holders first_party → safety_filter），
输出运行时真实 tool.name，用于逐字校准 persona_constant.py 的 kind/manager 白名单。

用法: D:\\Develop\\conda_envs\\langchain1.2\\python.exe scripts\\list_runtime_tools.py
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv

load_dotenv(override=True)


async def main() -> None:
    from config import load_mcp_server_configs
    from mcp_client.client import init_mcp_holders
    from utils.tools_util import safety_filter

    servers = load_mcp_server_configs()
    holders = await init_mcp_holders(servers, timeout=120, groups=["first_party"])
    try:
        all_tools = [t for h in holders for t in h.tools]
        filtered = safety_filter(all_tools)
        print(f"\n=== first_party 工具真实 name（safety_filter 后 {len(filtered)} 个）===")
        for t in filtered:
            print(f"  {t.name}")
    finally:
        for h in holders:
            try:
                await h.close()
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())
