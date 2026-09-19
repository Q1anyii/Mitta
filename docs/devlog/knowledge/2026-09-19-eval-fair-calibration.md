docs_sync: none

# 评测口径对齐生产：去掉"补到 5"，重跑 MMR on/off

**日期**：2026-09-19
**任务**：H-20260919-06
**影响面**：评测脚本（生产代码零改动）

## 背景

H-20260919-05 落地 MMR 后，评测 on/off 数字完全一样（key_points 0.5929）。
根因：`eval_retrieval.py` Step 6 有一段"过滤后不足 5 条按 RRF 补到 5"的逻辑
（2026-09-18 为对齐单路口径加的），把 MMR 选的多样性文档又用 RRF 旧顺序覆盖了。
生产 `filter_node` 只兜底 top3、不补到 5，MMR 在线上真实生效但测不出来。

## 改动

`src/ragas_test/eval_retrieval.py` Step 6：
- 删除"过滤后不足 5 条按 RRF 补到 5"整段；
- 保留"filter 后空则兜底 top3"（已对齐生产 filter_node）。

## 口径说明

- **新口径（本次）**：filter 0.25 后不足只兜底 top3，与生产一致；
- **旧口径（历史报告）**：filter 后补到 5，与生产不一致；
- 新报告文件名加 `_fair_` 后缀区分，历史报告不回改、不可比。

## 评测

- MMR off：`project_retrieval_eval_mmr_fair_off_report.json`
- MMR on：`project_retrieval_eval_mmr_fair_on_report.json`
- 21 条项目集，boolean 口径，filter 0.25。
