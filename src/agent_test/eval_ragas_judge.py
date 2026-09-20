"""
Mitta RAG 全链路 LLM-as-judge 五指标评测（H-20260919-07 P3）
================================================================
不依赖 PostgreSQL / ChatService，只走 hybrid_retrieve → LLM 生成 → DeepSeek judge。

五指标（0-1 分，DeepSeek temp=0 评分）：
  - context_precision: 检索 context 中相关片段占比（噪声越少越高）
  - context_recall:    ground_truth 事实点被 context 覆盖比例
  - faithfulness:      answer 中事实被 context 支撑的比例（抗幻觉）
  - answer_relevancy:  answer 与 question 的语义相关度
  - answer_correctness: answer 与 ground_truth 的事实匹配度

用法：
    cd src
    python -m ragas_test.eval_ragas_judge --dataset resources/knowledge-base/test-qa/eval_project_dataset.json --limit 21
"""
import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import load_vector_db_config
from vector.vector_store import create_vector_store
from init import model
from constant.retrieval_constants import RERANK_FILTER_THRESHOLD
from ragas_test.eval_retrieval import hybrid_retrieve, load_test_queries


JUDGE_MODEL_NAME = "deepseek-chat"  # 复用 init.model 的 DeepSeek；temp=0 由 judge 调用层控制

JUDGE_TEMPLATE = """你是严格的 RAG 评测员。请根据给定的【问题】【标准答案】【检索到的上下文】【模型回答】，为以下五个指标各打一个 0~1 分（保留两位小数）。

评分标准：
- context_precision: 上下文里有多少比例的内容与问题相关（0=全噪声，1=全相关）
- context_recall: 标准答案里的事实点有多少被上下文覆盖（0=全没覆盖，1=全覆盖）
- faithfulness: 模型回答里的事实陈述有多少能被上下文支撑（0=全是幻觉，1=全有依据）
- answer_relevancy: 模型回答与问题的语义相关度（0=答非所问，1=完全切题）
- answer_correctness: 模型回答与标准答案的事实匹配度（0=全错，1=完全正确）

只输出 JSON，不要解释：
{{"context_precision": 0.00, "context_recall": 0.00, "faithfulness": 0.00, "answer_relevancy": 0.00, "answer_correctness": 0.00}}

【问题】{question}
【标准答案】{ground_truth}
【检索上下文】{context}
【模型回答】{answer}
"""


def build_gen_prompt(question: str, context: str) -> str:
    """与 llm_node 检索分支 user_content 对齐（含引用约束）。"""
    return (
        f"请严格依据下面检索到的资料回答用户问题，资料中没有的内容不要编造。\n\n"
        f"【引用要求】事实性陈述后标注 [文档 i] 角标；资料无法支撑时说\"知识库暂未覆盖\"。\n\n"
        f"【检索资料】\n{context}\n\n"
        f"【用户问题】\n{question}"
    )


def judge_one(question: str, ground_truth: str, context: str, answer: str) -> dict:
    """DeepSeek temp=0 单条五指标评分。"""
    prompt = JUDGE_TEMPLATE.format(
        question=question, ground_truth=ground_truth,
        context=context[:3000], answer=answer[:2000],
    )
    try:
        resp = model.invoke(prompt, temperature=0)
        raw = resp.content if hasattr(resp, "content") else str(resp)
        # 提取 JSON
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start:end + 1])
    except Exception as e:
        logger.warning(f"judge 解析失败: {e}")
    return {k: 0.0 for k in ["context_precision", "context_recall", "faithfulness", "answer_relevancy", "answer_correctness"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--limit", type=int, default=21)
    ap.add_argument("--n-results", type=int, default=20)
    ap.add_argument("--filter-threshold", type=float, default=RERANK_FILTER_THRESHOLD)
    ap.add_argument("--output", default="ragas_judge_report.json")
    args = ap.parse_args()

    dataset_path = Path(args.dataset)
    if not dataset_path.is_absolute():
        dataset_path = Path(__file__).parent.parent.parent / args.dataset
    queries = load_test_queries(args.limit, dataset_path=dataset_path)
    if not queries:
        logger.error("无 query"); return

    logger.info("初始化向量库...")
    vector_store = create_vector_store(load_vector_db_config())
    logger.info(f"collection count: {vector_store.count()}")

    rows = []
    for i, item in enumerate(queries):
        q = item["question"]
        gt = item["ground_truth"]
        docs, _, _ = hybrid_retrieve(vector_store, q, args.n_results, args.filter_threshold)
        context = "\n\n".join(f"[文档 {j+1}] {d.text}" for j, d in enumerate(docs)) or "（无检索结果）"

        # 生成
        t0 = time.perf_counter()
        try:
            resp = model.invoke(build_gen_prompt(q, context))
            answer = resp.content if hasattr(resp, "content") else str(resp)
        except Exception as e:
            answer = f"（生成失败: {e}）"
        gen_ms = (time.perf_counter() - t0) * 1000

        # judge
        scores = judge_one(q, gt, context, answer)
        rows.append({"question": q, "gen_ms": round(gen_ms, 1), "scores": scores,
                     "answer": answer[:500]})
        logger.info(f"[{i+1}/{len(queries)}] cp={scores['context_precision']:.2f} "
                    f"cr={scores['context_recall']:.2f} f={scores['faithfulness']:.2f} "
                    f"ar={scores['answer_relevancy']:.2f} ac={scores['answer_correctness']:.2f}")

    keys = ["context_precision", "context_recall", "faithfulness", "answer_relevancy", "answer_correctness"]
    summary = {k: round(statistics.mean(r["scores"][k] for r in rows), 4) for k in keys}

    report = {
        "config": {"limit": args.limit, "n_results": args.n_results,
                   "filter_threshold": args.filter_threshold, "judge": JUDGE_MODEL_NAME},
        "summary": summary,
        "rows": rows,
    }
    out = Path(__file__).parent / args.output
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"报告: {out}")
    logger.info(f"汇总: {summary}")


if __name__ == "__main__":
    main()
