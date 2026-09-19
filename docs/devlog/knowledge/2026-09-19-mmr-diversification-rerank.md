docs_sync: required

# MMR 多样性重排：rerank top20 → MMR 选 5，解决多点分散题覆盖低

**日期**：2026-09-19
**任务**：H-20260919-05
**影响面**：检索链路（rerank 节点后、filter 前）

## 背景

项目专属评测集（21 条）实测发现：rerank top5 + filter(0.25) 后只剩 2~3 篇有效文档，
且多为同章节相邻段落（语义重复），导致多点分散题的 key_points 覆盖偏低。
纯 rerank 只按相关度排序，会把语义扎堆的相邻段落都排前面，挤掉其他关键点的文档。

## 方案

在 rerank 节点输出 top20 候选后、filter 前插入 MMR（Maximal Marginal Relevance）贪心选择：

```
score(doc) = λ × rerank_score(norm) - (1-λ) × max_cosine_sim(doc, 已选集合)
```

- 初始选 rerank 分最高的第 0 篇；
- 后续每篇选 MMR score 最大的；
- rerank_score 做 min-max 归一化到 [0,1]；
- 向量已归一化（`vector_store._embed_texts` 输出），dot product 即 cosine similarity。

## 改动

| 文件 | 改动 |
|---|---|
| `constant/retrieval_constants.py` | 加 4 常量：`MMR_ENABLED=True`、`MMR_LAMBDA=0.5`、`MMR_TOP_CANDIDATES=20`、`MMR_TOP_SELECT=5` |
| `graphs/nodes/retrieve/fusion_nodes.py` | 新增 `_mmr_select()`；`rerank()` 签名加 `vector_store=None`，top_n 改 20，MMR_ENABLED 且候选>5 时选 5，异常回退纯 rerank top5 |
| `graphs/retrieve_graph.py` | `rerank_bound` 加 `vector_store=vector_store` |
| `ragas_test/eval_retrieval.py` | 评测脚本独立实现的 hybrid_retrieve 同步 MMR 逻辑（top_n=20 + `_mmr_select`），保证测到与生产一致 |

## 开关与回退

- `MMR_ENABLED=False` 一键回退为纯 rerank top5（现状基线）；
- MMR 阶段任何异常（embed 失败、向量维度不对等）自动 warning 回退纯 rerank top5，不阻断检索。

## 评测

- 基线（MMR off）vs MMR（MMR on）同口径对比，21 条项目专属集；
- 报告：`ragas_test/project_retrieval_eval_baseline_report.json` / `project_retrieval_eval_mmr_report.json`；
- 验收：key_points avg ≥ 0.62，boolean median ≥ 0.9。

## 已知限制

- MMR 阶段需对 top20 候选文本做一次批量 embed（bge-m3），增加约 200~400ms 延迟；
- λ=0.5 是经验值，后续可按 key_points 覆盖数据微调（0.4 偏相关、0.6 偏多样）。
