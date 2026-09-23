---
docs_sync: none（已同步 2026-09-23：28 条 LLM-judge 全量结果 + 自建评测集 28 条 + RRF 路级权重 + MMR 默认 off + TypeSafeClassifier + 增量入库去 hash，已落根 README/项目详解 04/11/索引/实测数据/简历 Bullet/素材库/面试稿件/Wiki）
---

# 2026-09-23 检索链路诊断（QUESTION_POOL 29 条探针）

## 背景

前端聊天页 `QUESTION_POOL`（`app.js:2059-2099`）共 29 条引导问题，覆盖 10 大主题。用户反馈"每次都显示未检索到"，先排查是知识库缺内容还是检索质量问题。

**结论先行**：不是知识库缺内容，是 probe 集本身有偏 + 检索管线各环节的真实增益需要在口语 query 集上重测。

## 诊断方法

1. 从前端 `QUESTION_POOL` 抽取 29 条问题，构建临时探针数据集 `_probe_question_pool.json`（key_points 用问题原词，不代表标准答案）。
2. 用 `eval_retrieval.py --diagnose` 跑 4 组配置对比：
   - A：单路 dense（基线）
   - B：混合管线现状（改写 + RRF + rerank + 阈值 0.15）
   - C：混合 + rerank 只打分不重排（patch `online_rerank` 按原 RRF 顺序）
   - D：混合 + 跳过改写（patch `rewrite_query` 直接返回原 query）

## 实测结果

| 配置 | avg recall@5 | 平均延迟 | 说明 |
|---|---|---|---|
| A 单路 dense | **0.828** | 131 ms | 原始 query 直查 |
| B 混合现状 | 0.690 | 2946 ms | 改写 4 路 + RRF + rerank |
| C 混合 + rerank 不重排 | 0.586 | 2698 ms | 验证 rerank 是否帮倒忙 |
| D 混合 + 跳过改写 | **0.690** | **722 ms** | 验证改写是否帮倒忙 |

中位数 recall@5 全部 = 1.0，0 结果占比全部 = 0%。

## 三个硬结论

1. **rerank 重排是真增益**：C vs B（0.586 → 0.690），去掉重排 recall 掉 10 个点。rerank 不是元凶。
2. **改写对 recall 无影响但吃延迟**：D vs B（0.690 → 0.690），跳过改写 recall 不变，延迟从 2946ms 降到 722ms（快 4 倍）。但**不能据此关改写**——probe 集本身就是书面术语 query（"TypedDict""bcrypt"），真实用户口语 query 靠改写映射术语。
3. **MMR 实测无增益**：2026-09-19 项目集双盲 A/B（`project_retrieval_eval_mmr_fair_off/on`）recall 都是 0.5238，完全一致。生产代码默认 `MITTA_MMR_STAGE=off`（`retrieval_constants.py:42`），无需改动。

## probe 集偏差声明

29 条 QUESTION_POOL 全部照知识库术语编写，与文档用词高度重合，存在以下偏差：

- **BM25 在 probe 上是负贡献**（dense 已经精确命中，BM25 噪声稀释），但真实口语 query 靠 BM25 词面救命。
- **改写在 probe 上无增益**（query 已书面化），但真实口语 query 靠改写映射术语。
- **阈值 0.15 在 probe 上合理**，但真实 query 分数分布不同，不能据此调阈值。

**禁止用本 probe 集调生产参数**。后续要建一个"真实口语 query"评测集（10-20 条大白话问题），在那个集上 A/B 改写、BM25、阈值才有意义。

## 知识库内容缺口（真该补的）

诊断中发现 3-4 个 query 在现有 01-10 篇里答案不完整：

- `MySQL 连接池如何配置和管理？`（05 篇可能未专门讲）
- `Vue ref vs reactive 如何选择？`（09 篇可能缺对比表）
- `bcrypt 彩虹表`（08 篇有但切分位置可能不理想）

## 遗留

- 临时探针文件保留在 `resources/knowledge-base/test-qa/_probe_question_pool.json` 和 `src/agent_test/_probe_*.py`，用户明确说不删。
- 报告在 `src/agent_test/reports/2026-09-23/probe_*.json`。
- 下一步：建口语 query 评测集后重测，再决定是否调改写/BM25/阈值。
