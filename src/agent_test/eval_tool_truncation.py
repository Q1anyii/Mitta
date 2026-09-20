"""
Mitta 工具结果兜底评测（E5）
============================
评测工具执行结果的长度截断与异常兜底能力。

指标（对应项目描述「对工具返回结果做长度截断与异常兜底」）：
  - 异常→ToolMessage 转换率：_tool_error_message 能把各类异常转为可操作提示
  - ENOTDIR 专项：路径参数错误的错误提示是否给出纠正方向
  - 描述截断生效：format_tools_for_prompt 对超长 description 截断到 200 字符
  - 文档截断生效：llm_node 检索资料单篇截断到 MAX_DOC_CHARS、最多 MAX_RETRIEVAL_DOCS 篇
  - 工具轮次上限防护：MAX_TOOL_ROUNDS=8 的硬性上限存在且生效逻辑可测，
    且计数为**按轮**（新 HumanMessage 归零），非会话累计

用法：
    conda activate langchain1.2
    cd src
    python -m agent_test.eval_tool_truncation

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

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import ToolException

from graphs.main_graph import _tool_error_message
from graphs.nodes.llm_node import (
    MAX_RETRIEVAL_DOCS, MAX_DOC_CHARS, MAX_TOOL_ROUNDS, MAX_TOOL_FAILURES,
    _turn_anchor, _failed_tool_names,
)
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
        # 2026-09-19 随 MAX_RETRIEVAL_DOCS 5→8 同步（H-20260919-07 P1 放宽，与 rerank/filter 对齐）
        "assert": lambda v: v[0] == 2000 and v[1] == 8,
        "dimension": "doc_truncation",
    })
    # 6. 工具轮次上限常量
    cases.append({
        "name": "工具轮次上限常量",
        "fn": lambda: MAX_TOOL_ROUNDS,
        "assert": lambda v: v == 8,
        "dimension": "loop_protection",
    })

    # 7-10. 轮次上限的「按轮」语义：计数必须从最后一条 HumanMessage 之后开始，
    # 不能统计整个会话记录。回归 bug：旧实现 sum(全会话 ToolMessage)，
    # 会话聊到第 5-6 轮时历史已积满 8 条 ToolMessage，本轮一次工具都没调就被拦。
    def _turn_count(history):
        """复刻 llm_node 的 per-turn 计数：max(本轮 ToolMessage, 本轮 tool_calls)。"""
        start = _turn_anchor(history)
        executed = sum(1 for m in history[start:] if isinstance(m, ToolMessage))
        pending = sum(
            len(m.tool_calls) for m in history[start:]
            if isinstance(m, AIMessage) and m.tool_calls
        )
        return max(executed, pending)

    def _multi_turn_history(turns: int, calls_per_turn: int):
        h = []
        for t in range(turns):
            h.append(HumanMessage(content=f"q{t}"))
            for k in range(calls_per_turn):
                i = t * calls_per_turn + k
                h.append(AIMessage(content="", tool_calls=[
                    {"name": "fetch_url", "args": {"url": f"u{i}"}, "id": f"c{i}"},
                ]))
                h.append(ToolMessage(content="ok", tool_call_id=f"c{i}"))
        return h

    # 7. 跨多轮、每轮 2 次工具调用 → 新开一轮时本轮计数归零
    _hist = _multi_turn_history(6, 2) + [HumanMessage(content="q6")]
    cases.append({
        "name": "新轮起点计数归零（6 轮各调 2 次后，新轮仍可调工具）",
        "fn": lambda: _turn_count(_hist),
        "assert": lambda v: v == 0,
        "dimension": "loop_protection",
    })
    # 8. 同一轮内（工具循环中间态，无新 HumanMessage）累计到 8 次才封顶
    _hist2 = _multi_turn_history(1, MAX_TOOL_ROUNDS)
    cases.append({
        "name": f"轮内累计达上限（同轮 {MAX_TOOL_ROUNDS} 次调用 -> {MAX_TOOL_ROUNDS}）",
        "fn": lambda: _turn_count(_hist2),
        "assert": lambda v: v == MAX_TOOL_ROUNDS,
        "dimension": "loop_protection",
    })
    # 9. 两代口径的差异锁死：**会话累计**（旧口径）与**本轮**（新口径）必须分道扬镳。
    #     同一份「6 轮×2 调用 + 收尾新轮」历史，旧口径已积 12 次、早该被拦；
    #     新口径必须读到 0（本轮还没动手）。将来谁把计数改回全会话累计，本用例立刻变红。
    #     注：末条 HumanMessage 是列表最后一个元素，故 anchor == len（切片为空）。
    _hist3 = _multi_turn_history(6, 2) + [HumanMessage(content="q6")]
    cases.append({
        "name": "按轮计数 vs 会话累计（防回退）",
        "fn": lambda: (
            _turn_count(_hist3),                                   # 新：本轮
            sum(1 for m in _hist3 if isinstance(m, ToolMessage)),  # 旧：全会话
            _turn_anchor(_hist3) == len(_hist3),                   # 本轮起点 = 末尾（切片为空）
        ),
        "assert": lambda v: v[0] == 0 and v[1] >= MAX_TOOL_ROUNDS and v[2] is True,
        "dimension": "loop_protection",
    })

    # 10-13. 失败熔断（2026-09-19 新增）：同一工具连续失败达 MAX_TOOL_FAILURES 后本轮禁用。
    # 回归场景：模型反复调一个超时的工具（sequentialthinking），把 8 次额度全吃光，
    # 用户拿到的是"次数到上限"而非答案。熔断要能在额度耗尽前把坏工具摘掉。
    def _fail_turn(fail_count: int, name: str = "sequentialthinking"):
        """构造一轮内某工具连续失败 fail_count 次的历史。"""
        h = [HumanMessage(content="帮我想想怎么构建项目")]
        for i in range(fail_count):
            h.append(AIMessage(content="", tool_calls=[
                {"name": name, "args": {"thought": f"s{i}"}, "id": f"f{i}"}]))
            h.append(ToolMessage(
                content="工具执行失败：TimeoutError: tool call timed out after 30s",
                name=name, tool_call_id=f"f{i}", status="error"))
        return h

    # 10. 连续失败 2 次 → 该工具进入禁用集合
    cases.append({
        "name": f"连续失败 {MAX_TOOL_FAILURES} 次触发熔断",
        "fn": lambda: _failed_tool_names(_fail_turn(MAX_TOOL_FAILURES), 0),
        "assert": lambda d: "sequentialthinking" in d
                            and d["sequentialthinking"] >= MAX_TOOL_FAILURES,
        "dimension": "failure_circuit_breaker",
    })
    # 11. 只失败 1 次（未达阈值）→ 不熔断，给工具留重试机会
    cases.append({
        "name": "仅失败 1 次不熔断（留重试机会）",
        "fn": lambda: _failed_tool_names(_fail_turn(1), 0),
        "assert": lambda d: d == {},
        "dimension": "failure_circuit_breaker",
    })
    # 12. 「连续」语义：失败→成功→失败 不算连续，不该熔断。
    #     若这里变红，说明实现把「累计失败」当成了「连续失败」，
    #     会让一个只是偶尔抖动的工具被误杀。
    def _interleaved_failures():
        h = [HumanMessage(content="q")]
        for i, ok in enumerate([False, True, False]):
            h.append(AIMessage(content="", tool_calls=[
                {"name": "fetch_url", "args": {"url": f"u{i}"}, "id": f"i{i}"}]))
            h.append(ToolMessage(
                content="抓取失败: ConnectError" if not ok else "正文内容……",
                name="fetch_url", tool_call_id=f"i{i}",
                status="error" if not ok else "success"))
        return h

    cases.append({
        "name": "失败→成功→失败 不算连续（不熔断）",
        "fn": lambda: _failed_tool_names(_interleaved_failures(), 0),
        "assert": lambda d: d == {},
        "dimension": "failure_circuit_breaker",
    })
    # 13. 本节点自己注入的「次数已达上限」提示不能被误判为工具失败。
    #     否则熔断会自我强化：注入的提示被当成失败 → 下一轮又多禁一个工具。
    def _notice_history():
        h = [HumanMessage(content="q")]
        h.append(AIMessage(content="", tool_calls=[
            {"name": "memory", "args": {}, "id": "n0"}]))
        h.append(ToolMessage(
            content="注意：本轮请求中工具调用次数已达上限，请不要再调用任何工具。",
            name="memory", tool_call_id="n0"))
        return h

    cases.append({
        "name": "节点注入的提示不误判为工具失败",
        "fn": lambda: _failed_tool_names(_notice_history(), 0),
        "assert": lambda d: d == {},
        "dimension": "failure_circuit_breaker",
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
        "agent_test": "tool_truncation",
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
