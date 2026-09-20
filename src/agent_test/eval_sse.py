"""
Mitta SSE 首 token 延迟与流纯净度评估（H-11 路由合并后重构版）
================================================================

为什么要重构
------------
原实现只测「一条检索类 query 重复 N 次」，在 H-11（路由合并）后已不能反映真实分布：

H-11 把 `persona_router_node` + `classify_node` 两次串行 LLM 合并为单个 `router_node`，
**首 token 之前的链路变成三档，延迟量级完全不同**：

| 场景           | 首 token 之前发生什么                          | 路由 LLM 次数 |
|----------------|------------------------------------------------|---------------|
| `shortcut`     | 强模式正则命中，直答                            | 0             |
| `no_retrieval` | 一次路由 LLM 判定为不需检索 → 直答              | 1             |
| `retrieval`    | 一次路由 LLM → 查询改写+稠密+BM25+RRF+rerank    | 1 + 检索链路  |

旧报告 `sse_eval_report.json`（2026-08-23）5 次请求全是「介绍一下 LangGraph 的核心概念」
（检索类），avg 3843ms；而 `online_eval_report.json`（2026-09-18）测的是自我介绍，
1348ms。**两者场景不同、数值不可比**——这正是本版分场景测量的原因。

其他修正
--------
1. 默认端口 8000 → 与 `.env` 的 `MITTA_API_PORT=18000` 不符，改为显式 `--base-url`；
2. 增加预热请求（不计入统计），避免冷启动（连接池/MCP/embedding 索引）污染首个样本；
3. 每场景用**多条不同 query** 而非单条重复——单条重复会命中检索语义缓存
   （RedisSearch LSH + reranker 两级），第 2 次起测到的是缓存路径而非冷路径；
   因此本版分别统计 `cold`（每条 query 的首次）与 `warm`（第二次）；
4. 污染模式表更新：H-11 后 `classify_node` 已废弃，新增 `router_node`/`persona` 等。

用法
----
    cd src
    python -m agent_test.eval_sse --base-url https://www.mittaai.xyz --rounds 2
    python -m agent_test.eval_sse --base-url http://127.0.0.1:18000 --scenario retrieval

产物：src/agent_test/sse_eval_report.json
"""
import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import httpx
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 登录候选：线上为 qianyi，本地种子数据为常见测试账号
TEST_USERS = [
    {"userId": "qianyi", "password": "1234"},
    {"userId": "user_01", "password": "1234"},
    {"userId": "zhangsan", "password": "1234"},
    {"userId": "admin", "password": "admin123"},
]

# H-11 后首 token 前的三档链路（见模块 docstring）
SCENARIOS: Dict[str, List[str]] = {
    "shortcut": [
        "你好",
        "你是谁",
        "介绍一下你",
    ],
    "no_retrieval": [
        "讲个冷笑话听听。",
        "你能帮我算一下 12 加 34 等于多少吗？",
        "我心情不太好，能陪我聊聊吗？",
    ],
    "retrieval": [
        "介绍一下 LangGraph 的核心概念",
        "LangGraph 的 Checkpointer 和 Store 有什么区别？",
        "RAG 检索中为什么需要 Query 改写？",
    ],
}

# 不应出现在用户可见输出里的内部串。classify_node 为 H-11 前的遗留串，保留以检测旧版本输出
CONTAMINATION_PATTERNS = [
    "needs_retrieval", "分类结果", "classify_node", "persona_router_node",
    "router_node", "persona", "memory_node", "记忆提取", "idle 闲聊",
    "tool_filter", "工具筛选", "检索结果", "DEBUG", "TRACE",
]


def login(base_url: str, username: Optional[str] = None, password: Optional[str] = None) -> str:
    """登录获取 JWT token。"""
    candidates = []
    if username and password:
        candidates.append({"userId": username, "password": password})
    candidates.extend(TEST_USERS)

    for user in candidates:
        try:
            resp = httpx.post(f"{base_url}/api/login", json=user, timeout=15, follow_redirects=True)
            if resp.status_code == 200:
                data = resp.json()
                token = data.get("token") or data.get("data", {}).get("token")
                if token:
                    logger.info(f"登录成功: {user['userId']}")
                    return token
        except Exception as e:
            logger.debug(f"登录异常 {user['userId']}: {e}")
    logger.warning("无法自动获取 token，请手动提供 --token 参数")
    return ""


def test_sse_latency(base_url: str, token: str, query: str, thread_id: str) -> Dict:
    """测试单次 SSE 请求的首 token 延迟和流纯净度。"""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    payload = {"query": query, "thread_id": thread_id}

    first_content_time = None
    done_time = None
    content_chunks: List[str] = []
    tool_call_events: List[str] = []
    error_events: List[str] = []
    events_before_first_token: List[str] = []
    request_start = None

    try:
        with httpx.stream(
            "POST",
            f"{base_url}/api/chat/",
            headers=headers,
            json=payload,
            timeout=120.0,
            follow_redirects=True,
        ) as resp:
            request_start = time.perf_counter()
            if resp.status_code != 200:
                return {"error": f"HTTP {resp.status_code}", "detail": resp.text[:300]}

            for line in resp.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    done_time = time.perf_counter()
                    break
                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                # 记录首 token 之前到达的事件类型：用于判断该请求是否走了检索/工具链路
                if first_content_time is None:
                    events_before_first_token.append(
                        next(iter(event.keys()), "unknown") if isinstance(event, dict) else "raw"
                    )

                if "content" in event:
                    if first_content_time is None:
                        first_content_time = time.perf_counter()
                    content_chunks.append(event["content"])
                elif "tool_call_start" in event:
                    tool_call_events.append(event["tool_call_start"].get("name", "unknown"))
                elif "error" in event:
                    error_events.append(event["error"])

    except Exception as e:
        return {"error": str(e), "detail": type(e).__name__}

    if request_start is None:
        return {"error": "请求未发出"}

    total_time = (done_time or time.perf_counter()) - request_start
    first_token_latency = (first_content_time - request_start) if first_content_time else None
    full_content = "".join(content_chunks)
    contamination = [p for p in CONTAMINATION_PATTERNS if p in full_content]

    return {
        "query": query,
        "thread_id": thread_id,
        "first_token_latency_ms": round(first_token_latency * 1000, 2) if first_token_latency else None,
        "total_time_ms": round(total_time * 1000, 2),
        "content_length": len(full_content),
        "chunk_count": len(content_chunks),
        "tool_calls": tool_call_events,
        "events_before_first_token": events_before_first_token,
        "errors": error_events,
        "contamination": contamination,
        "is_pure": len(contamination) == 0 and len(error_events) == 0,
    }


def _stats(values: List[float]) -> Dict:
    """样本统计。样本量 <20 时 p95 不可靠，显式标注 confidence。"""
    if not values:
        return {"n": 0}
    s = sorted(values)
    return {
        "n": len(s),
        "avg_ms": round(statistics.mean(s), 2),
        "min_ms": round(s[0], 2),
        "max_ms": round(s[-1], 2),
        "p50_ms": round(statistics.median(s), 2),
        "p95_ms": round(s[min(int(len(s) * 0.95), len(s) - 1)], 2),
        "confidence": "low" if len(s) < 20 else "ok",
    }


def run_scenario(base_url: str, token: str, scenario: str, queries: List[str],
                 rounds: int, warmup: bool) -> Dict:
    """跑一个场景：每条 query 跑 rounds 次，第 1 次为 cold，其余为 warm。"""
    if warmup:
        logger.info(f"  [预热] {scenario}: {queries[0][:24]}")
        test_sse_latency(base_url, token, queries[0], f"sse_warmup_{scenario}")

    cold, warm, details = [], [], []
    for qi, q in enumerate(queries):
        for r in range(rounds):
            tid = f"sse_{scenario}_{qi}_{r}_{int(time.time())}"
            res = test_sse_latency(base_url, token, q, tid)
            if "error" in res:
                logger.warning(f"  ✗ [{scenario}] {q[:20]} -> {res['error']}")
                details.append({"scenario": scenario, "query": q, "round": r, "error": res["error"]})
                continue
            res["scenario"] = scenario
            res["round"] = r
            res["cold"] = (r == 0)
            details.append(res)
            (cold if r == 0 else warm).append(res["first_token_latency_ms"])
            tag = "cold" if r == 0 else "warm"
            logger.info(f"  [{scenario}/{tag}] 首 token {res['first_token_latency_ms']:.0f}ms "
                        f"总 {res['total_time_ms'] / 1000:.1f}s 纯净 {'✓' if res['is_pure'] else '✗'} :: {q[:24]}")

    return {
        "scenario": scenario,
        "queries": queries,
        "rounds": rounds,
        "cold": _stats(cold),
        "warm": _stats(warm) if warm else {"n": 0},
        "details": details,
    }


def main():
    ap = argparse.ArgumentParser(description="Mitta SSE 首 token 评估（分场景：shortcut/no_retrieval/retrieval）")
    ap.add_argument("--base-url", default="http://127.0.0.1:18000",
                    help="后端地址（默认 18000，与 .env MITTA_API_PORT 一致；线上用 https://www.mittaai.xyz）")
    ap.add_argument("--token", default="", help="JWT token（不填则自动登录）")
    ap.add_argument("--rounds", type=int, default=2, help="每条 query 重复轮次（默认 2：cold + warm）")
    ap.add_argument("--scenario", default="all", choices=["all", "shortcut", "no_retrieval", "retrieval"])
    ap.add_argument("--no-warmup", action="store_true", help="跳过预热请求")
    args = ap.parse_args()

    logger.info("=" * 66)
    logger.info("Mitta SSE 首 token 评估（H-11 分场景版）")
    logger.info(f"服务: {args.base_url}   轮次: {args.rounds}   场景: {args.scenario}")
    logger.info("=" * 66)

    token = args.token or login(args.base_url)
    if not token:
        logger.error("无法获取 JWT token，请检查服务与账号")
        return

    try:
        resp = httpx.get(f"{args.base_url}/health", timeout=10, follow_redirects=True)
        body = resp.text[:120]
        if resp.status_code != 200 or '"db":true' not in body:
            logger.error(f"健康检查失败: HTTP {resp.status_code} {body}")
            return
        logger.success(f"健康检查通过: {body}")
    except Exception as e:
        logger.error(f"无法连接服务: {e}")
        return

    targets = SCENARIOS if args.scenario == "all" else {args.scenario: SCENARIOS[args.scenario]}
    report = {
        "agent_test": "sse_first_token_by_scenario",
        "base_url": args.base_url,
        "rounds": args.rounds,
        "warmup": not args.no_warmup,
        "scenarios": {},
    }

    for name, queries in targets.items():
        logger.info(f"【场景 {name}】{len(queries)} 条 query × {args.rounds} 轮")
        report["scenarios"][name] = run_scenario(
            args.base_url, token, name, queries, args.rounds, not args.no_warmup)

    all_cold = [d["first_token_latency_ms"]
                for s in report["scenarios"].values() for d in s["details"]
                if d.get("cold") and "error" not in d]
    pure = sum(1 for s in report["scenarios"].values() for d in s["details"]
               if "error" not in d and d.get("is_pure"))
    total = sum(1 for s in report["scenarios"].values() for d in s["details"] if "error" not in d)
    report["overall"] = {
        "cold_first_token": _stats(all_cold),
        "stream_purity": {"pure": pure, "total": total, "rate": round(pure / total, 4) if total else 0},
    }

    logger.info("=" * 66)
    logger.info("【汇总】首 token（cold = 每条 query 首次，未命中检索缓存）")
    for name, s in report["scenarios"].items():
        c = s["cold"]
        logger.info(f"  {name:13s} n={c.get('n', 0)}  avg {c.get('avg_ms', 0):.0f}ms  "
                    f"p50 {c.get('p50_ms', 0):.0f}ms  min {c.get('min_ms', 0):.0f}ms  max {c.get('max_ms', 0):.0f}ms")
    logger.info(f"  流纯净度: {pure}/{total}")
    logger.info("=" * 66)

    out = Path(__file__).parent / "sse_eval_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"评估报告已保存: {out}")


if __name__ == "__main__":
    main()
