# retrieve_graph 混合检索优化思路

## 核心问题回顾

当前 bge-m3 双编码器对中文技术查询区分度极低（相关文档相似度仅 0.4-0.6，排名 100+），而 bge-reranker-v2-m3 交叉编码器在全量 388 条中 top1 可达 0.93-0.99。全量 rerank 虽准但不可扩展（知识库增长后 4 批 API 调用延迟线性增长）。

**混合检索的本质**：用 BM25 关键词召回补 bge-m3 语义召回的短板，RRF 融合两路结果扩大候选集，rerank 做最终精排。

---

## 八步流水线设计

### Step 1：稠密向量多路检索

```
输入：original_query + rewritten_queries（1 主 + 1~2 子）
对每个 query 独立调用 Milvus.search(TOP_K=20, 不设距离阈值)
输出：N 个 rank_list（每个是 [(doc_id, rank, distance), ...]）
```

**关键点**：
- **不做距离过滤**——bge-m3 相关文档距离 0.4-0.6，过滤会误杀
- TOP_K=20 足够：389 条小库，20 条已覆盖 5%，多路合并后候选 30-50 条
- rewritten_queries 用 LLM 生成技术关键词密集型查询（已验证改写后 rerank top1 从 0.00→0.99）

### Step 2：BM25 稀疏检索

```
输入：original_query
对全部文档的 page_content 建 BM25 索引（内存中，389 条极快）
返回 top_k=20 的 rank_list
```

**实现方案**（二选一）：
- **轻量方案**：用 `rank_bm25` 库，启动时把全部文档文本加载到内存建索引，查询 <1ms
- **生产方案**：用 Elasticsearch / OpenSearch 或 Redis Search（已有 Redis 实例，可加 RediSearch 模块）

**为什么 BM25 有效**：bge-m3 对"可变默认参数"这种精确术语匹配差，但 BM25 对"可变默认参数""bcrypt""WebSocket"等精确关键词命中极高，正好互补。

### Step 3：收集所有 rank_lists

```
rank_lists = [
  dense_original_result,      # 原始 query 向量检索
  dense_rewrite_1_result,     # 改写 query 1 向量检索
  dense_rewrite_2_result,     # 改写 query 2 向量检索（如有）
  bm25_result,                # BM25 关键词检索
]
```

每个 rank_list 是按相关性排序的 doc_id 列表。

### Step 4：RRF 融合（不做距离过滤）

```python
RRF_K = 60
scores = {}
for rank_list in rank_lists:
    for rank, doc_id in enumerate(rank_list):
        scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (RRF_K + rank + 1)
# 按融合分数降序，取 top 20-30 送 rerank
candidates = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:20]
```

**为什么此时不过滤**：向量距离和 BM25 分数量纲不同，RRF 只看排名不看绝对值。过滤应在 rerank 之后用统一的相关性分数做。

### Step 5：page_content 去重

```
对 candidates 中的文档，按 page_content 文本哈希去重
（同一文档可能被多路召回，RRF 已按 doc_id 去重，但不同 chunk 可能文本高度重叠）
```

**实现**：用 `hashlib.md5(doc.text.encode()).hexdigest()` 做 key，保留 RRF 分数最高的那条。知识库按标题切分，相邻 chunk 可能有 50 字符 overlap，去重避免 rerank 浪费。

### Step 6：rerank 精排

```
输入：去重后的 candidates（15-20 条）
调用 bge-reranker-v2-m3，返回 top_n=5
输出：5 条最相关文档 + rerank 分数
```

**关键**：rerank 用 **original_query**（不是改写 query），因为交叉编码器对原始口语问题也能准确判断相关性，且最终答案要基于原始问题。

### Step 7：分数/距离阈值过滤（最后执行）

```
对 rerank top 5，过滤 rerank_score < 0.05 的文档
（0.05 是经验阈值：相关文档 rerank 分数通常 >0.3，不相关 <0.01）
如果过滤后为空，兜底返回 rerank top 3（宁可不准确也不返回空）
```

**为什么最后过滤**：
- 向量距离过滤会误杀（bge-m3 相关文档距离 0.4-0.6）
- BM25 分数过滤无统一标准
- rerank 分数是唯一统一的相关性度量，在最后过滤最安全

### Step 8：写入 state

```python
return {
    "rewritten_queries": queries,
    "merged_docs": candidates,       # RRF 融合后的候选（供调试/缓存）
    "reranked_docs": top_docs,       # 最终 5 条（供 LLM 生成）
    "cache_hit": False,
}
```

---

## 与当前全量 rerank 方案的对比

| 维度 | 全量 rerank（当前） | 混合检索（优化后） |
|------|---------------------|-------------------|
| 候选集 | 388 条全部 | 15-20 条（RRF 融合） |
| rerank API 调用 | 4 批 × 100 条 | 1 批 × 20 条 |
| 延迟 | ~3s（rerank 占大头） | ~1.5s（BM25 <1ms + 向量 50ms × 3 + rerank 0.5s） |
| 可扩展性 | 知识库 1000 条后 rerank 10 批 | 候选集恒定 20 条，与库大小无关 |
| 准确率 | 高（全量无遗漏） | 高（BM25 补向量短板，RRF 保召回） |

---

## 落地建议

1. **BM25 先用 `rank_bm25` 内存方案**——389 条文档启动时建索引 <100ms，查询 <1ms，无需额外基础设施
2. **rewritten_queries 限制 2 路**（1 主 + 1 子），避免向量检索次数过多
3. **RRF 后取 top 20 送 rerank**，rerank top_n=5，分数阈值 0.05 过滤
4. **缓存层不变**——cache_service 仍按 thread_id + question 缓存 reranked_docs
5. **可加动态预检**：如果原始 query 向量检索 top1 rerank 分数 >0.5，跳过改写和 BM25，直接用单路结果（降低延迟）

需要我把这个思路落成代码吗？