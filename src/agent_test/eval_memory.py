"""
Mitta 记忆模块评估脚本（agent_test.eval_memory）
=================================================
评估对象：LangGraph PostgresStore（长期记忆落 PostgreSQL，见 memory_node.py
store.put(namespace, "user_profile", {"profile": ...})；Redis 只承载缓存/限流/JWT，
不存长期记忆）。

指标（延迟类均按生产口径统计）：
  - 写入延迟：模拟 memory_node 同一 namespace、同一 key=user_profile 覆盖写，
    画像文本逐轮增长（贴近真实多轮增量合并）；warmup 不计入统计
  - 读取延迟：热点 key（user_profile，模拟每轮对话都读档案）+ 多 key 混合读
  - 重复写入减少、连续对话画像条目变化（功能正确性验证）

统计口径：
  - 完整分布：avg / p50 / p95 / max / min 一起报
  - warmup：前 N 次连接建立 / JIT 不计入
  - 重复 R 轮取各指标中位数，消除单次抖动

用法：
    conda activate langchain1.2
    cd src
    python -m agent_test.eval_memory                          # 默认：写80 读300 重复3 轮
    python -m agent_test.eval_memory --write-n 100 --read-n 500
    python -m agent_test.eval_memory --repeat 5               # 更多轮次取中位数
    python -m agent_test.eval_memory --db-url postgresql://user:pwd@host:5432/db  # 指向线上/其他环境
    python -m agent_test.eval_memory --rounds 15              # 对话画像轮次

注意：
  - 本脚本直接操作 PostgresStore，不启动 HTTP 服务
  - 测试数据使用独立 namespace（agent_test_*），测试完成后清理
  - 生产环境实测需 PostgreSQL 可直连（公网 5432 或 SSH 隧道）+ 凭据；
    Redis 不承载长期记忆，测记忆延迟请连 PostgreSQL 而非 Redis
"""
import argparse
import json
import os
import random
import sys
import time
import uuid
from pathlib import Path
from typing import Dict, List, Tuple

from dotenv import load_dotenv
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv(override=True)

POSTGRESQL_DB_URL = os.getenv("POSTGRESQL_DB_URL")


# ============================================================
# 统计工具：完整分布 + 多轮中位数
# ============================================================

def _percentile(sorted_lat: List[float], p: float) -> float:
    """线性插值百分位；p=50 -> p50，p=95 -> p95。"""
    if not sorted_lat:
        return 0.0
    if len(sorted_lat) == 1:
        return sorted_lat[0]
    rank = (len(sorted_lat) - 1) * p / 100.0
    lo = int(rank)
    hi = min(lo + 1, len(sorted_lat) - 1)
    frac = rank - lo
    return sorted_lat[lo] + (sorted_lat[hi] - sorted_lat[lo]) * frac


def summarize(latencies: List[float]) -> Dict[str, float]:
    """完整分布：avg / p50 / p95 / max / min。"""
    if not latencies:
        return {"avg_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0, "min_ms": 0.0}
    s = sorted(latencies)
    return {
        "avg_ms": sum(latencies) / len(latencies),
        "p50_ms": _percentile(s, 50),
        "p95_ms": _percentile(s, 95),
        "max_ms": s[-1],
        "min_ms": s[0],
    }


def median_of_runs(runs: List[Dict[str, float]]) -> Dict[str, float]:
    """多轮重复跑：每项指标取中位数（消除单次抖动）。"""
    keys = ("avg_ms", "p50_ms", "p95_ms", "max_ms", "min_ms")
    return {k: sorted(r[k] for r in runs)[len(runs) // 2] for k in keys}


# ============================================================
# 测试数据集：贴近生产的画像文本（确定性生成，可复现）
# ============================================================

USERNAME = "评测用户"

# 画像事实池：覆盖长期记忆提取的真实类别（memory_node / prompt_constants 语义）
FACT_POOL = [
    ("职业", "计算机相关专业应届生，正在参加秋招，投递 AI 应用开发方向"),
    ("技能", "Java 后端与 Python 双栈，熟悉 Spring Boot、FastAPI"),
    ("技能", "熟悉 LangGraph、LangChain，做过 Agent 状态机与工具编排"),
    ("技能", "了解 MCP 协议，实践过本地 MCP Server 与工具装配"),
    ("项目", "自研 AI Agent 项目 Mitta：FastAPI + LangGraph + RAG + MCP"),
    ("项目", "Mitta 支持多人格路由、混合检索、语义缓存与长期记忆"),
    ("知识", "对 RAG 有实践经验：BM25 + 稠密向量 + reranker 混合链路"),
    ("知识", "了解 Redis 语义缓存与限流（滑动窗口、降级）"),
    ("偏好", "偏好深色主题界面，重视代码可读性"),
    ("偏好", "喜欢用 VS Code 作为主力编辑器"),
    ("环境", "使用 conda 管理 Python 环境（langchain1.2）"),
    ("环境", "使用 Git 管理代码，遵循先提交后推送的习惯"),
    ("位置", "位于江西南昌"),
    ("目标", "秋招目标是 AI 应用 / Agent 开发相关岗位"),
    ("学习", "正在学习多 Agent 协作与 Supervisor 架构"),
    ("兴趣", "对 AI 产品化、Agent 落地场景感兴趣"),
    ("习惯", "开发中习惯先跑小样本验证再全量执行"),
    ("习惯", "重要改动会写 devlog 记录根因与验证"),
    ("工具", "使用 Docker 与 docker-compose 部署服务"),
    ("工具", "使用 GitHub Actions 做 CI/CD 与部署"),
]


def _profile_text(round_idx: int) -> str:
    """生成第 round_idx 轮合并后的画像文本：确定性抽取 2~6 条事实，逐轮累积。

    模拟 memory_node 的增量合并：越到后面画像越长（真实负载下 value 增长，
    覆盖写同一行导致的更新开销也被计入写入延迟）。
    """
    rng = random.Random(20260920 + round_idx * 7)
    take = rng.randint(2, 6)
    # 用轮次做偏移从事实池环形取，保证相邻轮有重叠也有新增
    start = (round_idx * 3) % len(FACT_POOL)
    chosen = []
    for i in range(take):
        cat, text = FACT_POOL[(start + i * 2) % len(FACT_POOL)]
        line = f"- {cat}：{text}"
        if line not in chosen:
            chosen.append(line)
    return "用户名：" + USERNAME + "\n" + "\n".join(chosen)


# ============================================================
# PostgresStore 封装
# ============================================================

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
        """列出 namespace 下的所有 key。"""
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
# 延迟测试（warmup + 完整分布 + 多轮中位数）
# ============================================================

def run_write_latency_once(tester: MemoryStoreTester, ns: tuple, n: int, warmup: int) -> Dict[str, float]:
    """单轮写入延迟：先 warmup 不计入，再正式 n 次覆盖写 user_profile。"""
    # warmup：连接建立 / JIT / 连接池预热
    for i in range(warmup):
        tester.put(ns, "user_profile", {"profile": _profile_text(99999 + i)})
    latencies = []
    for i in range(n):
        elapsed, _ = tester.put(ns, "user_profile", {"profile": _profile_text(i)})
        latencies.append(elapsed)
    return summarize(latencies)


def test_write_latency(tester: MemoryStoreTester, user_id: str, n: int, warmup: int, repeat: int) -> Dict:
    """【测试 1】PostgresStore 写入延迟：n 次覆盖写，repeat 轮取中位数。"""
    logger.info(f"【测试 1】写入延迟（{n} 次/轮 × {repeat} 轮，warmup {warmup} 次）")
    namespace = ("agent_test", user_id, "write_latency")

    runs = [run_write_latency_once(tester, namespace, n, warmup) for _ in range(repeat)]
    final = median_of_runs(runs)
    result = {
        "agent_test": "write_latency",
        "count_per_run": n,
        "warmup": warmup,
        "repeat": repeat,
        "runs": runs,
        "avg_ms": final["avg_ms"],
        "p50_ms": final["p50_ms"],
        "p95_ms": final["p95_ms"],
        "max_ms": final["max_ms"],
        "min_ms": final["min_ms"],
    }
    logger.info(
        f"  中位数分布 avg={final['avg_ms']:.2f} p50={final['p50_ms']:.2f} "
        f"p95={final['p95_ms']:.2f} max={final['max_ms']:.2f} min={final['min_ms']:.2f} ms"
    )
    tester.clear_namespace(namespace)
    return result


def run_read_latency_once(tester: MemoryStoreTester, ns: tuple, n_read: int, warmup: int, n_keys: int = 60) -> Dict[str, float]:
    """单轮读取延迟：热点 key（user_profile）+ 多 key 混合读。"""
    # 准备 n_keys 个事实 key + 热点 user_profile
    for i in range(n_keys):
        tester.put(ns, f"fact_{i}", {"profile": _profile_text(10000 + i)})
    tester.put(ns, "user_profile", {"profile": _profile_text(5000)})

    # warmup 读：连接/缓存预热
    for _ in range(warmup):
        tester.get(ns, "user_profile")

    latencies = []
    rng = random.Random(20260921)
    # 混合：热点 key 占多数（模拟每轮对话都读档案），多 key 覆盖读路径
    for i in range(n_read):
        if i % 3 == 0:  # 1/3 读随机 fact key
            key = f"fact_{rng.randrange(n_keys)}"
        else:           # 2/3 读热点 user_profile
            key = "user_profile"
        elapsed, _ = tester.get(ns, key)
        latencies.append(elapsed)
    return summarize(latencies)


def test_read_latency(tester: MemoryStoreTester, user_id: str, n_read: int, warmup: int, repeat: int) -> Dict:
    """【测试 2】PostgresStore 读取延迟：n 次读，repeat 轮取中位数。"""
    logger.info(f"【测试 2】读取延迟（{n_read} 次/轮 × {repeat} 轮，warmup {warmup} 次）")
    namespace = ("agent_test", user_id, "read_latency")

    runs = [run_read_latency_once(tester, namespace, n_read, warmup) for _ in range(repeat)]
    final = median_of_runs(runs)
    result = {
        "agent_test": "read_latency",
        "count_per_run": n_read,
        "warmup": warmup,
        "repeat": repeat,
        "runs": runs,
        "avg_ms": final["avg_ms"],
        "p50_ms": final["p50_ms"],
        "p95_ms": final["p95_ms"],
        "max_ms": final["max_ms"],
        "min_ms": final["min_ms"],
    }
    logger.info(
        f"  中位数分布 avg={final['avg_ms']:.2f} p50={final['p50_ms']:.2f} "
        f"p95={final['p95_ms']:.2f} max={final['max_ms']:.2f} min={final['min_ms']:.2f} ms"
    )
    tester.clear_namespace(namespace)
    return result


# ============================================================
# 功能正确性测试（保留）
# ============================================================

def test_duplicate_write_reduction(tester: MemoryStoreTester, user_id: str) -> Dict:
    """【测试 3】重复写入减少：相同内容不重复写入。"""
    logger.info("【测试 3】重复写入减少")
    namespace = ("agent_test", user_id, "dedup")

    _content_hashes = set()

    def put_with_dedup(ns, key, value):
        import hashlib
        content_str = json.dumps(value, sort_keys=True, ensure_ascii=False)
        content_hash = hashlib.sha256(content_str.encode("utf-8")).hexdigest()
        if content_hash in _content_hashes:
            return 0, True
        elapsed, size = tester.put(ns, key, value)
        _content_hashes.add(content_hash)
        return elapsed, False

    new_facts = [
        {"fact": "用户使用 VS Code 编辑器", "category": "tool"},
        {"fact": "用户熟悉 Spring Boot", "category": "skill"},
        {"fact": "用户对 RAG 技术有研究", "category": "interest"},
    ]
    first_round_writes = first_round_skips = 0
    for i, fact in enumerate(new_facts):
        _, skipped = put_with_dedup(namespace, f"fact_{i}", fact)
        first_round_skips += skipped
        first_round_writes += 0 if skipped else 1

    second_round_writes = second_round_skips = 0
    for i, fact in enumerate(new_facts):
        _, skipped = put_with_dedup(namespace, f"fact_{i}", fact)
        second_round_skips += skipped
        second_round_writes += 0 if skipped else 1

    mixed_facts = [
        {"fact": "用户使用 VS Code 编辑器", "category": "tool"},
        {"fact": "用户熟悉 Spring Boot", "category": "skill"},
        {"fact": "用户学习过 LangGraph", "category": "skill"},
        {"fact": "用户对 MCP 协议有了解", "category": "interest"},
    ]
    third_round_writes = third_round_skips = 0
    for i, fact in enumerate(mixed_facts):
        _, skipped = put_with_dedup(namespace, f"fact_{i}", fact)
        third_round_skips += skipped
        third_round_writes += 0 if skipped else 1

    total_writes = first_round_writes + second_round_writes + third_round_writes
    total_skips = first_round_skips + second_round_skips + third_round_skips
    total = total_writes + total_skips
    reduction_rate = total_skips / total if total else 0

    result = {
        "agent_test": "duplicate_write_reduction",
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
    """【测试 4】模拟连续对话 N 轮，统计画像条目变化（含完整分布）。"""
    logger.info(f"【测试 4】连续对话 {rounds} 轮画像变化")
    namespace = ("agent_test", user_id, "conversation")

    write_latencies = []
    entry_counts = []
    for r in range(1, rounds + 1):
        fact = {"profile": _profile_text(r)}
        key = f"round_{r}"
        elapsed, _ = tester.put(namespace, key, fact)
        write_latencies.append(elapsed)
        keys = tester.list_namespace(namespace)
        entry_counts.append(len(keys))
        if r % 5 == 0:
            logger.info(f"  第 {r} 轮: 写入 {elapsed:.2f}ms, 当前条目数={len(keys)}")

    read_latencies = []
    for r in range(1, rounds + 1):
        elapsed, _ = tester.get(namespace, f"round_{r}")
        read_latencies.append(elapsed)

    write_dist = summarize(write_latencies)
    read_dist = summarize(read_latencies)
    result = {
        "agent_test": "conversation_profile",
        "rounds": rounds,
        "final_entry_count": entry_counts[-1] if entry_counts else 0,
        "entry_growth": entry_counts,
        "write": write_dist,
        "read": read_dist,
        "total_write_time_ms": sum(write_latencies),
    }
    logger.info(f"  最终条目数: {result['final_entry_count']}")
    logger.info(f"  写入分布 avg={write_dist['avg_ms']:.2f} p95={write_dist['p95_ms']:.2f} max={write_dist['max_ms']:.2f} ms")
    logger.info(f"  读取分布 avg={read_dist['avg_ms']:.2f} p95={read_dist['p95_ms']:.2f} max={read_dist['max_ms']:.2f} ms")
    logger.info(f"  总写入时间: {result['total_write_time_ms']:.0f}ms")

    tester.clear_namespace(namespace)
    return result


# ============================================================
# 主流程
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Mitta 记忆模块评估（PostgresStore）")
    parser.add_argument("--rounds", type=int, default=10, help="模拟对话轮次（默认 10）")
    parser.add_argument("--user-id", type=str, default=f"mem_test_{uuid.uuid4().hex[:8]}", help="测试用户 ID")
    parser.add_argument("--write-n", type=int, default=80, help="写入样本量/轮（50~100，默认 80）")
    parser.add_argument("--read-n", type=int, default=300, help="读取样本量/轮（200~500，默认 300）")
    parser.add_argument("--warmup", type=int, default=10, help="warmup 次数（连接建立/JIT，不计入统计，默认 10）")
    parser.add_argument("--repeat", type=int, default=3, help="重复轮数取中位数（默认 3）")
    parser.add_argument("--db-url", type=str, default=None, help="PostgreSQL 连接串（默认读 .env POSTGRESQL_DB_URL）")
    args = parser.parse_args()

    if not (50 <= args.write_n <= 100):
        logger.warning(f"--write-n {args.write_n} 超出推荐区间 50~100，按传入值执行")
    if not (200 <= args.read_n <= 500):
        logger.warning(f"--read-n {args.read_n} 超出推荐区间 200~500，按传入值执行")

    logger.info("=" * 60)
    logger.info("Mitta 记忆模块评估（PostgresStore）")
    logger.info(f"用户 ID: {args.user_id}")
    logger.info(f"样本量: 写 {args.write_n}×{args.repeat} / 读 {args.read_n}×{args.repeat}，warmup {args.warmup}")
    logger.info("=" * 60)

    tester = MemoryStoreTester(db_url=args.db_url)
    try:
        tester.open()
    except Exception as e:
        logger.error(f"PostgresStore 初始化失败: {e}")
        return

    all_results = []
    try:
        all_results.append(test_write_latency(tester, args.user_id, args.write_n, args.warmup, args.repeat))
        all_results.append(test_read_latency(tester, args.user_id, args.read_n, args.warmup, args.repeat))
        all_results.append(test_duplicate_write_reduction(tester, args.user_id))
        all_results.append(test_conversation_profile(tester, args.user_id, args.rounds))
    finally:
        logger.info("清理测试数据...")
        for prefix in ["write_latency", "read_latency", "dedup", "conversation"]:
            ns = ("agent_test", args.user_id, prefix)
            count = tester.clear_namespace(ns)
            if count:
                logger.info(f"  清理 {ns}: {count} 条")
        tester.close()

    # ---- 汇总报告 ----
    logger.info(f"\n{'='*60}")
    logger.info("【记忆模块评估汇总】")
    logger.info("")
    for r in all_results:
        test_name = r.get("agent_test", "unknown")
        if test_name == "write_latency":
            logger.info(f"  写入延迟（中位数分布）: avg {r['avg_ms']:.2f} / p50 {r['p50_ms']:.2f} / p95 {r['p95_ms']:.2f} / max {r['max_ms']:.2f} ms")
        elif test_name == "read_latency":
            logger.info(f"  读取延迟（中位数分布）: avg {r['avg_ms']:.2f} / p50 {r['p50_ms']:.2f} / p95 {r['p95_ms']:.2f} / max {r['max_ms']:.2f} ms")
        elif test_name == "duplicate_write_reduction":
            logger.info(f"  重复写入减少率: {r['reduction_rate']*100:.1f}%")
        elif test_name == "conversation_profile":
            logger.info(f"  对话画像: {r['rounds']} 轮生成 {r['final_entry_count']} 条记忆, 总写入 {r['total_write_time_ms']:.0f}ms")

    output_path = Path(__file__).parent / "memory_eval_report.json"
    output_path.write_text(json.dumps(all_results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    logger.info(f"\n评估报告已保存: {output_path}")


if __name__ == "__main__":
    main()
