# 双轨制 B：项目专属评测集 eval_project_dataset（H-20260919-02）

docs_sync: none

日期：2026-09-19
类别：agent / rag-eval

## 背景

H-20260918-02 诊断确认：原评测集 45 条约 70% 为通用技术知识题（TypedDict/JWT/SSE…），知识库（项目内部文档）不收录，测不出"项目知识库检索质量"。双轨制 B 方案：以知识库**真实入库内容**为唯一出题源，新建项目专属评测集，跑项目检索质量的真值 recall。

## 改动

1. **新评测集** `resources/knowledge-base/test-qa/eval_project_dataset.json`：21 条（15~20 区间内按内容密度取 21），每条含 `query / ground_truth / key_points(3~5点) / category`。覆盖：langgraph-architecture×4、rag-retrieval-system×5、system-architecture×3、security-authentication×3，其余 01/02/05/07/09/10 各 1 条。题目问法模拟真实用户（"这个项目的 X 是怎么实现的 / X 配置在哪 / X 出了什么问题怎么排查"），无通用技术题。
2. **反作弊核对**：全部 88 个 key_points 逐一在生产 405 chunks（Chroma `FAQ_KNOWLEDGE_BASE` 全量 documents 拼接）grep 验证存在原句（或可验证近义原句）——验证脚本先行，21 条中 19 条一次通过，2 条各 1 点表述不匹配后改用文档实际原句（"HTTPException 统一包装为 {ok, detail} 格式"、"main.py"）复验通过。核对结果记录在评测集 `_meta.anti_cheat`。
3. **脚本** `src/ragas_test/eval_retrieval.py`：新增 `--dataset`（评测集路径，默认仍指原 `eval_dataset.json`，现有口径零影响）+ `--output`（报告输出名，默认 `retrieval_eval_report.json`）；`load_test_queries` 兼容"顶层数组 / 顶层对象含 dataset 字段"两种结构；相对路径按项目根解析（修复第一版按 cwd 解析的 bug）。
4. **报告** `src/ragas_test/project_retrieval_eval_report.json` + 失败 case 明细 `src/ragas_test/project_diagnose_report.md`。

## 实测结果（2026-09-19，21 条全量，filter 0.25 / n_results 20 / rerank top_n 5 未动）

| 指标 | 单路向量 | 混合检索 |
|---|---|---|
| key_points avg_point_coverage | **0.5690** | **0.5357** |
| key_points 全中比例 | 0.2381（5/21） | 0.2381（5/21） |
| boolean avg_recall | 0.5714（median 1.0） | 0.6190（median 1.0） |
| 平均延迟 | 616 ms | 3205 ms |

解读：
- **boolean 口径在新集上 median 升到 1.0**（旧集恒 0）——GT 主句能被 top5 覆盖，证明"答案在知识库内"的前提下指标立刻有区分度；新集比旧集更能反映真实检索质量。
- **key_points 覆盖 0.54~0.57，全中仅 5/21**：这是**真实召回损失**——每题 3~5 个事实点分散在多个 chunk，rerank top5 + filter 0.25 后实际只剩 2~3 条有效文档（诊断明细可见：如 Q0 rerank top5 中 filter(>=0.25) 仅 2 条），无法覆盖全部事实点。混合链路 rerank 相关性更高但多样性略低，key_points 覆盖反而略低于单路（0.5357 vs 0.5690），属预期 trade-off。
- 生产参数未动（H-19-02 约束）；如需提升覆盖可放宽 filter 阈值或提高 rerank top_n，另立 handoff。

## 验证

- 原集冒烟（--limit 5）与 H-02 试点前 5 条数字逐项一致（key_points 0.9/0.9、boolean 0.8/0.6），确认默认路径行为零变化；冒烟覆盖的 retrieval_eval_report.json 已 checkout 恢复 H-02 45 条全量版本。
- 语法 `ast.parse` 通过；相对路径 bug 修复后全量 21 条跑通。

## 遗留

- key_points 覆盖 0.57 暴露的召回损失（filter 0.25 滤掉低分相关 chunk）可单独立项优化；
- 双轨制 A 轨（原评测集 key_points 推广到其余 35 条）仍待用户决策；
- 文档 Agent 可在项目详解补充"双轨制评测集"说明（本 devlog 标 none：生产行为/参数未变，README 项目描述不受影响）。
