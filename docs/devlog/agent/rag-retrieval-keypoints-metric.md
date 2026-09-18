# 检索评测口径修正：key_points 关键事实点覆盖（H-20260918-02）

docs_sync: none

日期：2026-09-19
类别：agent / rag-eval

## 背景

用户对检索评测结果"这个结果也很差吧"提出质疑后，经诊断确认根因不在检索链路，而在评测口径与评测集设计。handoff H-20260918-02 要求：诊断失败 case + 引入 key_points（关键事实点）口径试点 + 新旧对照重跑。

## 诊断结论（关键证据）

1. **评测集与知识库文本同一性为零**：生产向量库 405 chunks 中 `test-qa/*` 来源为 **0**；评测集 45 条题目全部来自 `test-qa/01~04.md`（从未入库）。基础概念 10 条 ground_truth 长句在生产库中找到原句的仅结构性片段（表格头/代码块），核心事实句 95% 找不到。
2. **链路本身正常**：`--diagnose` 输出显示失败 case 的 top5 全部是主题相关文档（如 TypedDict/BaseModel 题召回 `TypedDict 用于结构化字典`、`class RAGState(TypedDict)`、`Pydantic BaseModel 用于数据校验`，rerank score 0.995/0.828）。丢分在"字面覆盖标准答案"，不在"没找到相关文档"。
3. **旧口径缺陷**：boolean 句级覆盖 / coverage 关键词覆盖率都依赖"答案文本在文档中字面出现"，而答案是从生产文档改写组织而来——字面匹配天然偏低（45 条 median 恒 0，无区分度）。

## 改动

1. `resources/knowledge-base/test-qa/eval_dataset.json`：基础概念 10 条新增 `key_points` 数组（3~4 个事实点/条），事实点取自 ground_truth 核心事实，并**逐一在生产 405 chunks 验证存在原句**（如"认证、校验、分页等逻辑抽成可复用组件"、"PostgresSaver"、"不需要归一化"）。原 `ground_truth` 字段保留；改前已备份 `eval_dataset.v1.json`。
2. `src/ragas_test/eval_retrieval.py`：
   - 新增 `evaluate_key_points()`：任一点在 top5 文档文本中出现即命中，返回 (hit, total)。
   - 新增 `--diagnose` 模式 + `diagnose_retrieve()`：对布尔=0 或 key_points 未全中的 query，输出 dense 候选池 → RRF → rerank → filter 逐级明细，判定相关文档在哪一级丢失。
   - 报告新增 `key_points` 段（avg_point_coverage / query_full_hit_ratio）+ config 记 `deprecated_metric_note`。
   - 顺手修复 main() 中重复两遍的 Redis 注入段（历史遗留）。
3. 产出 `diagnose_report.md`：诊断结论 + 失败 case 候选池明细。

## 实测结果（2026-09-19，45 条全量）

| 指标 | boolean 旧口径（45 条） | key_points 新口径（基础概念 10 条） |
|---|---|---|
| 单路 avg | 0.3111 | **0.7583** |
| 混合 avg | 0.2667 | **0.7833** |
| 混合 vs 单路 | 反而更低 | 混合略优（0.7833 > 0.7583，符合预期） |
| 全中比例 | — | 单路 0.5 / 混合 0.6 |

生产检索参数（filter 0.25 / n_results 20 / rerank top_n 5）**未改动**（本 handoff 只改评测层）。

## 遗留与建议

- key_points 全中率仅 0.5~0.6：即使事实点在知识库，每条仍有 1~2 点未被 top5 覆盖（rerank 只留 5 + filter 0.25），属真实召回损失，可另行立项优化（放宽 filter / 提高 rerank top_n）。
- 评测集约 70% 为通用技术知识题（TypedDict/JWT/SSE…），知识库不收录；建议后续以知识库真实内容重建评测集（需用户/对接专员决策，另立 handoff）。
