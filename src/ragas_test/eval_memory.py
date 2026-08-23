"""
Mitta 记忆模块评估脚本
========================
评估指标：
  - PostgresStore 写入延迟
  - 重复写入减少（相同内容不重复写入）
  - 连续对话 10 轮的画像条目变化
  - 记忆读取延迟

用法：
    conda activate langchain1.2
    cd src
    python -m ragas_test.test_memory                  # 默认测试
    python -m ragas_test.test_memory --rounds 10      # 对话轮次
    python -m ragas_test.test_memory --user-id test_user  # 测试用户 ID

注意：
  - 本脚本直接操作 PostgresStore，不启动 HTTP 服务
  - 测试数据使用独立 namespace，测试完成后清理
"""
import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import List, Dict, Tuple
from dotenv import load_dotenv
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


load_dotenv(override=True)
# ============================================================
# PostgresStore 封装
# ============================================================
POSTGRESQL_DB_URL = os.getenv("POSTGRESQL_DB_URL")
class MemoryStoreTester:
    """PostgresStore 测试封装。"""

    def __init__(self, db_url: str = None):
        self.db_url = db_url or POSTGRESQL_DB_URL
        self.store = None
        self.pool = None

    def open(self):
        from psycopg_pool import ConnectionPool
        from langgraph.store.postgres import PostgresStore

        self.pool = ConnectionPool(
            conninfo=self.db_url,
            min_size=1,
            max_size=5,
            kwargs={"autocommit": True},
        )
        self.store = PostgresStore(self.pool)
        self.store.setup()
        logger.success("PostgresStore 就绪")

    def close(self):
        if self.pool:
            self.pool.close()
            logger.info("PostgresStore 已关闭")

    def put(self, namespace: tuple, key: str, value: dict) -> Tuple[float, int]:
        """写入一条记忆，返回 (延迟ms, 写入字节数)。"""
        t0 = time.perf_counter()
        self.store.put(namespace, key, value)
        elapsed = (time.perf_counter() - t0) * 1000
        size = len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
        return elapsed, size

    def get(self, namespace: tuple, key: str) -> Tuple[float, object]:
        """读取一条记忆，返回 (延迟ms, 值)。"""
        t0 = time.perf_counter()
        item = self.store.get(namespace, key)
        elapsed = (time.perf_counter() - t0) * 1000
        return elapsed, item

    def search(self, namespace: tuple, query: str, limit: int = 10) -> Tuple[float, list]:
        """搜索记忆，返回 (延迟ms, 结果列表)。"""
        t0 = time.perf_counter()
        results = list(self.store.search(namespace, query, limit=limit))
        elapsed = (time.perf_counter() - t0) * 1000
        return elapsed, results

    def list_namespace(self, namespace: tuple) -> list:
        """列出 namespace 下的所有 key。

        PostgresStore 表结构：prefix(text, namespace用点连接) / key(text) / value(jsonb)
        """
        try:
            ns_str = ".".join(str(p) for p in namespace)
            with self.pool.connection() as conn:
                cur = conn.execute(
                    "SELECT key FROM store WHERE prefix = %s", (ns_str,)
                )
                return [row[0] for row in cur.fetchall()]
        except Exception as e:
            logger.warning(f"list_namespace 失败: {e}")
            return []

    def delete(self, namespace: tuple, key: str):
        """删除一条记忆。"""
        self.store.delete(namespace, key)

    def clear_namespace(self, namespace: tuple):
        """清空整个 namespace（测试用）。"""
        keys = self.list_namespace(namespace)
        for k in keys:
            try:
                self.delete(namespace, k)
            except Exception:
                pass
        return len(keys)


# ============================================================
# 测试用例
# ============================================================

def test_write_latency(tester: MemoryStoreTester, user_id: str) -> Dict:
    """测试 PostgresStore 写入延迟。"""
    logger.info("【测试 1】PostgresStore 写入延迟")
    namespace = ("memory_test", user_id, "write_latency")

    test_data = [
        {"fact": "用户喜欢 Python 编程", "category": "preference", "confidence": 0.9},
        {"fact": "用户使用 Java 后端开发", "category": "skill", "confidence": 0.85},
        {"fact": "用户对 AI Agent 开发感兴趣", "category": "interest", "confidence": 0.95},
        {"fact": "用户使用 conda 管理 Python 环境", "category": "environment", "confidence": 0.8},
        {"fact": "用户在南昌工作", "category": "location", "confidence": 0.7},
    ]

    latencies = []
    sizes = []
    for i, data in enumerate(test_data):
        key = f"fact_{i}"
        elapsed, size = tester.put(namespace, key, data)
        latencies.append(elapsed)
        sizes.append(size)
        logger.info(f"  写入 {key}: {elapsed:.2f}ms ({size} bytes)")

    avg_latency = sum(latencies) / len(latencies)
    p95_latency = sorted(latencies)[int(len(latencies) * 0.95) - 1]
    max_latency = max(latencies)
    min_latency = min(latencies)

    result = {
        "ragas_test": "write_latency",
        "count": len(test_data),
        "avg_ms": avg_latency,
        "p95_ms": p95_latency,
        "max_ms": max_latency,
        "min_ms": min_latency,
        "avg_size_bytes": sum(sizes) / len(sizes),
    }
    logger.info(f"  平均: {avg_latency:.2f}ms, P95: {p95_latency:.2f}ms, Max: {max_latency:.2f}ms")

    # 清理
    tester.clear_namespace(namespace)
    return result


def test_read_latency(tester: MemoryStoreTester, user_id: str) -> Dict:
    """测试 PostgresStore 读取延迟。"""
    logger.info("【测试 2】PostgresStore 读取延迟")
    namespace = ("memory_test", user_id, "read_latency")

    # 先写入测试数据
    for i in range(20):
        tester.put(namespace, f"key_{i}", {"fact": f"测试事实 {i}", "index": i})

    # 测试读取
    latencies = []
    for i in range(20):
        key = f"key_{i}"
        elapsed, item = tester.get(namespace, key)
        latencies.append(elapsed)
        if i % 5 == 0:
            logger.info(f"  读取 {key}: {elapsed:.2f}ms")

    avg_latency = sum(latencies) / len(latencies)
    p95_latency = sorted(latencies)[int(len(latencies) * 0.95) - 1]

    result = {
        "ragas_test": "read_latency",
        "count": 20,
        "avg_ms": avg_latency,
        "p95_ms": p95_latency,
    }
    logger.info(f"  平均: {avg_latency:.2f}ms, P95: {p95_latency:.2f}ms")

    tester.clear_namespace(namespace)
    return result


def test_duplicate_write_reduction(tester: MemoryStoreTester, user_id: str) -> Dict:
    """测试重复写入减少：相同内容不重复写入。"""
    logger.info("【测试 3】重复写入减少")
    namespace = ("memory_test", user_id, "dedup")

    # 模拟 memory_node 的去重逻辑：按内容哈希去重，相同内容不重复写入
    _content_hashes = set()  # 已写入内容的哈希集合

    def put_with_dedup(ns, key, value):
        """带去重的写入：按内容哈希去重，相同内容不重复写入。"""
        import hashlib
        content_str = json.dumps(value, sort_keys=True, ensure_ascii=False)
        content_hash = hashlib.sha256(content_str.encode("utf-8")).hexdigest()
        if content_hash in _content_hashes:
            return 0, True  # 相同内容已存在，跳过写入
        elapsed, size = tester.put(ns, key, value)
        _content_hashes.add(content_hash)
        return elapsed, False

    # 第一轮：写入新内容
    new_facts = [
        {"fact": "用户使用 VS Code 编辑器", "category": "tool"},
        {"fact": "用户熟悉 Spring Boot", "category": "skill"},
        {"fact": "用户对 RAG 技术有研究", "category": "interest"},
    ]

    first_round_writes = 0
    first_round_skips = 0
    for i, fact in enumerate(new_facts):
        _, skipped = put_with_dedup(namespace, f"fact_{i}", fact)
        if skipped:
            first_round_skips += 1
        else:
            first_round_writes += 1

    # 第二轮：写入相同内容（应全部跳过）
    second_round_writes = 0
    second_round_skips = 0
    for i, fact in enumerate(new_facts):
        _, skipped = put_with_dedup(namespace, f"fact_{i}", fact)
        if skipped:
            second_round_skips += 1
        else:
            second_round_writes += 1

    # 第三轮：部分新内容 + 部分旧内容（用相同 key，前两个内容重复，后两个新内容）
    mixed_facts = [
        {"fact": "用户使用 VS Code 编辑器", "category": "tool"},  # 重复
        {"fact": "用户熟悉 Spring Boot", "category": "skill"},     # 重复
        {"fact": "用户学习过 LangGraph", "category": "skill"},      # 新
        {"fact": "用户对 MCP 协议有了解", "category": "interest"},  # 新
    ]
    third_round_writes = 0
    third_round_skips = 0
    for i, fact in enumerate(mixed_facts):
        _, skipped = put_with_dedup(namespace, f"fact_{i}", fact)
        if skipped:
            third_round_skips += 1
        else:
            third_round_writes += 1

    total_writes = first_round_writes + second_round_writes + third_round_writes
    total_skips = first_round_skips + second_round_skips + third_round_skips
    total = total_writes + total_skips
    reduction_rate = total_skips / total if total else 0

    result = {
        "ragas_test": "duplicate_write_reduction",
        "first_round": {"writes": first_round_writes, "skips": first_round_skips},
        "second_round": {"writes": second_round_writes, "skips": second_round_skips},
        "third_round": {"writes": third_round_writes, "skips": third_round_skips},
        "total_writes": total_writes,
        "total_skips": total_skips,
        "reduction_rate": reduction_rate,
    }
    logger.info(f"  第一轮（全新）: 写入={first_round_writes}, 跳过={first_round_skips}")
    logger.info(f"  第二轮（全重复）: 写入={second_round_writes}, 跳过={second_round_skips}")
    logger.info(f"  第三轮（混合）: 写入={third_round_writes}, 跳过={third_round_skips}")
    logger.info(f"  总写入减少率: {reduction_rate*100:.1f}%")

    tester.clear_namespace(namespace)
    return result


def test_conversation_profile(tester: MemoryStoreTester, user_id: str, rounds: int) -> Dict:
    """模拟连续对话 N 轮，统计画像条目变化。"""
    logger.info(f"【测试 4】连续对话 {rounds} 轮画像变化")
    namespace = ("memory_test", user_id, "conversation")

    # 模拟对话内容（每轮提取不同的用户信息）
    conversation_facts = [
        {"fact": "用户是 Java 后端开发者", "category": "role", "round": 1},
        {"fact": "用户使用 Spring Boot 框架", "category": "tech_stack", "round": 2},
        {"fact": "用户熟悉 MySQL 数据库", "category": "tech_stack", "round": 3},
        {"fact": "用户对 AI Agent 开发感兴趣", "category": "interest", "round": 4},
        {"fact": "用户在学习 LangGraph", "category": "learning", "round": 5},
        {"fact": "用户使用 conda 管理 Python 环境", "category": "environment", "round": 6},
        {"fact": "用户偏好深色主题", "category": "preference", "round": 7},
        {"fact": "用户在南昌工作", "category": "location", "round": 8},
        {"fact": "用户使用 VS Code 编辑器", "category": "tool", "round": 9},
        {"fact": "用户对 RAG 技术有实践经验", "category": "skill", "round": 10},
        {"fact": "用户熟悉 Redis 缓存", "category": "tech_stack", "round": 11},
        {"fact": "用户使用 Git 进行版本控制", "category": "tool", "round": 12},
        {"fact": "用户对微服务架构有了解", "category": "skill", "round": 13},
        {"fact": "用户偏好简洁的代码风格", "category": "preference", "round": 14},
        {"fact": "用户关注秋招机会", "category": "goal", "round": 15},
    ]

    write_latencies = []
    entry_counts = []

    for r in range(1, min(rounds, len(conversation_facts)) + 1):
        fact = conversation_facts[r - 1]
        key = f"round_{r}"
        elapsed, _ = tester.put(namespace, key, fact)
        write_latencies.append(elapsed)

        # 统计当前条目数
        keys = tester.list_namespace(namespace)
        entry_counts.append(len(keys))

        if r % 5 == 0:
            logger.info(f"  第 {r} 轮: 写入 {elapsed:.2f}ms, 当前条目数={len(keys)}")

    # 最终读取验证
    read_latencies = []
    for r in range(1, min(rounds, len(conversation_facts)) + 1):
        key = f"round_{r}"
        elapsed, _ = tester.get(namespace, key)
        read_latencies.append(elapsed)

    result = {
        "ragas_test": "conversation_profile",
        "rounds": min(rounds, len(conversation_facts)),
        "final_entry_count": entry_counts[-1] if entry_counts else 0,
        "entry_growth": entry_counts,
        "avg_write_ms": sum(write_latencies) / len(write_latencies) if write_latencies else 0,
        "avg_read_ms": sum(read_latencies) / len(read_latencies) if read_latencies else 0,
        "total_write_time_ms": sum(write_latencies),
    }
    logger.info(f"  最终条目数: {result['final_entry_count']}")
    logger.info(f"  平均写入: {result['avg_write_ms']:.2f}ms")
    logger.info(f"  平均读取: {result['avg_read_ms']:.2f}ms")
    logger.info(f"  总写入时间: {result['total_write_time_ms']:.0f}ms")

    tester.clear_namespace(namespace)
    return result


# ============================================================
# 主流程
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Mitta 记忆模块评估")
    parser.add_argument("--rounds", type=int, default=10, help="模拟对话轮次（默认 10）")
    parser.add_argument("--user-id", type=str, default=f"mem_test_{uuid.uuid4().hex[:8]}", help="测试用户 ID")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("Mitta 记忆模块评估")
    logger.info(f"用户 ID: {args.user_id}")
    logger.info(f"对话轮次: {args.rounds}")
    logger.info("=" * 60)

    tester = MemoryStoreTester()
    try:
        tester.open()
    except Exception as e:
        logger.error(f"PostgresStore 初始化失败: {e}")
        return

    all_results = []

    try:
        # 测试 1: 写入延迟
        all_results.append(test_write_latency(tester, args.user_id))

        # 测试 2: 读取延迟
        all_results.append(test_read_latency(tester, args.user_id))

        # 测试 3: 重复写入减少
        all_results.append(test_duplicate_write_reduction(tester, args.user_id))

        # 测试 4: 连续对话画像
        all_results.append(test_conversation_profile(tester, args.user_id, args.rounds))

    finally:
        # 清理所有测试数据
        logger.info("清理测试数据...")
        for prefix in ["write_latency", "read_latency", "dedup", "conversation"]:
            ns = ("memory_test", args.user_id, prefix)
            count = tester.clear_namespace(ns)
            if count:
                logger.info(f"  清理 {ns}: {count} 条")
        tester.close()

    # ---- 汇总报告 ----
    logger.info(f"\n{'='*60}")
    logger.info("【记忆模块评估汇总】")
    logger.info("")

    for r in all_results:
        test_name = r.get("ragas_test", "unknown")
        if test_name == "write_latency":
            logger.info(f"  写入延迟: 平均 {r['avg_ms']:.2f}ms, P95 {r['p95_ms']:.2f}ms")
        elif test_name == "read_latency":
            logger.info(f"  读取延迟: 平均 {r['avg_ms']:.2f}ms, P95 {r['p95_ms']:.2f}ms")
        elif test_name == "duplicate_write_reduction":
            logger.info(f"  重复写入减少率: {r['reduction_rate']*100:.1f}%")
        elif test_name == "conversation_profile":
            logger.info(f"  对话画像: {r['rounds']} 轮生成 {r['final_entry_count']} 条记忆, 总写入 {r['total_write_time_ms']:.0f}ms")

    # 保存报告
    output_path = Path(__file__).parent / "memory_eval_report.json"
    output_path.write_text(json.dumps(all_results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    logger.info(f"\n评估报告已保存: {output_path}")


if __name__ == "__main__":
    main()
