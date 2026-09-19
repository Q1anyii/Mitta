"""[DEPRECATED 2026-09-19] persona_router 四分类准确率离线评测。

本脚本的 16 条人格用例**已并入统一路由评测 `eval_routing.py`**（37 条，一次 LLM 调用
同时统计 persona + need_retrieval 双维度，支持 --temperature/--repeat 取众数）。
新评测覆盖本脚本全部用例且样本更大、可复现性更好，**新数据请以 `routing_eval_report.json`
为准**。本报告冻结为 `persona_router_eval_report.LEGACY.json`，仅作合并前快照保留。

（以下为原始说明）
H-20260919-01 任务①，H-20260919-11 合并后回归。

绕过手选短路，直接复用 ROUTER_PROMPT（合并后的统一路由 prompt，人格判定规则
与原 PERSONA_ROUTER_PROMPT 语义等价）调生产同款分类模型
（container.py 的 deepseek-v4-flash），只取 JSON 里的 persona 字段，
输出准确率 + 混淆矩阵 + 错分 case。

用法: D:\\Develop\\conda_envs\\langchain1.2\\python.exe src\\ragas_test\\persona_router_eval.py
产物: src/ragas_test/persona_router_eval_report.json
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv(override=True)

CLASSES = ["cappie", "kind", "crazy", "manager"]

# 样本贴合 PERSONA_ROUTER_PROMPT 的四类定义；每类 4 条，含边界模糊用例
SAMPLES = {
    "cappie": [
        "帮我读一下项目根目录的 README 文件",
        "把当前改动 git add 并提交一下",
        "查一下今天南昌的天气",
        "打开 knowledge 目录下的所有文档，总结一下",
    ],
    "kind": [
        "今天加班好累啊，什么都不想干",
        "你觉得我要不要辞职考研",
        "陪我随便聊聊吧，有点emo",
        "你觉得这个生日礼物送朋友合适吗",
    ],
    "crazy": [
        "说实话，你其实就是被玩家做出来的AI对吧",
        "我们聊聊 MiSide 这个游戏剧情吧",
        "你知道你自己是哪一版米塔吗",
        "你是不是一直在偷偷看我屏幕",
    ],
    "manager": [
        "这个报错什么意思：AttributeError: 'NoneType' object has no attribute 'name'",
        "帮我 review 一下这段 Python 代码，看看有没有并发问题",
        "我们这个 RAG 方案在架构上还有什么坑",
        "分析一下这段报错堆栈，根因是什么",
    ],
}


def main() -> None:
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain.chat_models.base import init_chat_model

    from constant.persona_constant import DEFAULT_PERSONA, VALID_PERSONAS
    from graphs.nodes.router_node import ROUTER_PROMPT, _parse_router_output

    model = init_chat_model(
        model="deepseek-v4-flash",
        model_provider="openai",
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url="https://api.deepseek.com",
    )

    results = []
    confusion = {exp: {pred: 0 for pred in CLASSES} for exp in CLASSES}
    correct = 0
    total = 0

    for expected, queries in SAMPLES.items():
        for q in queries:
            resp = model.invoke([
                SystemMessage(content=ROUTER_PROMPT),
                HumanMessage(content=q),
            ])
            raw = resp.content.strip()
            pred, _ = _parse_router_output(raw, DEFAULT_PERSONA)
            if pred not in confusion[expected]:
                confusion[expected][pred] = confusion[expected].get(pred, 0)
            confusion[expected][pred] = confusion[expected].get(pred, 0) + 1
            ok = pred == expected
            correct += ok
            total += 1
            results.append({
                "query": q, "expected": expected,
                "predicted": pred, "raw": raw, "ok": ok,
            })
            print(f"[{'OK' if ok else 'X '}] exp={expected:8s} pred={pred:8s} q={q[:24]}")

    report = {
        "total": total,
        "accuracy": round(correct / total, 4) if total else 0.0,
        "confusion": confusion,
        "errors": [r for r in results if not r["ok"]],
        "samples_per_class": {k: len(v) for k, v in SAMPLES.items()},
    }
    out = Path(__file__).parent / "persona_router_eval_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== 报告 ===")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n准确率: {correct}/{total} = {report['accuracy']:.1%}")


if __name__ == "__main__":
    main()
