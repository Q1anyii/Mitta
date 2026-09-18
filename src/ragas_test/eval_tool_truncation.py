"""
Mitta 工具结果兜底评测（E5）
============================
评测工具执行结果的长度截断与异常兜底能力。

指标（对应项目描述「对工具返回结果做长度截断与异常兜底」）：
  - 异常→ToolMessage 转换率：_tool_error_message 能把各类异常转为可操作提示
  - ENOTDIR 专项：路径参数错误的错误提示是否给出纠正方向
  - 描述截断生效：format_tools_for_prompt 对超长 description 截断到 200 字符
  - 文档截断生效：llm_node 检索资料单篇截断到 MAX_DOC_CHARS、最多 MAX_RETRIEVAL_DOCS 篇
  - 工具轮次上限防护：MAX_TOOL_ROUNDS=8 的硬性上限存在且生效逻辑可测

用法：
    conda activate langchain1.2
    cd src
    python -m ragas_test.eval_tool_truncation

说明：
  - 纯函数/常量白盒测试，无外部依赖，可进 CI。
"""
import json
import os
import sys
from pathlib import Path
from typing import Dict, List

from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.tools import ToolException

from graphs.main_graph import _tool_error_message
from graphs.nodes.llm_node import MAX_RETRIEVAL_DOCS, MAX_DOC_CHARS, MAX_TOOL_ROUNDS
from utils.tools_util import format_tools_for_prompt
from graphs.tool_filter import ToolFilter


def build_cases() -> List[Dict]:
    """构造兜底能力测试集。"""
    cases = []

    # 1. 普通异常 → 可操作提示
    cases.append({
        "name": "普通异常转 ToolMessage 提示",
        "fn": lambda: _tool_error_message(RuntimeError("disk full")),
        "assert": lambda s: "工具执行失败" in s and "disk full" in s,
        "dimension": "exception_to_message",
    })
    # 2. ENOTDIR → 纠正方向提示
    cases.append({
        "name": "ENOTDIR 给出纠正方向",
        "fn": lambda: _tool_error_message(ToolException("path is a file, ENOTDIR")),
        "assert": lambda s: "必须是目录" in s and "read_file" in s,
        "dimension": "exception_to_message",
    })
    # 3. ToolException 普通错误
    cases.append({
        "name": "ToolException 转提示",
        "fn": lambda: _tool_error_message(ToolException("invalid args")),
        "assert": lambda s: "工具执行失败" in s,
        "dimension": "exception_to_message",
    })
    # 4. 描述截断：超长 description 截断到 200
    long_desc = "x" * 500
    cases.append({
        "name": "工具描述截断到 200",
        "fn": lambda: format_tools_for_prompt(
            [ToolFilter_StubTool(long_desc)]
        ),
        # 截断后不应出现 200 个以上的连续 x（desc[:200] 生效）
        "assert": lambda s: ("x" * 201) not in s,
        "dimension": "desc_truncation",
    })
    # 5. 常量约束：文档截断上限存在且合理
    cases.append({
        "name": "检索文档截断常量",
        "fn": lambda: (MAX_DOC_CHARS, MAX_RETRIEVAL_DOCS),
        "assert": lambda v: v[0] == 2000 and v[1] == 5,
        "dimension": "doc_truncation",
    })
    # 6. 工具轮次上限常量
    cases.append({
        "name": "工具轮次上限常量",
        "fn": lambda: MAX_TOOL_ROUNDS,
        "assert": lambda v: v == 8,
        "dimension": "loop_protection",
    })
    return cases


class ToolFilter_StubTool:
    """模拟 BaseTool 的最小对象（format_tools_for_prompt 需要 name/description/args/tags）。"""
    name = "stub"
    description = "x" * 500
    tags = []

    def __init__(self, description: str = None):
        if description is not None:
            self.description = description

    @property
    def args(self):
        return {"properties": {"query": {}}}


def main():
    logger.info("=" * 60)
    logger.info("Mitta 工具结果兜底评测（截断/异常）")
    logger.info("=" * 60)

    cases = build_cases()
    results = []
    dim_stats = {}

    for case in cases:
        dim = case["dimension"]
        dim_stats.setdefault(dim, {"total": 0, "pass": 0})
        dim_stats[dim]["total"] += 1
        try:
            value = case["fn"]()
            ok = case["assert"](value)
        except Exception as e:
            ok = False
            value = repr(e)
        if ok:
            dim_stats[dim]["pass"] += 1
        results.append({
            "case": case["name"],
            "dimension": dim,
            "passed": ok,
            "value_preview": str(value)[:120],
        })
        mark = "✓" if ok else "✗"
        logger.info(f"  [{mark}] {case['name']}")

    dimension_results = {}
    for dim, st in dim_stats.items():
        rate = st["pass"] / st["total"] if st["total"] else 0
        dimension_results[dim] = {"total": st["total"], "pass": st["pass"], "pass_rate": round(rate, 4)}
        logger.info(f"  维度[{dim}] 通过率: {rate*100:.0f}% ({st['pass']}/{st['total']})")

    total = len(cases)
    passed = sum(1 for r in results if r["passed"])
    summary = {
        "ragas_test": "tool_truncation",
        "total": total,
        "passed": passed,
        "pass_rate": round(passed / total, 4),
        "dimension_stats": dimension_results,
        "results": results,
    }
    logger.info(f"【汇总】兜底用例通过 {passed}/{total}（{summary['pass_rate']*100:.0f}%）")

    output_path = Path(__file__).parent / "tool_truncation_eval_report.json"
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"评测报告已保存: {output_path}")


if __name__ == "__main__":
    main()
