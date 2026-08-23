"""
Mitta SSE 首 token 延迟与流纯净度评估
======================================
评估指标：
  - 首 token 延迟：从请求发出到首个 content 事件到达的时间
  - 流纯净度：验证输出流中无分类器/记忆提取等内部节点的混入内容
  - 完整响应时间：从请求到 [DONE] 的总耗时

用法：
    conda activate langchain1.2
    cd src
    python -m ragas_test.eval_sse                  # 默认测试
    python -m ragas_test.eval_sse --requests 10    # 请求次数
    python -m ragas_test.eval_sse --query "你好"   # 测试 query

注意：
  - 需要启动后端服务（FastAPI），本脚本通过 HTTP 调用 /api/chat/ 接口
  - 需要有效的 JWT token（测试用户登录后获取）
  - 若服务未启动，脚本会提示并退出
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List

import httpx
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def get_test_token(base_url: str) -> str:
    """获取测试用户的 JWT token（通过登录接口）。"""
    # 登录接口字段：userId + password，响应：{"ok": true, "token": "..."}
    test_users = [
        {"userId": "user_01", "password": "1234"},
        {"userId": "zhangsan", "password": "1234"},
        {"userId": "admin", "password": "admin123"},
    ]
    for user in test_users:
        try:
            resp = httpx.post(
                f"{base_url}/api/login",
                json=user,
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                token = data.get("token") or data.get("data", {}).get("token")
                if token and data.get("ok", True):
                    logger.info(f"登录成功: {user['userId']}")
                    return token
        except Exception:
            continue
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
    content_chunks = []
    tool_call_events = []
    error_events = []
    other_events = []
    request_start = None

    try:
        with httpx.stream(
            "POST",
            f"{base_url}/api/chat/",
            headers=headers,
            json=payload,
            timeout=120.0,
        ) as resp:
            request_start = time.perf_counter()
            if resp.status_code != 200:
                return {"error": f"HTTP {resp.status_code}", "detail": resp.text[:500]}

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
                    other_events.append(data_str[:100])
                    continue

                if "content" in event:
                    if first_content_time is None:
                        first_content_time = time.perf_counter()
                    content_chunks.append(event["content"])
                elif "tool_call_start" in event:
                    tool_call_events.append(event["tool_call_start"].get("name", "unknown"))
                elif "tool_call_end" in event:
                    pass  # 工具调用结束，正常事件
                elif "error" in event:
                    error_events.append(event["error"])
                else:
                    other_events.append(str(event)[:100])

    except Exception as e:
        return {"error": str(e), "detail": type(e).__name__}

    if request_start is None:
        return {"error": "请求未发出"}

    total_time = (done_time or time.perf_counter()) - request_start
    first_token_latency = (first_content_time - request_start) if first_content_time else None

    # 流纯净度检查：不应出现分类器/记忆提取等内部内容
    full_content = "".join(content_chunks)
    suspicious_patterns = [
        "needs_retrieval", "分类结果", "classify_node",
        "memory_node", "记忆提取", "idle 闲聊",
        "tool_filter", "工具筛选", "检索结果",
        "DEBUG", "TRACE",
    ]
    contamination = [p for p in suspicious_patterns if p in full_content]

    return {
        "query": query,
        "first_token_latency_ms": first_token_latency * 1000 if first_token_latency else None,
        "total_time_ms": total_time * 1000,
        "content_length": len(full_content),
        "chunk_count": len(content_chunks),
        "tool_calls": tool_call_events,
        "errors": error_events,
        "other_events": other_events,
        "contamination": contamination,
        "is_pure": len(contamination) == 0 and len(error_events) == 0,
    }


def main():
    parser = argparse.ArgumentParser(description="SSE 首 token 延迟与流纯净度评估")
    parser.add_argument("--base-url", type=str, default="http://127.0.0.1:8000",
                        help="后端服务地址（默认 http://127.0.0.1:8000）")
    parser.add_argument("--token", type=str, default="", help="JWT token（不填则尝试自动登录）")
    parser.add_argument("--requests", type=int, default=5, help="测试请求次数（默认 5）")
    parser.add_argument("--query", type=str, default="介绍一下 LangGraph 的核心概念",
                        help="测试 query")
    parser.add_argument("--thread-id", type=str, default="sse_eval_test",
                        help="测试会话 ID")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("Mitta SSE 首 token 延迟与流纯净度评估")
    logger.info(f"服务: {args.base_url}")
    logger.info(f"请求次数: {args.requests}")
    logger.info(f"Query: {args.query}")
    logger.info("=" * 60)

    # 获取 token
    token = args.token or get_test_token(args.base_url)
    if not token:
        logger.error("无法获取 JWT token，请启动服务并用 --token 参数提供")
        return

    # 检查服务是否可用
    try:
        resp = httpx.get(f"{args.base_url}/health", timeout=5)
        if resp.status_code != 200:
            logger.error(f"服务健康检查失败: {resp.status_code}")
            return
        logger.success("服务健康检查通过")
    except Exception as e:
        logger.error(f"无法连接服务: {e}")
        logger.error("请先启动后端服务（uvicorn main:app --port 8000）")
        return

    # 运行测试
    results = []
    for i in range(1, args.requests + 1):
        logger.info(f"[{i}/{args.requests}] 发送请求...")
        result = test_sse_latency(args.base_url, token, args.query,
                                  f"{args.thread_id}_{i}")
        results.append(result)

        if "error" in result:
            logger.warning(f"  请求失败: {result['error']}")
        else:
            lat = result.get("first_token_latency_ms")
            lat_str = f"{lat:.0f}ms" if lat else "N/A"
            logger.info(f"  首 token: {lat_str}, 总耗时: {result['total_time_ms']:.0f}ms, "
                        f"内容: {result['content_length']}字, 纯净: {'✓' if result['is_pure'] else '✗'}")
            if result["contamination"]:
                logger.warning(f"  检测到混入内容: {result['contamination']}")
            if result["errors"]:
                logger.warning(f"  错误事件: {result['errors']}")

    # 汇总统计
    valid_results = [r for r in results if "error" not in r and r.get("first_token_latency_ms")]
    if not valid_results:
        logger.error("没有成功的请求结果")
        return

    latencies = [r["first_token_latency_ms"] for r in valid_results]
    total_times = [r["total_time_ms"] for r in valid_results]
    pure_count = sum(1 for r in valid_results if r["is_pure"])

    summary = {
        "total_requests": args.requests,
        "successful": len(valid_results),
        "failed": args.requests - len(valid_results),
        "first_token": {
            "avg_ms": sum(latencies) / len(latencies),
            "min_ms": min(latencies),
            "max_ms": max(latencies),
            "p50_ms": sorted(latencies)[len(latencies) // 2],
            "p95_ms": sorted(latencies)[int(len(latencies) * 0.95) - 1],
        },
        "total_time": {
            "avg_ms": sum(total_times) / len(total_times),
            "min_ms": min(total_times),
            "max_ms": max(total_times),
        },
        "stream_purity": {
            "pure_count": pure_count,
            "pure_rate": pure_count / len(valid_results),
            "contaminated": [r["query"] for r in valid_results if not r["is_pure"]],
        },
    }

    logger.info(f"\n{'='*60}")
    logger.info("【SSE 评估汇总】")
    logger.info(f"  成功/总数: {len(valid_results)}/{args.requests}")
    logger.info(f"  首 token 延迟: 平均 {summary['first_token']['avg_ms']:.0f}ms, "
                f"P50 {summary['first_token']['p50_ms']:.0f}ms, "
                f"P95 {summary['first_token']['p95_ms']:.0f}ms")
    logger.info(f"  总响应时间: 平均 {summary['total_time']['avg_ms']:.0f}ms")
    logger.info(f"  流纯净度: {pure_count}/{len(valid_results)} ({summary['stream_purity']['pure_rate']*100:.0f}%)")
    logger.info(f"{'='*60}")

    # 保存报告
    report = {"summary": summary, "details": results}
    output_path = Path(__file__).parent / "sse_eval_report.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"评估报告已保存: {output_path}")


if __name__ == "__main__":
    main()
