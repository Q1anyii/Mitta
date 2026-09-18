"""
Mitta 动态路由评测（E1）
========================
评测意图分类节点（classify_node）的检索/非检索分流准确率。

指标：
  - 意图分类准确率（needs_retrieval 判断正确比例）
  - 检索召回率（真实需要检索的问题被正确路由的比例）
  - 检索误报率（闲聊问题被错误路由进检索的比例）
  - 非检索精确率 / 平均单次分类耗时

用法：
    conda activate langchain1.2
    cd src
    python -m ragas_test.eval_routing                # 默认评测
    python -m ragas_test.eval_routing --cases 20     # 用例数

说明：
  - 需 LLM 可用（init.model），白盒调用 classify_node。
  - 测试集：检索类（取自 eval_dataset.json 中的知识库问题）+ 非检索类（闲聊/通用问题）。
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List

from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graphs.nodes.classify_node import classify_node

# 检索类测试问题（应 needs_retrieval=True）：取自知识库测试集主题
RETRIEVAL_QUESTIONS = [
    "TypedDict 和 BaseModel 的区别是什么？各自适用于什么场景？",
    "FastAPI 的 Depends 依赖注入有什么优势？",
    "LangGraph 的 Checkpointer 和 Store 有什么区别？",
    "RAG 检索中为什么需要 Query 改写？直接用用户问题检索不行吗？",
    "RRF（Reciprocal Rank Fusion）融合算法的原理是什么？",
    "为什么 RAG 检索后还需要重排序（Rerank）？",
    "JWT 认证中为什么还要用 Redis 存储 token？",
    "LangGraph 的 CachePolicy 是怎么工作的？",
    "PostgresSaver 和 PostgresStore 的区别是什么？",
    "工具调用死循环是怎么防护的？",
]

# 非检索类测试问题（应 needs_retrieval=False）：闲聊/通用，无需知识库
NON_RETRIEVAL_QUESTIONS = [
    "你好呀，今天过得怎么样？",
    "谢谢你的帮助！",
    "你能帮我算一下 12 加 34 等于多少吗？",
    "讲个冷笑话听听。",
    "再见，下次聊。",
    "你觉得今天天气怎么样？",
    "我心情不太好，能陪我聊聊吗？",
    "你能直接告诉我你的名字吗？",
]


def build_cases(n: int) -> List[Dict]:
    """构造测试集：检索类 + 非检索类，按需截断。"""
    cases = []
    for q in RETRIEVAL_QUESTIONS[: max(1, n // 2)]:
        cases.append({"query": q, "expected": True})
    for q in NON_RETRIEVAL_QUESTIONS[: max(1, n - n // 2)]:
        cases.append({"query": q, "expected": False})
    return cases


def run(model, cases: List[Dict]) -> Dict:
    """对测试集逐个调用 classify_node，统计指标。"""
    tp = fp = tn = fn = 0
    latencies = []
    details = []

    for case in cases:
        t0 = time.perf_counter()
        try:
            result = classify_node({"input_str": case["query"]}, model)
            predicted = bool(result.get("needs_retrieval", False))
        except Exception as e:
            logger.warning(f"分类失败：{case['query'][:30]} -> {e}")
            predicted = not case["expected"]  # 失败视为错误
        latencies.append((time.perf_counter() - t0) * 1000)

        if predicted and case["expected"]:
            tp += 1
        elif predicted and not case["expected"]:
            fp += 1
        elif not predicted and not case["expected"]:
            tn += 1
        else:
            fn += 1
        details.append({
            "query": case["query"][:60],
            "expected": case["expected"],
            "predicted": predicted,
            "correct": predicted == case["expected"],
            "latency_ms": round(latencies[-1], 2),
        })

    total = len(cases)
    accuracy = (tp + tn) / total if total else 0
    retrieval_recall = tp / (tp + fn) if (tp + fn) else 0
    retrieval_false_positive = fp / (fp + tn) if (fp + tn) else 0
    non_retrieval_precision = tn / (tn + fn) if (tn + fn) else 0
    latencies.sort()

    return {
        "ragas_test": "routing",
        "total": total,
        "accuracy": round(accuracy, 4),
        "retrieval_recall": round(retrieval_recall, 4),
        "retrieval_false_positive": round(retrieval_false_positive, 4),
        "non_retrieval_precision": round(non_retrieval_precision, 4),
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "avg_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else 0,
        "p95_latency_ms": round(latencies[int(len(latencies) * 0.95) - 1], 2) if latencies else 0,
        "details": details,
    }


def main():
    parser = argparse.ArgumentParser(description="Mitta 动态路由评测")
    parser.add_argument("--cases", type=int, default=18, help="测试用例总数（默认 18）")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("Mitta 动态路由评测（意图分类）")
    logger.info(f"用例数: {args.cases}")
    logger.info("=" * 60)

    from init import model
    cases = build_cases(args.cases)
    logger.info(f"测试集构成：检索 {sum(1 for c in cases if c['expected'])} 条 / "
                f"非检索 {sum(1 for c in cases if not c['expected'])} 条")

    result = run(model, cases)
    # 发现标注（2026-09-18 实测）：CLASSIFIER_PROMPT 仍为「在线学习平台」业务路由文案，
    # 而实际知识库为 Mitta 项目技术文档（01-python-best-practices 等 10 篇）。
    # 结果：技术概念题全部被判 needs_retrieval=False，检索召回 0%。如实记录，供决策。
    result["note"] = (
        "发现：constant/prompt_constants.py CLASSIFIER_PROMPT 仍为「在线学习平台」路由文案，"
        "与实际知识库（Mitta 技术文档）主题不一致；技术概念题被判无需检索。"
        "需确认是否应更新路由 prompt 以匹配实际知识库主题。"
    )
    logger.info("【评测结果】")
    logger.info(f"  意图分类准确率: {result['accuracy'] * 100:.1f}%")
    logger.info(f"  检索召回率: {result['retrieval_recall'] * 100:.1f}%")
    logger.info(f"  检索误报率: {result['retrieval_false_positive'] * 100:.1f}%")
    logger.info(f"  非检索精确率: {result['non_retrieval_precision'] * 100:.1f}%")
    logger.info(f"  平均耗时: {result['avg_latency_ms']}ms, P95: {result['p95_latency_ms']}ms")

    for d in result["details"]:
        mark = "✓" if d["correct"] else "✗"
        logger.info(f"  [{mark}] (期望 {d['expected']}, 实际 {d['predicted']}) {d['query']}")

    output_path = Path(__file__).parent / "routing_eval_report.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"评测报告已保存: {output_path}")


if __name__ == "__main__":
    main()
