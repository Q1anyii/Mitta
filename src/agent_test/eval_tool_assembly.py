"""
Mitta 工具装配评测（E3）
========================
评测工具装配：规则层 + 语义层并集召回，语义层异常自动降级规则层并熔断。

指标：
  - 规则层命中率（tags 强相关是否被正确筛出）
  - 语义层命中率（语义相关工具是否被正确补充）
  - 并集召回率（期望工具在最终选中集合中的比例）
  - 语义层异常降级：mock 向量库抛异常后 select_tools 仍返回规则层结果、不阻塞
  - 熔断生效：语义层失败一次后 _semantic_available=False，后续查询跳过语义检索

用法：
    conda activate langchain1.2
    cd src
    python -m agent_test.eval_tool_assembly

说明：
  - 白盒测试：使用 stub 工具 + stub 向量库，不依赖真实 MCP 工具/外部服务。
  - 聚焦装配逻辑本身（并集、去重、降级、熔断），与 E2（真实工具召回）互补。
"""
import json
import os
import sys
from pathlib import Path
from typing import Dict, List
from unittest.mock import MagicMock, patch

from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graphs.tool_filter import ToolFilter
from constant.tool_constant import TOP_FILTER_TOOLS


def make_stub_tool(name: str, description: str, tags: List[str]):
    """构造一个最小 BaseTool stub（含 name/description/tags/metadata）。"""
    from langchain_core.tools import BaseTool
    from pydantic import BaseModel, Field

    class _Args(BaseModel):
        query: str = Field(description="查询内容")

    # 动态构造类：Pydantic v2 覆盖基类字段必须带类型注解，用 type() 传入
    _Stub = type(
        "StubTool",
        (BaseTool,),
        {
            "__module__": __name__,
            "__annotations__": {"name": str, "description": str, "tags": List[str], "args_schema": type},
            "name": name,
            "description": description,
            "args_schema": _Args,
            "tags": tags,
            "_run": lambda self, *a, **kw: "stub",
        },
    )
    return _Stub()


def make_stub_tools() -> List:
    """模拟一组工具：部分有 tags（规则层可命中），部分仅描述（语义层可命中）。"""
    return [
        make_stub_tool("search_files", "在文件系统中搜索文件，按文件名/内容匹配", ["search", "文件"]),
        make_stub_tool("read_file", "读取指定文件的完整内容", ["read", "文件"]),
        make_stub_tool("write_file", "写入或覆盖文件内容", ["write", "文件"]),
        make_stub_tool("get_weather", "查询指定城市的实时天气", ["weather", "天气"]),
        make_stub_tool("query_database", "对数据库执行 SQL 查询", ["sql", "数据库"]),
        make_stub_tool("send_email", "发送电子邮件给指定收件人", ["email", "邮件"]),
    ]


def make_semantic_vector_store(tool_map: dict):
    """构造语义层正常工作的向量库 stub：query 命中与关键词相关的工具。"""
    store = MagicMock()
    store.query.return_value = [[
        MagicMock(metadata={"tool_name": name}) for name in ["search_files", "query_database", "get_weather"]
    ]]
    return store


class TestToolAssembly:
    """工具装配测试套件。"""

    def setup_method(self):
        self.tools = make_stub_tools()
        self.tool_map = {t.name: t for t in self.tools}
        # patch 向量库构造：评测只关心装配逻辑，不真实初始化 chroma/外部向量库（避免 60s+ 依赖）
        self._vs_patch = patch("graphs.tool_filter.create_vector_store", return_value=MagicMock())
        self._vs_patch.start()

    def teardown_method(self):
        self._vs_patch.stop()

    def test_rule_based_filter(self):
        """规则层：tags 命中应筛出对应工具。"""
        hit = ToolFilter.rule_based_filter("帮我搜索文件", self.tools)
        names = {t.name for t in hit}
        assert "search_files" in names, f"tags 搜索未命中 search_files，命中={names}"

    def test_rule_empty_when_no_tag(self):
        """规则层：无 tags 命中时返回空列表（不兜底全量）。"""
        hit = ToolFilter.rule_based_filter("随便聊聊", self.tools)
        assert hit == []

    def test_semantic_merge_union(self):
        """并集：规则 + 语义结果合并且按 name 去重。"""
        tf = ToolFilter(selector_llm=MagicMock())
        tf.vector_store = make_semantic_vector_store(self.tool_map)
        selected = tf.select_tools("帮我搜索文件", self.tools)
        names = [t.name for t in selected]
        assert "search_files" in names, f"并集丢失规则层命中，selected={names}"
        assert "query_database" in names, f"并集丢失语义层补充，selected={names}"
        assert len(names) == len(set(names)), f"存在重复工具：{names}"

    def test_semantic_failure_degrades_to_rule(self):
        """语义层异常：应降级为规则层结果，不抛异常不阻塞。"""
        tf = ToolFilter(selector_llm=MagicMock())
        # 语义层 mock 抛异常
        tf.vector_store = MagicMock()
        tf.vector_store.query.side_effect = RuntimeError("vector store down")
        selected = tf.select_tools("帮我搜索文件", self.tools)
        names = {t.name for t in selected}
        assert "search_files" in names, f"语义异常后未降级规则层，selected={names}"
        assert tf._semantic_available is False, "语义层失败后应熔断"

    def test_circuit_breaker_skips_semantic(self):
        """熔断生效：_semantic_available=False 时 query_available_tools 直接返回空。"""
        tf = ToolFilter(selector_llm=MagicMock())
        tf._semantic_available = False
        tf.vector_store = MagicMock()
        result = tf.query_available_tools("任意查询", self.tools)
        assert result == []
        tf.vector_store.query.assert_not_called()  # 熔断后不应再调向量库

    def test_select_returns_empty_on_no_hit(self):
        """两路均未命中：返回空列表（不兜底全量），由 llm_node 注入提示。"""
        tf = ToolFilter(selector_llm=MagicMock())
        tf.vector_store = MagicMock()
        tf.vector_store.query.return_value = [[]]  # 语义无命中
        selected = tf.select_tools("完全无关的内容", self.tools)
        assert selected == []


def main():
    logger.info("=" * 60)
    logger.info("Mitta 工具装配评测（规则层+语义层并集/降级/熔断）")
    logger.info("=" * 60)

    suite = TestToolAssembly()
    suite.setup_method()

    results = []
    tests = [
        ("rule_based_filter", suite.test_rule_based_filter, "规则层 tags 命中"),
        ("rule_empty_when_no_tag", suite.test_rule_empty_when_no_tag, "规则层无命中返回空"),
        ("semantic_merge_union", suite.test_semantic_merge_union, "并集合并去重"),
        ("semantic_failure_degrades_to_rule", suite.test_semantic_failure_degrades_to_rule, "语义异常降级规则层"),
        ("circuit_breaker_skips_semantic", suite.test_circuit_breaker_skips_semantic, "熔断跳过语义检索"),
        ("select_empty_on_no_hit", suite.test_select_returns_empty_on_no_hit, "两路未命中返回空"),
    ]

    passed = 0
    for key, fn, desc in tests:
        try:
            fn()
            results.append({"test": key, "desc": desc, "passed": True})
            passed += 1
            logger.info(f"  ✓ {desc}")
        except AssertionError as e:
            results.append({"test": key, "desc": desc, "passed": False, "error": str(e)})
            logger.error(f"  ✗ {desc}: {e}")
        except Exception as e:
            results.append({"test": key, "desc": desc, "passed": False, "error": repr(e)})
            logger.error(f"  ✗ {desc}: {repr(e)}")

    summary = {
        "agent_test": "tool_assembly",
        "total": len(tests),
        "passed": passed,
        "pass_rate": round(passed / len(tests), 4),
        "top_filter_tools": TOP_FILTER_TOOLS,
        "results": results,
    }
    logger.info(f"【汇总】通过 {passed}/{len(tests)}（{summary['pass_rate']*100:.0f}%）")

    output_path = Path(__file__).parent / "tool_assembly_eval_report.json"
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"评测报告已保存: {output_path}")


if __name__ == "__main__":
    main()
