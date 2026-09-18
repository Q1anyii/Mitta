# Devlog: RAG 阈值放宽 + 评测口径修复 + BM25 转义 bug

> 日期：2026-09-18
> 类别：RAG / 评测
> 背景：用户要求"把最后的得分阈值稍微放宽一些"，并给出评测口径三原因分析

## 一、生产链路：filter_node 阈值 0.3 → 0.25

`src/graphs/nodes/retrieve/fusion_nodes.py` `filter_node`（原硬编码 `relevance_score >= 0.3`）：

- 放宽至 **0.25**：top5 内 0.25~0.3 的中等相关文档此前被过滤，放宽后进入上下文，
  提升召回覆盖率；代价是引入少量低相关噪声。
- 空结果兜底 top3 逻辑不变。

## 二、评测口径修复（eval_retrieval.py）

1. **hybrid 补足 5 条**（与单路同口径算 recall@5）：重排只保留 top5，过滤后通常只剩 1~3 条，
   关键词覆盖文本条数天然比单路（固定 5 条全文）少——指标被"候选条数"人为压低。
   现过滤后不足 5 条时按 RRF 融合顺序从 merged 补足到 5 条。
2. **默认阈值对齐生产**：`--filter-threshold` 默认 0.15 → **0.25**（此前评测 0.15 与生产 0.3 不一致，
   评测跑的根本不是生产参数）。

## 三、BM25 RedisSearch 转义 bug（生产 + 评测，真 bug）

**现象**：评测中部分 query 报 `Syntax error at offset 114 near self` / `near 名为`，BM25 降级返回空。

**根因**：jieba 分词 token 含 RedisSearch 语法特殊字符——代码块/路径类问题会切出
`..`、`self._`、`__`、`...`、`UploadFile` 等 token，直接拼进 FT.SEARCH 查询触发语法错误。
评测集 45 条中 3 条受影响（含代码块/路径的问题：`UploadFile`、`self._`、`../../etc/passwd`）。

**影响面**：不仅评测脚本，**生产 `query_nodes.py` 的 `bm25_search` 同款问题**——
线上用户问含代码/路径的问题同样会 Syntax error 降级，混合链路退化为纯稠密。

**修复**：对每个 token 的 RedisSearch 特殊字符（`,.<>{}\[]"'=~!@#$%^&*();:|-+\`）统一加 `\` 转义，
空 token 列表时对原始 query 转义兜底。验证：45 条 query 转义后 **0 报错**，`bge-m3`/`self`/`UploadFile` 等 token 正常命中。

## 四、实测指标（2026-09-18，45 条刁钻 QA）

| 指标 | 单路向量 | 混合（0.25 阈值 + 补足 + BM25 修复） |
| --- | --- | --- |
| avg recall@5 | 0.2774 | 0.2570 |
| median recall@5 | 0.2000 | 0.1753 |
| avg latency | 309.6ms | 4578.1ms（rewrite 占 3326.8ms，逐条串行评测特性） |
| 0 结果占比 | 0% | 0% |

> ⚠️ 与 README 历史指标（E7 混合 median 0.7732）差异大：历史 0.7732 为 8/23 产物，
> 当前环境向量库（405 chunks）状态与当时不同（git 不跟踪），**当前环境不可复现**。
> 今日报告已覆盖 `retrieval_eval_report.json`（filter_threshold=0.25）；README 历史指标由用户决定是否更新。

## 五、遗留

- 生产 0.25 阈值 + BM25 转义需线上部署后实测（对话中问代码/路径类问题不再触发 BM25 降级）。
- 长答案 recall 仍被"2+ 字整串子串匹配"口径惩罚（用户分析的原因 1），
  如需更真实指标可后续改布尔命中率或引入 RAGAS context_precision/faithfulness（本期未做）。
