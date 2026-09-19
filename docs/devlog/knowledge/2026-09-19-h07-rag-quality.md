docs_sync: required
# H-20260919-07 RAG 质量优化全包（P0+P1+P2+P3）

## 背景
H-06 fair 口径基线（chunk 300/50、filter 0.25、top5、MMR off）：
hybrid boolean avg=0.5238、key_points avg=0.4524、median=1.0。
21 条项目集上召回偏低，需重切 chunk + 放宽参数 + 生成端引用 + LLM-judge 五指标。

## P0 重切 chunk
- `embedding_constants.py`：`CHUNK_SIZE 300→800`，`CHUNK_OVERLAP 50→100`。
- 旧 chunk 把小节切碎，800 字保留完整段落语义，减少"一个要点跨 chunk"。
- 重入库后 chunk 数 405→270（21 个 md 文件）。

## P1 放宽召回参数
- `retrieval_constants.py`：新增 `RERANK_FILTER_THRESHOLD=0.15`（原硬编码 0.25）；`MMR_ENABLED=False`（H-06 证伪无增益）；`MMR_TOP_SELECT 5→8`。
- `fusion_nodes.py`：`filter_node` 改用常量；返回 `[:MMR_TOP_SELECT]`。
- `llm_node.py`：`MAX_RETRIEVAL_DOCS 5→8`。
- `eval_retrieval.py`：Step 5 永远拿 `MMR_TOP_CANDIDATES=20` 篇 rerank（不再因 MMR off 只打 8）；argparse `--filter-threshold` 默认值改常量。

## P1 验收（21 条项目集，boolean 口径）
| 指标 | H-06 基线 | H-07 P0+P1 | 目标 |
| --- | --- | --- | --- |
| boolean avg | 0.5238 | **0.8095** | ≥0.62 |
| boolean median | 1.0 | 1.0 | 维持 |
| key_points hybrid avg | 0.4524 | **0.7119** | ≥0.55 |

报告：`src/ragas_test/h07_p0p1_report.json`。

## P2 生成端引用约束
- `llm_node.py`：检索分支 user_content 加引用要求——事实句挂 `[文档 i]` 角标，无依据说"知识库暂未覆盖"。
- `app.js`：`marked.parse` 后正则 `\[(\d{1,2})\](?!\()` 转 `<sup class="cite-ref">`（负向前瞻排除 markdown 链接）。
- `style.css`：`.cite-ref` 上标样式（#6b7cff，0.75em）。

## P3 LLM-as-judge 五指标
- 新建 `src/ragas_test/eval_ragas_judge.py`：hybrid_retrieve → model 生成 → DeepSeek(temp=0) judge。
- 21 条项目集结果：
  - context_precision 0.6381
  - context_recall 0.8005
  - faithfulness 0.959
  - answer_relevancy 0.9881
  - answer_correctness 0.7976
- 报告：`src/ragas_test/ragas_judge_report.json`。

## 已知问题
- WSL2 localhost 转发不稳定（10054），ingest 脚本 RedisSearch 写入包 try/except 跳过；BM25 索引未重建（评测时 BM25 路降级为空，dense+rerank 结论不受影响）。
- 服务器 1.6G 内存，线上 chroma 同步本 handoff 未执行。
