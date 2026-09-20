"""工具筛选离线评估（toolsTODO 7.2 / 评测矩阵 E2）：对测试集计算 recall@k / precision@k。

用法：
    conda activate langchain1.2
    cd src && python -m ragas_test.evaluate_tool_filter
    cd src && python -m ragas_test.evaluate_tool_filter --max-cases 22

按实际 MCP 工具名调整 TEST_CASES 的期望命中工具，运行后输出各 query 的
命中情况与汇总指标，据此调整 TOP_FILTER_TOOLS / TOOL_DISTANCE_THRESHOLD。

评估指标（对应项目描述「工具装配抽象为规则层+语义层并集召回」）：
  - recall@k：期望工具被选中的比例（漏检敏感）
  - precision@k：选中的工具中期望工具占比（噪声敏感）
  - zero_hit_count：完全未命中的 query 数（装配失效告警）
"""
import argparse
import asyncio
import json
import os
from pathlib import Path

from loguru import logger
from rich.console import Console
from rich.table import Table

from config import load_mcp_server_configs
from constant.tool_constant import TOP_FILTER_TOOLS
from graphs.tool_filter import ToolFilter
from mcp_client.client import init_mcp_holders
from utils.tools_util import safety_filter

console = Console()


def _localize_mcp_configs(servers: list[dict]) -> list[dict]:
    """本地评测适配：把 mitta-tools 等本地 Python server 的 cwd 从容器路径
    （/app/...）覆盖为本地项目根，并前置 conda python 到 PATH，使裸 `python`
    命令解析到带 fastmcp 的解释器。线上运行时（容器内）不生效。
    """
    project_root = Path(__file__).resolve().parent.parent.parent  # ragas_test -> src -> 项目根
    conda_py = Path(r"D:\Develop\conda_envs\langchain1.2\python.exe")
    if conda_py.is_file():
        conda_dir = str(conda_py.parent)
        os.environ["PATH"] = conda_dir + os.pathsep + str(conda_py.parent / "Scripts") + os.pathsep + os.environ.get("PATH", "")
    for cfg in servers:
        # 容器路径在 Windows 上被 Path.is_absolute() 转为 E:\app\...，两种形态都覆盖
        cwd = cfg.get("cwd", "")
        if cfg.get("name") == "mitta-tools" and (cwd.startswith("/app") or cwd.startswith("E:\\app")):
            cfg["cwd"] = str(project_root)
    return servers

# 测试集：query -> 期望命中的工具名（按实际 MCP 工具名对齐；与当前 41 工具环境一致）
TEST_CASES = [
    {"query": "读取 E:\\工作文件\\AgentProject\\README.md 的内容", "expected": ["read_file", "read_text_file"]},
    {"query": "在 docs 目录下创建一个新文件 architecture.md", "expected": ["create_directory", "search_project_files"]},
    {"query": "列出当前目录下的所有文件", "expected": ["list_directory", "directory_tree"]},
    {"query": "搜索项目中包含 TODO 的文件", "expected": ["search_project_files"]},
    {"query": "把 temp.py 移动到 utils 目录", "expected": ["read_file", "create_directory"]},
    {"query": "查看当前 git 仓库的提交历史", "expected": ["git_log"]},
    {"query": "看看有哪些文件被修改了", "expected": ["git_status", "git_diff"]},
    {"query": "把 main.py 的改动提交到仓库", "expected": ["git_add", "git_commit"]},
    {"query": "创建一个新分支 feature/cache", "expected": ["git_branch", "git_checkout"]},
    {"query": "切换到 dev 分支", "expected": ["git_checkout"]},
    {"query": "查看当前分支列表", "expected": ["git_branch"]},
    {"query": "帮我抓取 https://example.com 的内容", "expected": ["fetch_url"]},
    {"query": "搜索一下 LangGraph 的最新文档", "expected": ["web_search", "query-docs"]},
    {"query": "解析这个 PDF 文件的内容", "expected": ["read_media_file", "read_file"]},
    {"query": "查询用户表中所有用户", "expected": ["read_query", "describe_table"]},
    {"query": "查看数据库中有哪些表", "expected": ["describe_table", "list_tables"]},
    {"query": "这个问题很复杂，帮我分步思考", "expected": ["sequentialthinking"]},
    {"query": "记住我喜欢用 Python 编程", "expected": ["add_observations", "create_relations"]},
    {"query": "更新我的个人信息", "expected": ["add_observations", "create_relations"]},
    {"query": "帮我分析这个项目的代码结构", "expected": ["get_project_info", "directory_tree"]},
    {"query": "读取配置文件并修改数据库连接", "expected": ["read_file", "write_file"]},
    {"query": "看看最近的代码改动并生成总结", "expected": ["git_log", "git_diff", "read_file"]},

]

def recall_at_k(selected: list[str], expected: list[str], k: int) -> float:
    """命中的期望工具数 / 期望工具总数（k 内截断）。"""
    if not expected:
        return 1.0
    return len(set(selected[:k]) & set(expected)) / len(expected)


def precision_at_k(selected: list[str], expected: list[str], k: int) -> float:
    """命中的期望工具数 / 实际返回工具数（k 内截断）。"""
    if not selected[:k]:
        return 0.0
    return len(set(selected[:k]) & set(expected)) / len(selected[:k])


async def main(max_cases: int = None):
    connections = await init_mcp_holders(_localize_mcp_configs(load_mcp_server_configs()))
    try:
        tools = safety_filter([t for h in connections for t in h.tools])
        if not tools:
            logger.error("无 MCP 工具，请先配置 MCP 服务器")
            return

        cases = TEST_CASES if max_cases is None else TEST_CASES[:max_cases]
        tool_filter = ToolFilter()
        table = Table(title=f"工具筛选离线评估（k={TOP_FILTER_TOOLS}，共 {len(tools)} 个工具）")
        table.add_column("query")
        table.add_column("期望")
        table.add_column(f"recall@{TOP_FILTER_TOOLS}")
        table.add_column(f"precision@{TOP_FILTER_TOOLS}")

        recall_sum = precision_sum = 0.0
        zero_hit = 0
        details = []
        for case in cases:
            expected = case["expected"]
            selected = [t.name for t in tool_filter.select_tools(case["query"], tools)][:TOP_FILTER_TOOLS]
            recall = recall_at_k(selected, expected, TOP_FILTER_TOOLS)
            precision = precision_at_k(selected, expected, TOP_FILTER_TOOLS)
            recall_sum += recall
            precision_sum += precision
            if recall == 0.0:
                zero_hit += 1
            table.add_row(case["query"][:30], str(expected), f"{recall:.2f}", f"{precision:.2f}")
            details.append({
                "query": case["query"], "expected": expected, "selected": selected,
                "recall": round(recall, 4), "precision": round(precision, 4),
            })

        console.print(table)
        n = len(cases)
        avg_recall = recall_sum / n if n else 0
        avg_precision = precision_sum / n if n else 0
        console.print(
            f"平均 recall@{TOP_FILTER_TOOLS} = {avg_recall:.2f}，"
            f"平均 precision@{TOP_FILTER_TOOLS} = {avg_precision:.2f}，"
            f"零命中 query 数 = {zero_hit}/{n}"
        )

        report = {
            "config": {"top_k": TOP_FILTER_TOOLS, "total_tools": len(tools), "test_cases": n},
            "avg_recall": round(avg_recall, 4),
            "avg_precision": round(avg_precision, 4),
            "zero_hit_count": zero_hit,
            "details": details,
        }
        output_path = Path(__file__).parent / "tool_filter_eval_report.json"
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"评测报告已保存: {output_path}")
    finally:
        # 显式关闭 MCP 连接：asyncio.run 结束时事件循环先关闭，stdio_client 的
        # async generator 被 GC 时会在已关闭/不同任务上退出 anyio cancel scope，
        # 触发 RuntimeError 使进程以非零码中断，报告写盘可能被截断
        for conn in connections:
            await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="工具筛选离线评估")
    parser.add_argument("--max-cases", type=int, default=None, help="仅跑前 N 条用例")
    args = parser.parse_args()
    asyncio.run(main(max_cases=args.max_cases))
