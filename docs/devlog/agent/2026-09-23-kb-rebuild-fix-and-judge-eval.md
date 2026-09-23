---
docs_sync: required
---

# 知识库重建事故修复 + LLM Judge 全量评测（base_id 冲突 / 评测集改造 / 增量入库）

## 背景

上一轮已完成三件事：① 探针评测集改造成 LLM-as-Judge 格式（28 条）；② 增量入库去 hash（只重建改过的文件）；③ 补 4 个知识缺口（Send 扇出 / AI Agent 目录设计 / 上下文窗口管理 / 用户表分表动机）。

随后全量重建后跑评测，发现异常：补了 Send 扇出内容后该题 answer_correctness 反而 0.20→0.00（context_recall 0.15→0.00），判定是重建事故而非评测波动。

## 事故定位（根因）

`ingest_knowledge.py` 的 `get_category` 判断 `"agent_test-qa" in file_path.parts`，但实际子目录名是 `test-qa`——**判断永远不命中**。导致：

1. `test-qa/` 下 4 个 md（01-basic-concepts / 02-code-debugging / 03-architecture-design / 04-badcase-tricky）走根目录分支，base_id 取 `01/02/03/04`；
2. 与根目录 01-python / 02-fastapi / 03-langgraph / 04-rag **同 base_id 冲突**；
3. 新增的 `_clear_old_chunks` 按 `{base_id}_` 前缀清旧时，排在后处理的 test-qa 文件把根目录 01-04 刚入库的 chunk 全删了。

## 改动（H-20260923-01）

`resources/knowledge-base/ingest_knowledge.py`：

- `get_category` 目录判断 `"agent_test-qa"` → `"test-qa"`；
- test-qa 文件 base_id 加前缀：`01` → `qa01`、`02` → `qa02`、`03` → `qa03`、`04` → `qa04`，与根目录彻底分离。

BACKLOG 新增 `#20`（P0 已修复）：记录该事故的完整定位过程。

## 修复后重建验证

- 集合总数 359（01-20 + README + qa01-04 全在，根目录 01-04 恢复，test-qa 独立前缀）；
- `03_` 前缀 17 条，含 Send 扇出 4 个 chunk（`03_003/004/015/016`）——修复前 Send 内容被删，现在确认在库；
- `qa03_` 前缀 19 条，与根目录 03 无冲突。

## 全量评测结果（28 条，LLM-as-Judge）

| 指标 | 基线（重建前） | 修复后 | 变化 |
|---|---|---|---|
| context_precision | 0.6304 | 0.6593 | +0.029 |
| context_recall | 0.6154 | **0.7432** | **+0.128** |
| faithfulness | 0.9411 | 0.9464 | +0.005 |
| answer_relevancy | 0.9875 | 0.9929 | +0.005 |
| answer_correctness | 0.6518 | **0.7575** | **+0.106** |

### 4 个知识缺口全部修复

| 题目 | ac 变化 | 说明 |
|---|---|---|
| LangGraph Send 扇出 | 0.20 → 1.00 | cr 0.15→1.00 |
| 上下文管理 | 0.35 → 1.00 | cr 0.35→1.00 |
| 用户表分表设计 | 0.40 → 1.00 | cr 0.35→1.00 |
| bcrypt 密码哈希 | 0.35 → 0.95 | 旧 passlib 污染清除 |
| AI Agent 目录结构 | 0.33 → 0.89 | cr 0.33→0.89 |

### 遗留观察（非阻塞）

- FastAPI 中间件限流 ac 0.60→0.30：cr 仅 0.35，检索上下文仍偏弱，待后续调 filter 阈值/重排时观察；
- RAG 文档切分策略 ac 0.50 持平，cp 0.35→0.25 略降，同属检索精度问题。

## 数据文件

- 基线（重建前）：`src/agent_test/ragas_judge_probe_28.json`
- 脏库数据（作废）：`src/agent_test/ragas_judge_probe_after_rebuild.json`（base_id 冲突污染，Send 归零即此份）
- 最终可信数据：`src/agent_test/ragas_judge_probe_after_fix.json`

## 结论

重建事故已修复并验证；评测数据在修复后全面好转，ac +0.106、recall +0.128，4 个知识缺口全部补齐。这是当前知识库质量的可信基线。
