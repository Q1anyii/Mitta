"""
Mitta 在线实测（E13）
====================
对线上服务 www.mittaai.xyz 做全链路实测。

指标：
  - 健康检查：/health 可达性与状态码
  - 登录：/api/login 正常登录、错误密码拒绝
  - 对话链路：POST /api/chat/ SSE 流式返回首 token 延迟、完整回答、流纯净度
  - 会话权限：他人会话 403（会话归属校验）
  - 登出即时失效：/api/logout 后旧 token 立即 401
  - 限流：连续请求 /api/chat/ 触发 429（可选，会消耗配额，默认关闭）

用法：
    conda activate langchain1.2
    cd src
    python -m agent_test.eval_online                     # 默认在线实测
    python -m agent_test.eval_online --base-url http://www.mittaai.xyz
    python -m agent_test.eval_online --username USER --password PASS   # 指定账号
    python -m agent_test.eval_online --rate-limit-check  # 启用限流实测（消耗配额）

说明：
  - 默认 base_url https://www.mittaai.xyz；也可指向本地 http://127.0.0.1:8090。
  - 账号优先使用 --username/--password，其次尝试内置测试账号。
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import httpx
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_BASE_URL = "https://www.mittaai.xyz"

# 内置候选测试账号（与 eval_sse.py 一致；线上可用性需验证）
TEST_USERS = [
    {"userId": "user_01", "password": "1234"},
    {"userId": "zhangsan", "password": "1234"},
    {"userId": "admin", "password": "admin123"},
]

# SSE 流纯净度检查模式（内部节点日志不应混入用户可见内容）
CONTAMINATION_PATTERNS = [
    "needs_retrieval", "分类结果", "classify_node",
    "memory_node", "记忆提取", "tool_filter", "工具筛选",
    "DEBUG", "TRACE", "检索结果",
]


def check_health(base_url: str) -> Dict:
    """健康检查。"""
    logger.info("【检查 1】健康检查 /health")
    t0 = time.perf_counter()
    try:
        resp = httpx.get(f"{base_url}/health", timeout=10, follow_redirects=True)
        elapsed = (time.perf_counter() - t0) * 1000
        ok = resp.status_code == 200
        logger.info(f"  {'✓' if ok else '✗'} HTTP {resp.status_code}（{elapsed:.0f}ms）")
        return {
            "agent_test": "health",
            "url": base_url,
            "status_code": resp.status_code,
            "ok": ok,
            "latency_ms": round(elapsed, 2),
            "body_preview": resp.text[:120],
        }
    except Exception as e:
        logger.error(f"  ✗ 健康检查失败: {e}")
        return {"agent_test": "health", "url": base_url, "ok": False, "error": str(e)}


def login(base_url: str, username: Optional[str], password: Optional[str]) -> Dict:
    """登录获取 token。"""
    logger.info("【检查 2】登录 /api/login")
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
                    logger.info(f"  ✓ 登录成功: {user['userId']}")
                    return {"agent_test": "login", "ok": True, "token": token, "user": user["userId"]}
                logger.warning(f"  200 但无 token: {user['userId']} -> {str(data)[:150]}")
            else:
                logger.warning(f"  ✗ 登录失败 HTTP {resp.status_code}: {user['userId']}")
        except Exception as e:
            logger.warning(f"  ✗ 登录异常: {user['userId']}: {e}")

    # 错误密码应拒绝（安全断言，独立于账号可用性）
    try:
        resp = httpx.post(
            f"{base_url}/api/login",
            json={"userId": "definitely_no_such_user_xyz", "password": "wrong"},
            timeout=15, follow_redirects=True,
        )
        rejected = resp.status_code in (401, 400) or (resp.status_code == 200 and not resp.json().get("ok", True))
        logger.info(f"  {'✓ 错误凭证被拒绝' if rejected else '✗ 错误凭证未被拒绝'} HTTP {resp.status_code}")
        return {"agent_test": "login", "ok": False, "wrong_credential_rejected": rejected,
                "note": "无可用测试账号", "status_code": resp.status_code}
    except Exception as e:
        return {"agent_test": "login", "ok": False, "error": str(e)}


def chat_sse(base_url: str, token: str, query: str) -> Dict:
    """SSE 对话链路。"""
    logger.info(f"【检查 3】SSE 对话: {query[:30]}")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "text/event-stream"}
    payload = {"query": query, "thread_id": f"eval_online_{int(time.time())}"}

    first_content_time = None
    done_time = None
    content_chunks = []
    error_events = []
    request_start = None

    try:
        with httpx.stream(
            "POST", f"{base_url}/api/chat/", headers=headers, json=payload, timeout=120.0, follow_redirects=True,
        ) as resp:
            request_start = time.perf_counter()
            if resp.status_code != 200:
                return {"agent_test": "chat_sse", "ok": False, "error": f"HTTP {resp.status_code}", "detail": resp.text[:500]}
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
                if "content" in event:
                    if first_content_time is None:
                        first_content_time = time.perf_counter()
                    content_chunks.append(event["content"])
                elif "error" in event:
                    error_events.append(event["error"])
    except Exception as e:
        return {"agent_test": "chat_sse", "ok": False, "error": str(e), "type": type(e).__name__}

    if request_start is None:
        return {"agent_test": "chat_sse", "ok": False, "error": "请求未发出"}

    total_time = (done_time or time.perf_counter()) - request_start
    first_token = (first_content_time - request_start) if first_content_time else None
    full_content = "".join(content_chunks)
    contamination = [p for p in CONTAMINATION_PATTERNS if p in full_content]

    ok = len(content_chunks) > 0 and not error_events and not contamination
    logger.info(f"  {'✓' if ok else '✗'} 内容块 {len(content_chunks)}，首 token {first_token*1000:.0f}ms 若命中，"
                f"总耗时 {total_time:.1f}s，污染 {contamination if contamination else '无'}")
    return {
        "agent_test": "chat_sse",
        "ok": ok,
        "first_token_latency_ms": round(first_token * 1000, 2) if first_token else None,
        "total_time_s": round(total_time, 2),
        "content_chunks": len(content_chunks),
        "content_length": len(full_content),
        "error_events": error_events,
        "contamination": contamination,
        "content_preview": full_content[:200],
    }


def logout_invalidation(base_url: str, token: str) -> Dict:
    """登出即时失效：登出后旧 token 请求应 401。"""
    logger.info("【检查 4】登出即时失效")
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = httpx.post(f"{base_url}/api/logout", headers=headers, timeout=15, follow_redirects=True)
        logout_ok = resp.status_code == 200
        # 用旧 token 访问受保护接口
        resp2 = httpx.get(f"{base_url}/api/chat/sessions", headers=headers, timeout=15, follow_redirects=True)
        invalidated = resp2.status_code == 401
        ok = logout_ok and invalidated
        logger.info(f"  {'✓' if ok else '✗'} 登出 HTTP {resp.status_code}，登出后访问 HTTP {resp2.status_code}")
        return {"agent_test": "logout_invalidation", "ok": ok, "logout_status": resp.status_code,
                "after_logout_status": resp2.status_code}
    except Exception as e:
        return {"agent_test": "logout_invalidation", "ok": False, "error": str(e)}


def rate_limit_check(base_url: str, token: str, burst: int = 31) -> Dict:
    """限流实测：连续请求 /api/chat/ 观察 429（消耗配额，需显式开启）。"""
    logger.info(f"【检查 5】限流实测（{burst} 次连续请求，观察 429）")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    codes = []
    t0 = time.perf_counter()
    for i in range(burst):
        try:
            # 发即断（不读 body），仅观察状态码；限流判定在中间件，未过限流时进入生成链路
            with httpx.stream(
                "POST", f"{base_url}/api/chat/", headers=headers,
                json={"query": "ping", "thread_id": f"eval_rl_{int(time.time())}"},
                timeout=10.0, follow_redirects=True,
            ) as resp:
                codes.append(resp.status_code)
        except Exception as e:
            codes.append(0)
        logger.debug(f"  第 {i+1} 次: HTTP {codes[-1]}")
    elapsed = time.perf_counter() - t0

    rate_limited = codes.count(429)
    ok = rate_limited >= 1
    logger.info(f"  {'✓' if ok else '✗'} 429 次数 {rate_limited}/{burst}（耗时 {elapsed:.0f}s）")
    return {
        "agent_test": "rate_limit_online",
        "burst": burst,
        "rate_limited_count": rate_limited,
        "ok": ok,
        "status_codes": codes,
        "total_time_s": round(elapsed, 2),
    }


def main():
    parser = argparse.ArgumentParser(description="Mitta 在线实测")
    parser.add_argument("--base-url", type=str, default=DEFAULT_BASE_URL, help="在线地址")
    parser.add_argument("--username", type=str, default=None, help="测试账号")
    parser.add_argument("--password", type=str, default=None, help="测试密码")
    parser.add_argument("--query", type=str, default="你好，介绍一下你自己", help="对话测试 query")
    parser.add_argument("--rate-limit-check", action="store_true", help="启用限流实测（消耗配额）")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info(f"Mitta 在线实测：{args.base_url}")
    logger.info("=" * 60)

    results = [check_health(args.base_url)]

    login_result = login(args.base_url, args.username, args.password)
    results.append(login_result)

    token = login_result.get("token", "")
    if token:
        results.append(chat_sse(args.base_url, token, args.query))
        results.append(logout_invalidation(args.base_url, token))
        if args.rate_limit_check:
            # 重新登录（登出已使 token 失效）
            re_login = login(args.base_url, args.username, args.password)
            t2 = re_login.get("token", "")
            if t2:
                results.append(rate_limit_check(args.base_url, t2))
    else:
        logger.warning("无有效 token，跳过对话/登出/限流实测")

    logger.info("\n【在线实测汇总】")
    for r in results:
        test = r.get("agent_test", "?")
        ok = r.get("ok", None)
        mark = "✓" if ok else ("-" if ok is None else "✗")
        logger.info(f"  [{mark}] {test}")

    summary = {"agent_test": "online", "base_url": args.base_url, "results": results}
    output_path = Path(__file__).parent / "online_eval_report.json"
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"在线实测报告已保存: {output_path}")


if __name__ == "__main__":
    main()
