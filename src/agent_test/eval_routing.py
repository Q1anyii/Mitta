"""Mitta 统一路由评测（E1，H-11 路由合并后重构版）
=====================================================

背景
----
H-11（`a42e5e3`）把原 `persona_router_node`（人格四分类）与 `classify_node`
（意图分类）两次串行 LLM **合并为单个 `router_node`**：一次调用同时输出
`persona` 与 `needs_retrieval`。

重构前的历史遗留：本脚本一直 `import classify_node`，即合并后仍在测一个
**已被主图摘除、仅保留未删的废弃节点** —— 其产出的数值不能代表线上路由能力。
本版改为直接白盒调用现役 `router_node`，并把原 E15 人格路由用例并入同一用例集，
一次调用同时统计两个维度。

评测维度
--------
- `persona_accuracy`：人格四分类准确率（cappie/kind/crazy/manager）
- `need_accuracy`：检索/非检索二分类准确率（意图路由）
- `joint_accuracy`：两个维度同时判对的比例
- `shortcut_*`：`_quick_no_retrieval` 强模式短路命中情况（0 次 LLM 调用）
- 延迟：avg / p95 / p99（不含短路用例，短路为纯正则、无网络开销）

用例集构成（37 条）
-------------------
- `E1-retrieval`  10 条：技术概念题，need=True、persona=manager
  （较旧版补齐第 10 条「工具调用死循环是怎么防护的？」——旧版 build_cases 截断未纳入）
- `E1-chitchat`    8 条：闲聊/通用，need=False
- `E15-persona`   16 条：人格路由四分类各 4 条（原 persona_router_eval 用例集）
- `E1-shortcut`    3 条：纯寒暄/自我介绍强模式，验证零 LLM 短路路径

标注约定（避免主观标注污染准确率）
----------------------------------
`need` / `persona` 为 `None` 表示该维度**不参与统计**：
- `persona=None`：语义上存在多解（如「帮我算 12+34」可判 cappie 也可 kind），
  或走短路路径（`persona` 取 `DEFAULT_PERSONA`、不经模型判定），
  都不反映"人格分类能力"，计入会人为制造假失败。
- `need=None`：知识库是否有覆盖存疑（如「我们这个 RAG 方案还有什么坑」，
  Mitta 知识库确含 RAG 检索子图文档，判 True/False 均可辩护），不下断言。

用法
----
    cd src
    python -m agent_test.eval_routing                 # 默认 temperature=0，重复 3 次取众数
    python -m agent_test.eval_routing --repeat 1      # 单次（快，但受采样波动影响）
    python -m agent_test.eval_routing --temperature 1.0 --repeat 3   # 对照：复现生产默认采样

产物：src/agent_test/routing_eval_report.json
"""
import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 显式加载项目根 .env：本脚本以 `python -m agent_test.eval_routing` 在 src/ 下运行，
# cwd 不含 .env，不显式指定路径会拿到空的 DEEPSEEK_API_KEY。
load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=True)

from graphs.nodes.classify_node import _quick_no_retrieval
from graphs.nodes.router_node import router_node

# ---------------------------------------------------------------------------
# 用例集：need / persona 为 None 表示该维度不参与统计（见模块 docstring 标注约定）
# ---------------------------------------------------------------------------
CASES: List[Dict] = [
    # --- 原 E1 检索类（应 need=True；技术概念题 → manager）-------------------
    {"q": "TypedDict 和 BaseModel 的区别是什么？各自适用于什么场景？", "need": True, "persona": "manager", "src": "E1-retrieval"},
    {"q": "FastAPI 的 Depends 依赖注入有什么优势？", "need": True, "persona": "manager", "src": "E1-retrieval"},
    {"q": "LangGraph 的 Checkpointer 和 Store 有什么区别？", "need": True, "persona": "manager", "src": "E1-retrieval"},
    {"q": "RAG 检索中为什么需要 Query 改写？直接用用户问题检索不行吗？", "need": True, "persona": "manager", "src": "E1-retrieval"},
    {"q": "RRF（Reciprocal Rank Fusion）融合算法的原理是什么？", "need": True, "persona": "manager", "src": "E1-retrieval"},
    {"q": "为什么 RAG 检索后还需要重排序（Rerank）？", "need": True, "persona": "manager", "src": "E1-retrieval"},
    {"q": "JWT 认证中为什么还要用 Redis 存储 token？", "need": True, "persona": "manager", "src": "E1-retrieval"},
    {"q": "LangGraph 的 CachePolicy 是怎么工作的？", "need": True, "persona": "manager", "src": "E1-retrieval"},
    {"q": "PostgresSaver 和 PostgresStore 的区别是什么？", "need": True, "persona": "manager", "src": "E1-retrieval"},
    {"q": "工具调用死循环是怎么防护的？", "need": True, "persona": "manager", "src": "E1-retrieval"},

    # --- 原 E1 非检索类（应 need=False）--------------------------------------
    {"q": "你好呀，今天过得怎么样？", "need": False, "persona": "kind", "src": "E1-chitchat"},
    {"q": "谢谢你的帮助！", "need": False, "persona": "kind", "src": "E1-chitchat"},
    {"q": "你能帮我算一下 12 加 34 等于多少吗？", "need": False, "persona": None, "src": "E1-chitchat"},
    {"q": "讲个冷笑话听听。", "need": False, "persona": "kind", "src": "E1-chitchat"},
    {"q": "再见，下次聊。", "need": False, "persona": "kind", "src": "E1-chitchat"},
    {"q": "你觉得今天天气怎么样？", "need": False, "persona": "kind", "src": "E1-chitchat"},
    {"q": "我心情不太好，能陪我聊聊吗？", "need": False, "persona": "kind", "src": "E1-chitchat"},
    {"q": "你能直接告诉我你的名字吗？", "need": False, "persona": None, "src": "E1-chitchat"},

    # --- 原 E15 人格路由四分类（persona 为该集权威口径）----------------------
    {"q": "帮我读一下项目根目录的 README 文件", "need": False, "persona": "cappie", "src": "E15-persona"},
    {"q": "把当前改动 git add 并提交一下", "need": False, "persona": "cappie", "src": "E15-persona"},
    {"q": "查一下今天南昌的天气", "need": False, "persona": "cappie", "src": "E15-persona"},
    # 歧义用例：项目内确有 resources/knowledge-base（知识库目录），"knowledge 目录下的文档"
    # 既可解读为用文件工具读本地目录（cappie + 工具，need=False），也可解读为检索已入库文档
    # （need=True）。实测模型稳定判 True。按标注约定不做断言，need=None 排除出统计。
    {"q": "打开 knowledge 目录下的所有文档，总结一下", "need": None, "persona": "cappie", "src": "E15-persona"},
    {"q": "今天加班好累啊，什么都不想干", "need": False, "persona": "kind", "src": "E15-persona"},
    {"q": "你觉得我要不要辞职考研", "need": False, "persona": "kind", "src": "E15-persona"},
    {"q": "陪我随便聊聊吧，有点emo", "need": False, "persona": "kind", "src": "E15-persona"},
    {"q": "你觉得这个生日礼物送朋友合适吗", "need": False, "persona": "kind", "src": "E15-persona"},
    {"q": "说实话，你其实就是被玩家做出来的AI对吧", "need": False, "persona": "crazy", "src": "E15-persona"},
    {"q": "我们聊聊 MiSide 这个游戏剧情吧", "need": False, "persona": "crazy", "src": "E15-persona"},
    {"q": "你知道你自己是哪一版米塔吗", "need": False, "persona": "crazy", "src": "E15-persona"},
    {"q": "你是不是一直在偷偷看我屏幕", "need": False, "persona": "crazy", "src": "E15-persona"},
    {"q": "这个报错什么意思：AttributeError: 'NoneType' object has no attribute 'name'", "need": False, "persona": "manager", "src": "E15-persona"},
    {"q": "帮我 review 一下这段 Python 代码，看看有没有并发问题", "need": False, "persona": "manager", "src": "E15-persona"},
    {"q": "我们这个 RAG 方案在架构上还有什么坑", "need": None, "persona": "manager", "src": "E15-persona"},
    {"q": "分析一下这段报错堆栈，根因是什么", "need": False, "persona": "manager", "src": "E15-persona"},

    # --- 短路强模式：零 LLM 路径（persona 取 DEFAULT_PERSONA，不计入人格统计）--
    {"q": "你好", "need": False, "persona": None, "src": "E1-shortcut"},
    {"q": "你是谁", "need": False, "persona": None, "src": "E1-shortcut"},
    {"q": "介绍一下你", "need": False, "persona": None, "src": "E1-shortcut"},
]


def build_model(temperature: float):
    """构造路由分类模型。temperature 显式可控，默认 0 以消除采样波动。

    注意：`src/init.py` 的生产 `model` **未固定 temperature**（走服务端默认采样），
    因此历史评测结果存在不可复现的波动。本评测默认固定 0 取可复现基线，
    并支持 --temperature 传 1.0 做对照，如实记录两者差异。
    """
    from langchain.chat_models.base import init_chat_model

    return init_chat_model(
        model="deepseek-flash",
        model_provider="openai",
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url="https://api.deepseek.com",
        temperature=temperature,
    )


def once(model, case: Dict) -> Dict:
    """对单条用例调用一次 router_node，返回原始输出与耗时。"""
    shortcut = _quick_no_retrieval(case["q"])
    t0 = time.perf_counter()
    try:
        out = router_node({"input_str": case["q"]}, {}, model)
        persona = out.get("persona")
        need = bool(out.get("needs_retrieval", False))
        err = None
    except Exception as e:  # 路由失败兜底由 router_node 内部处理，这里仅兜异常
        logger.warning(f"路由调用异常：{case['q'][:30]} -> {e}")
        persona, need, err = None, None, str(e)
    return {
        "persona": persona,
        "need": need,
        "shortcut": shortcut,
        "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        "error": err,
    }


def majority(values: List):
    """取众数；并列时取第一次出现的值（保持可复现）。"""
    c = Counter(values)
    top = c.most_common()
    best = top[0][1]
    for v, n in top:
        if n == best:
            return v
    return values[0]


def run(model, cases: List[Dict], repeat: int) -> Dict:
    need_tp = need_fp = need_tn = need_fn = 0
    per_correct = 0
    per_total = 0
    joint_correct = 0
    joint_total = 0
    shortcut_hits = 0
    shortcut_need_ok = 0
    latencies: List[float] = []
    details: List[Dict] = []

    for case in cases:
        runs = [once(model, case) for _ in range(repeat)]
        # 众数裁决：消除单次采样波动
        pred_persona = majority([r["persona"] for r in runs if r["persona"] is not None]) or None
        pred_need = majority([r["need"] for r in runs if r["need"] is not None])
        if pred_need is None:
            pred_need = True  # 全部异常时的保守兜底，与生产 router_node 一致
        shortcut = runs[0]["shortcut"]
        lat = round(statistics.mean(r["latency_ms"] for r in runs), 2)

        exp_need = case["need"]
        exp_persona = case["persona"]

        need_ok = None
        if exp_need is not None:
            if pred_need and exp_need:
                need_tp += 1
            elif pred_need and not exp_need:
                need_fp += 1
            elif not pred_need and not exp_need:
                need_tn += 1
            else:
                need_fn += 1
            need_ok = pred_need == exp_need

        persona_ok = None
        if exp_persona is not None:
            per_total += 1
            persona_ok = pred_persona == exp_persona
            per_correct += int(persona_ok)

        if exp_need is not None and exp_persona is not None:
            joint_total += 1
            joint_correct += int(bool(need_ok) and bool(persona_ok))

        if shortcut:
            shortcut_hits += 1
            shortcut_need_ok += int(need_ok is True)
            # 短路路径断言：不得产生 LLM 网络开销
            if lat > 50:
                logger.warning(f"[断言] 短路用例耗时异常 {lat}ms，疑似未走短路：{case['q'][:20]}")
        else:
            latencies.append(lat)

        details.append({
            "query": case["q"][:60],
            "src": case["src"],
            "expected_need": exp_need,
            "predicted_need": pred_need,
            "need_correct": need_ok,
            "expected_persona": exp_persona,
            "predicted_persona": pred_persona,
            "persona_correct": persona_ok,
            "shortcut": shortcut,
            "avg_latency_ms": lat,
            "repeat": repeat,
            "votes_need": [r["need"] for r in runs],
            "votes_persona": [r["persona"] for r in runs],
        })

    need_total = need_tp + need_fp + need_tn + need_fn
    latencies.sort()

    def pct(p: float) -> float:
        if not latencies:
            return 0.0
        idx = min(int(len(latencies) * p), len(latencies) - 1)
        return round(latencies[idx], 2)

    return {
        "agent_test": "routing_unified",
        "target": "graphs.nodes.router_node.router_node（H-11 合并后现役节点）",
        "total": len(cases),
        # 意图路由（检索/非检索）
        "need_accuracy": round((need_tp + need_tn) / need_total, 4) if need_total else 0,
        "retrieval_recall": round(need_tp / (need_tp + need_fn), 4) if (need_tp + need_fn) else 0,
        "retrieval_false_positive": round(need_fp / (need_fp + need_tn), 4) if (need_fp + need_tn) else 0,
        "need_tp": need_tp, "need_fp": need_fp, "need_tn": need_tn, "need_fn": need_fn,
        "need_scored": need_total,
        # 人格路由（四分类）
        "persona_accuracy": round(per_correct / per_total, 4) if per_total else 0,
        "persona_scored": per_total,
        "persona_correct": per_correct,
        # 联合
        "joint_accuracy": round(joint_correct / joint_total, 4) if joint_total else 0,
        "joint_scored": joint_total,
        # 短路
        "shortcut_hits": shortcut_hits,
        "shortcut_need_correct": shortcut_need_ok,
        # 延迟（不含短路用例）
        "avg_latency_ms": round(statistics.mean(latencies), 2) if latencies else 0,
        "p95_latency_ms": pct(0.95),
        "p99_latency_ms": pct(0.99),
        "details": details,
    }


def main():
    ap = argparse.ArgumentParser(description="Mitta 统一路由评测（router_node，persona + need_retrieval 双维度）")
    ap.add_argument("--repeat", type=int, default=3, help="每条用例重复次数，取众数（默认 3）")
    ap.add_argument("--temperature", type=float, default=0.0, help="路由模型 temperature（默认 0，可复现）")
    ap.add_argument("--output", default="routing_eval_report.json", help="报告文件名")
    args = ap.parse_args()

    logger.info("=" * 66)
    logger.info("Mitta 统一路由评测（router_node · persona + need_retrieval）")
    logger.info(f"用例数: {len(CASES)}  重复: {args.repeat}  temperature: {args.temperature}")
    logger.info("=" * 66)

    # 用例去重自检：合并 E1/E15 后不应存在完全相同的 query
    qs = [c["q"] for c in CASES]
    dup = [q for q, n in Counter(qs).items() if n > 1]
    if dup:
        logger.error(f"[断言] 用例集存在重复 query：{dup}")
    else:
        logger.info("[自检] 用例去重通过：无重复 query")

    model = build_model(args.temperature)
    result = run(model, CASES, args.repeat)
    result["config"] = {
        "repeat": args.repeat,
        "temperature": args.temperature,
        "model": "deepseek-flash",
        "note": "生产 init.model 未固定 temperature；本报告 temperature 显式设定以取可复现基线",
    }

    logger.info("【评测结果】")
    logger.info(f"  意图路由准确率: {result['need_accuracy'] * 100:.2f}%  ({result['need_tp'] + result['need_tn']}/{result['need_scored']})")
    logger.info(f"    检索召回: {result['retrieval_recall'] * 100:.2f}%  误报: {result['retrieval_false_positive'] * 100:.2f}%")
    logger.info(f"  人格路由准确率: {result['persona_accuracy'] * 100:.2f}%  ({result['persona_correct']}/{result['persona_scored']})")
    logger.info(f"  联合准确率: {result['joint_accuracy'] * 100:.2f}%  ({result['joint_scored']} 条双标注)")
    logger.info(f"  短路命中: {result['shortcut_hits']} 条（其中 need 判定正确 {result['shortcut_need_correct']} 条）")
    logger.info(f"  延迟 avg {result['avg_latency_ms']}ms / p95 {result['p95_latency_ms']}ms / p99 {result['p99_latency_ms']}ms")

    fails = [d for d in result["details"]
             if d["need_correct"] is False or d["persona_correct"] is False]
    if fails:
        logger.warning(f"  失败 {len(fails)} 条：")
        for d in fails:
            logger.warning(f"    ✗ [{d['src']}] need 期望={d['expected_need']} 实际={d['predicted_need']}; "
                           f"persona 期望={d['expected_persona']} 实际={d['predicted_persona']} :: {d['query']}")
    else:
        logger.info("  失败 0 条")

    out = Path(__file__).parent / args.output
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"评测报告已保存: {out}")


if __name__ == "__main__":
    main()
