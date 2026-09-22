# ============================================================
# Agent 系统回归测试（评测矩阵 E14）
# 覆盖：动态路由 / MCP 安全校验 / 工具异常兜底 / 记忆缓存 key / 工具名解析
# 运行：pytest tests/test_agent_regression.py -v
# 说明：全部为纯函数/无外部依赖用例，可进 CI（RAGAS 评测不进 CI）。
# ============================================================

import os
import sys

import pytest

# 添加 src 到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Send

from graphs.routes import route, route_after_llm
from graphs.nodes.memory_node import _memory_cache_key
from service.mcp_config_service import validate_mcp_server_config
from graphs.main_graph import _tool_error_message
from utils.tools_util import parse_tool_names, safety_filter


# ============================================================
# E1 动态路由
# ============================================================

class TestDynamicRouting:
    def test_route_retrieval_when_needed(self):
        """needs_retrieval=True 应路由到 retrieve_node。"""
        result = route({"input_str": "x", "needs_retrieval": True})
        assert result == "retrieve_node"

    def test_route_llm_when_not_needed(self):
        """needs_retrieval=False 应路由到 llm_node。"""
        result = route({"input_str": "x", "needs_retrieval": False})
        assert result == "llm_node"

    def test_route_after_llm_tool(self):
        """LLM 有 tool_calls 应路由到 tool_node。"""
        state = {"messages": [AIMessage(content="", tool_calls=[{"name": "fetch", "args": {}, "id": "1"}])]}
        assert route_after_llm(state) == "tool_node"

    def test_route_after_llm_memory(self):
        """LLM 无 tool_calls 应路由到 memory_node。"""
        state = {"messages": [AIMessage(content="你好")]}
        assert route_after_llm(state) == "memory_node"


# ============================================================
# E4 MCP 安全校验
# ============================================================

class TestMcpSafety:
    def test_invalid_command_rejected(self):
        """非法命令应被白名单拦截。"""
        with pytest.raises(ValueError):
            validate_mcp_server_config(
                {"name": "x", "type": "stdio", "command": "rm", "args": ["-rf", "/"]},
                user_id="u1",
            )

    def test_unknown_package_rejected(self):
        """非白名单包名应被拒绝。"""
        with pytest.raises(ValueError):
            validate_mcp_server_config(
                {"name": "x", "type": "stdio", "command": "npx", "args": ["-y", "malicious-pkg"]},
                user_id="u1",
            )

    def test_sensitive_env_filtered(self):
        """敏感环境变量应被过滤，非敏感保留。"""
        cleaned = validate_mcp_server_config(
            {"name": "x", "type": "stdio", "command": "uvx", "args": ["mcp-server-fetch"],
             "env": {"PATH": "/usr/bin", "HOME": "/root", "API_KEY": "ok"}},
            user_id="u1",
        )
        assert "PATH" not in cleaned["env"]
        assert "HOME" not in cleaned["env"]
        assert cleaned["env"]["API_KEY"] == "ok"

    def test_internal_sse_url_rejected(self):
        """sse 内网地址应被拒绝。"""
        with pytest.raises(ValueError):
            validate_mcp_server_config(
                {"name": "x", "type": "sse", "url": "http://localhost:3000/mcp"},
                user_id="u1",
            )

    def test_non_http_sse_rejected(self):
        """sse 非 http(s) 协议应被拒绝。"""
        with pytest.raises(ValueError):
            validate_mcp_server_config(
                {"name": "x", "type": "sse", "url": "ftp://example.com/mcp"},
                user_id="u1",
            )

    def test_legit_config_passes(self):
        """合法配置应放行且命令小写归一。"""
        cleaned = validate_mcp_server_config(
            {"name": "x", "type": "stdio", "command": "UVX", "args": ["mcp-server-fetch"]},
            user_id="u1",
        )
        assert cleaned["command"] == "uvx"


# ============================================================
# E5 工具异常兜底
# ============================================================

class TestToolErrorFallback:
    def test_ordinary_exception_message(self):
        """普通异常应转为可操作提示。"""
        msg = _tool_error_message(RuntimeError("disk full"))
        assert "工具执行失败" in msg
        assert "disk full" in msg

    def test_entdir_guidance(self):
        """ENOTDIR 应给出纠正方向（改目录/用 read_file）。"""
        from langchain_core.tools import ToolException
        msg = _tool_error_message(ToolException("path is a file, ENOTDIR"))
        assert "必须是目录" in msg
        assert "read_file" in msg


# ============================================================
# E6 记忆缓存 key
# ============================================================

class TestMemoryCacheKey:
    def test_idle_never_cached(self):
        """idle 轮返回随机 key，永不命中。"""
        k1 = _memory_cache_key({"tool_status": "idle", "messages": [], "input_str": "hi"})
        k2 = _memory_cache_key({"tool_status": "idle", "messages": [], "input_str": "hi"})
        assert k1.startswith("nocache-")
        assert k1 != k2

    def test_executed_deterministic(self):
        """executed 轮返回确定性 key（同轮可命中）。"""
        state = {"tool_status": "executed", "messages": [AIMessage(content="ok")], "input_str": "q"}
        k1 = _memory_cache_key(state)
        k2 = _memory_cache_key(state)
        assert k1 == k2
        assert not k1.startswith("nocache-")

    def test_key_includes_round(self):
        """key 含消息轮次，跨轮不误命中。"""
        state1 = {"tool_status": "executed", "messages": [AIMessage(content="a")], "input_str": "q"}
        state2 = {"tool_status": "executed", "messages": [AIMessage(content="a"), ToolMessage(content="r", tool_call_id="1")], "input_str": "q"}
        assert _memory_cache_key(state1) != _memory_cache_key(state2)


# ============================================================
# 工具名解析 / 安全过滤
# ============================================================

class TestToolNameParsing:
    def test_fenced_json_array(self):
        """应剥离 ```json 围栏并解析数组。"""
        assert parse_tool_names('```json\n["fetch", "query"]\n```') == ["fetch", "query"]

    def test_wrapped_object(self):
        """应兼容 {"tools": [...]} 包装。"""
        assert parse_tool_names('{"tools": ["a", "b"]}') == ["a", "b"]

    def test_invalid_returns_empty(self):
        """解析失败应返回空列表。"""
        assert parse_tool_names("not json at all") == []


class TestSafetyFilter:
    def test_destructive_tool_requires_admin(self):
        """破坏性工具（destructiveHint）非管理员应被过滤。"""
        from langchain_core.tools import BaseTool
        from pydantic import BaseModel as PydBase

        class _Args(PydBase):
            pass

        class _Tool(BaseTool):
            name: str = "git_reset"
            description: str = "reset repo"
            args_schema: type = _Args
            metadata: dict = {"destructiveHint": True}

            def _run(self, *args, **kwargs):
                return "x"

        tools = [_Tool()]
        assert safety_filter(tools, user_role="user") == []
        assert len(safety_filter(tools, user_role="admin")) == 1
