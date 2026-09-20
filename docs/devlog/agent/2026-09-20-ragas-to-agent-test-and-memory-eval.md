# 2026-09-20 评测目录改名 ragas_test → agent_test + eval_memory 数据集改造与公网实测

docs_sync: none

## 类别
agent（评测体系改造）

## 背景
1. 用户要求把 `src/ragas_test` 整个目录改名为 `src/agent_test`：原目录名（RAGAS 评测）已不能覆盖当前评测范围（已扩展到工具调用、缓存命中、限流拦截、JWT、SSE 等整个 Agent 系统指标）。
2. 用户要求改造 `eval_memory.py` 测试数据集：样本量写入 50~100 / 读取 200~500、warmup 前 5~10 次不计入、报完整分布（avg/p50/p95/max/min）、重复 3 轮取中位数，并直连线上生产环境实测。
3. 已查证：长期记忆实际存 **PostgreSQL（LangGraph PostgresStore）**，Redis 只承载缓存/限流/JWT；"连 Redis 测记忆"是误解，评测目标应为 PostgreSQL（本地 WSL `172.29.183.97:5432`，线上 `121.199.38.43:5432`）。

## 改动
1. **目录改名**：`git mv src/ragas_test src/agent_test`，目录内 19 个 py 全部批量替换 `ragas_test` → `agent_test`（模块自引用 `from agent_test.report_path import ...`、docstring `python -m agent_test.*`、报告字段键、CLI 参数）。全仓 `*.py` 残留 `ragas_test` 归零（含 `tests/test_config.py` 错误消息串、`resources/knowledge-base/ingest_knowledge.py` 死代码分支一并清理）。
2. **eval_memory.py 重写**（`src/agent_test/eval_memory.py`）：
   - 新增 `_percentile`（线性插值）/`summarize`（完整分布）/`median_of_runs`（3 轮取中位数）；
   - 测试 1 写入延迟：模拟 memory_node 同一 `user_profile` key 覆盖写、画像逐轮增长（FACT_POOL 20 条事实池确定性生成）；
   - 测试 2 读取延迟：热点 key（user_profile 占 2/3）+ 60 个 fact key 混合；
   - CLI：`--write-n 80 --read-n 300 --repeat 3 --warmup 10 --rounds 10 --db-url`；
   - 测试数据 namespace 统一 `("agent_test", user_id, prefix)`，跑完自动清理；
   - 报告落 `src/agent_test/memory_eval_report.json`。
3. **公网直连实测**（线上生产 PostgreSQL `121.199.38.43:5432`，compose 默认 root/1234/agentproject，用户已放行安全组）：

| 指标 | avg | p50 | p95 | max | min |
| --- | --- | --- | --- | --- | --- |
| 写入延迟（80 次×3 轮） | 23.35 ms | 23.80 ms | 28.00 ms | 30.31 ms | 19.47 ms |
| 读取延迟（300 次×3 轮） | 44.58 ms | 46.26 ms | 51.87 ms | 70.06 ms | 36.52 ms |
| 重复写入减少率 | 50.0%（第二轮全重复全部跳过） | | | | |
| 对话画像（10 轮） | 10 条记忆，总写入 212 ms，写 p95 23.79 ms / 读 p95 52.54 ms | | | | |

## 根因/结论
- 公网直连读取 p95 ≈ 52 ms（较本地 WSL 略高，主要来自公网 RTT 与连接池跨网段往返），写入 p95 ≈ 28 ms，处于合理量级；生产长期记忆读写瓶颈不在存储本身。
- 重复写入去重生效（第二轮 3 条全部跳过）。

## 验证
- `ast.parse` 全部 agent_test 脚本通过；`import agent_test.eval_memory/report_path/eval_retrieval/eval_ragas_judge` 通过；
- `pytest tests/test_config.py -q` → 32 passed；
- 公网实测完整跑通 4 项测试并落盘报告。

## 补充：生产容器内实测（2026-09-20）
- 方式：`docker exec mitta-api python -X utf8 /tmp/eval_memory.py --db-url "postgresql://root:1234@postgres:5432/agentproject?sslmode=disable"`（容器网络，生产真实链路）。
- 结果（3 轮中位数）：写入 avg 1.77 / p95 3.01 / max 3.74 ms；读取 avg 1.34 / p95 2.09 / max 7.58 ms；对话画像 10 轮总写入 22 ms。
- 结论：生产链路读写 P95 ≤ 5ms，存储无瓶颈；公网直连数字（读 p95 52ms）差距几乎全部来自公网 RTT，仅作对照，不代表生产链路。
- 报告归档：`src/agent_test/reports/2026-09-20/memory_eval_report.production.json`（生产）+ `memory_eval_report.public_net.json`（公网对照）+ `README.md`（环境标注）。

## 待办（交文档撰写 Agent）
- README / CONTRIBUTING / docs/AGENT_EVAL_MATRIX.md / docs/DEVELOPMENT_LOG.md / requirements-eval.txt 中 `ragas_test` 引用同步为 `agent_test`（文档侧未改，本次仅动代码）。
- README 中"长期记忆写入 P95 约 46 ms"旧口径替换为生产容器内实测（写 p95 3.01ms / 读 p95 2.09ms）。
