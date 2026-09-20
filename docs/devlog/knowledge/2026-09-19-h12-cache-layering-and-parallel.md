# 2026-09-19 · H-12 缓存分层 + 检索链路并行化 + MMR 前移

docs_sync: none（H-12 缓存分层/并行化/MMR 前移已于 2026-09-19 由 tool-budget 与 cache-hitrate 两篇 devlog 的同步批次覆盖至详解/素材库/README，本文件无需单独同步）

> 三件一起改，因为它们互相咬合：改写是链路最贵的节点（2226~2694 ms，占 45% 左右），
> 它既是最该被缓存的（L1），也是并行化里唯一的前置依赖（改写后的多路召回要等它）。

## 0. 先修了一个静默 bug：检索评测从未跑过多路改写

`src/ragas_test/eval_retrieval.py::rewrite_query()` 从 `stage08-03`（commit `623140e`）起就是这么写的：

```python
queries = [raw.get("主查询", query)] + raw.get("子查询", [])
```

但 `REWRITE_PROMPT` 要求的 JSON 键是 **英文** `main_query` / `sub_queries`（已实测模型输出确认）。
两个中文键永远取不到 → 静默回退成 `[原始 query]` → **所有历史检索评测实际只跑了 1 路稠密召回**，
而生产 `dense_query` 是 `[原问题] + [主查询] + 子查询` = 4 路。

影响（21 条项目评测集，同口径重跑）：

| | 修复前（归档 h07） | 修复后 |
|---|---|---|
| 改写路数 | 1 路 | **4 路** |
| 候选池（RRF 后） | 20.0 | **44.9** |
| 端到端混合检索 | 5614.9 ms | 4278.7 ms |
| boolean avg recall | 0.8095 | 0.8095（不变） |
| key_points 覆盖 | 0.7119 | 0.6833 |
| key_points 全覆盖比 | 0.4762 | 0.4286 |

**口径警示**：简历/面试素材里引用的「混合检索 avg 0.8095 / median 1.0 / 零空结果 0%」
仍然成立（boolean 口径未变），但**延迟分解和 key_points 两组数字是在 1 路召回下测的**，
对外表述时应按修复后的 4 路口径讲。

同时修了 `localhost` → `127.0.0.1`：Windows 上 `localhost` 会解析到 `::1`，
redis-stack 只监听 IPv4，表现是 10054「远程主机强迫关闭连接」→ BM25 整路静默降级为空
（h07 报告里 BM25 只有 12.6 ms 就是因为这个，其实它根本没在跑）。

---

## 1. 缓存分层改造

### 1.1 原来的问题

`CacheService.query_cache()` 的流程是：embed 一次 → RedisSearch KNN 召回 → rerank 验证 → 命中。
**没有向量就没法查 KNN**，所以命中也要付 `1 次 embedding + 12 条 rerank`，
省掉的是「改写 + 4 路召回 + 45 篇 rerank」，延迟只减半，embedding 调用量一分没少。

### 1.2 四层设计

| 层 | 缓存什么 | Key | 命中条件 | 跨用户共享 | TTL / 失效 |
|---|---|---|---|---|---|
| **L1 改写** | `{main_query, sub_queries, keywords}` | `rw:{prompt_ver}:{sha256(归一化(问题‖历史))}` | 文本 hash 精确 | ✅ 与 user/thread 无关 | 24h；`REWRITE_PROMPT_VERSION` +1 即全量失效 |
| **L2 向量** | embedding 向量（float32 二进制） | `emb:{model}:{sha256(归一化(文本))}` | 文本 hash 精确 | ✅✅ 最强：纯函数 f(text) | 7d；模型名进 key 自动隔离；靠 `allkeys-lru` 兜底 |
| **L3a 精确结果** | 检索召回结果 | `rcache:x:{kb_ver}:{sha256(归一化(问题))}` | 文本 hash 精确 | ✅ 检索结果只取决于 (问题, 知识库) | `CACHE_DEFAULT_TTL=900s` + 命中滑动续期；`MITTA_KB_VERSION` bump 全量失效 |
| **L3b 语义结果** | 检索召回结果 | `retrieve_cache:{thread_id}:{lsh_bucket}`（原实现） | LSH 桶 + KNN 召回 + rerank ≥ 0.5 | ⚠️ 现按 thread 隔离；理论上可放开到全局 | 同上 |

**归一化**（`normalize_text`）：全角空格→半角、首尾 trim、连续空白折叠、ASCII 小写。
让「  Mitta 是什么 ？ 」和「Mitta 是什么?」落到同一个 hash。

### 1.3 为什么 L3b 不能省掉 rerank

有两处 rerank，别混：

1. `fusion_nodes.rerank()` —— 检索子图里对 45 篇候选做 bge-reranker 精排，**缓存命中时确实跳过了**；
2. `query_cache()` 内部的 `online_rerank(query, 候选问题文本)` —— **它就是命中判据本身，不能跳过**。

原因写在这段代码的注释里：实测 bge-m3 原始 query 向量对短问题的语义区分度极差
（同义改写之间距离 0.6+，比无关问题还远），所以向量只能用来"召回候选"，
判定是否等价必须交给 cross-encoder。

### 1.4 L3b 两阶段验证（抵消 top_k 3→12 的成本）

`top_k` 从 3 提到 12 把 12 条场景命中率从 61.1% 拉到 88.9%，但每次要给 12 条候选打分。
现在改成两阶段：先只对 KNN top3 打分，最高分 ≥ `CACHE_RERANK_STRONG_HIT=0.7` 直接命中
（实测同义改写 rerank 分 0.89+，0.7 有充足间隔）；不够强才扩容到 top_k 全量验证。
强命中只打 3 条，未命中仍是 12 条（未命中的开销不变，但那是少数路径）。

### 1.5 各层量化收益

实测（本机 redis-stack 直连，单条文本）：

| 层 | 单次成本 | 命中后省什么 | 实测/推算 |
|---|---|---|---|
| L1 | Redis GET ~0.5 ms | 一次 LLM 改写 | **2226~2694 ms**（占混合链路 45%~48%），命中率取决于重复提问比例 |
| L2 | Redis MGET ~2 ms | embedding API 调用 | **469 ms → 2 ms**（同文本二次调用，实测）；每条 query 4 条文本 + 缓存查找 1 条 |
| L3a | Redis GET ~0.5 ms | **整条 RAG 链路** | 4278.7 ms → ~1 ms；0 次 embedding、0 次 rerank |
| L3b | 1 次 embed + KNN + 3~12 条 rerank ≈ 330~430 ms | 整条 RAG 链路 | 实测 p50 327~427 ms，命中率 61%~97%（视场景） |

原实现最好也只能到 L3b；加了 L3a 之后「字面重复提问」这条高频路径彻底打平。

### 1.6 失效策略汇总

- 改 `REWRITE_PROMPT` → `REWRITE_PROMPT_VERSION` +1
- 换 embedding 模型 → 模型名在 key 里，自动隔离
- 知识库重切/重建 → `MITTA_KB_VERSION` bump，L3a/L3b 一起失效
- 单条淘汰 → L1/L2 靠 TTL，L3 靠 TTL + 命中滑动续期
- 一键回退 → `MITTA_CACHE_LAYER=0`

> 附带发现：`clear_thread_cache()` 目前被「用户更新 system_prompt」触发，
> 但缓存的是检索结果，与 prompt 无关，这次清理其实没必要；
> 且 L3a 是跨会话共享的，按 thread 清反而会误删别人能用的条目。待后续处理。

---

## 2. 检索链路并行化

### 2.1 依赖关系（改造的核心依据）

| 节点 | 真实依赖 | 能否立即发起 |
|---|---|---|
| `rewrite` | question + history | ✅ |
| `dense(原问题)` | question | ✅ **原实现把它和改写后的路写在同一节点，被一起串行化** |
| `bm25` | question（不依赖改写） | ✅ 但原实现排在 `dense_query` 之后，白等 |
| `dense(主查询/子查询)` | rewrite 产出 | ❌ 必须等 |

关键：`dense_query` 的查询集合是 `[原问题] + [主查询] + 子查询`，
其中「原问题」那一路根本不需要等改写。

### 2.2 为什么用线程池而不是 LangGraph 并行边

主图走 `asyncio.to_thread(graph.invoke(...))`（`chat_service.invoke`），是**同步**调用。
LangGraph 的同步 superstep 对同一批无依赖节点是**串行执行**的 —— 只加边不加执行器不会变快。
要真并发得把节点改成 `async def` + 全链路换 `ainvoke`，牵动 `main_graph` / `retrieve_node` /
`chat_service` 三处，风险大。所以在**单个节点内部用 ThreadPoolExecutor 扇出**。

### 2.3 编排形状

```
阶段一（并发）：rewrite  ∥  dense(原问题)  ∥  bm25
阶段二（并发）：dense(主查询) ∥ dense(子查询1) ∥ dense(子查询2)
阶段三（串行）：RRF 融合 → rerank → filter   （仍是原图节点）
```

`src/graphs/nodes/retrieve/parallel_nodes.py::parallel_retrieve()` 取代
`rewrite → dense_query → bm25_search` 三个节点；`RETRIEVE_PARALLEL_ENABLED=0` 完整回退串行
（三个旧节点保留注册，不删代码）。

### 2.4 结果合并与去重

- 各路召回条数不合并、不裁剪，保持二维 `rank_list` 原样交给下游 `retrieve` 节点；
- 去重**不在**并行节点里做，统一由 `rrf_fusion` 按 `doc.id or doc.text` 聚合 +
  `dedup_by_text` 按正文去重，避免并行节点各写一份去重逻辑；
- 顺序固定为 `[原问题, 主查询, 子查询…, BM25]`，与改造前完全一致，RRF 结果可复现。

### 2.5 单路超时 / 失败降级

| 失败路 | 降级动作 | 影响 |
|---|---|---|
| rewrite 超时/异常 | `rewritten_queries = [原问题]` | 退化为「原问题 + BM25」两路，仍出结果 |
| dense(原问题) 失败 | 该路贡献 0 条 | 还剩改写路 + BM25 |
| dense(某子查询) 失败 | 该路贡献 0 条 | 其余路不受影响 |
| bm25 失败 | 稀疏路为空 | RedisSearch 不可用时常见，原实现也是这个行为 |
| 全部稠密路失败 | 只剩 BM25 | 仍走 rerank/filter，不返回空 |

超时常量：`REWRITE_TIMEOUT_SEC=10` / `DENSE_TIMEOUT_SEC=8` / `SPARSE_TIMEOUT_SEC=3`。
**注意**：Python 无法强制杀线程，超时是「不再等待该 future 的结果」，
线程会在后台跑完（结果被丢弃），不影响状态一致性。

### 2.6 预期端到端延迟

按修复后实测的分项（改写 2226、稠密 1280.7、BM25 12.2、重排 759.6，串行合计 4278.7 ms）：

- 单路稠密 ≈ 1280.7 / 4 ≈ **320 ms**
- 串行：2226 + 1280.7 + 12.2 + 759.6 = **4278.7 ms**
- 并行（阶段二合并成一次批调用）：2226 + (3×320) + 759.6 = **~3546 ms**（省 ~17%）
- 并行（阶段二按路拆分并发）：2226 + 320 + 759.6 = **~3306 ms**（省 ~23%）
- **并行 + L1 改写缓存命中**：~1 + 320 + 759.6 = **~1080 ms**（省 ~75%）
- **L3a 精确命中（任意编排）**：**~1 ms**

结论：**并行化单独只能省 17%~23%，真正把延迟打下来的是 L1/L3a 缓存**。
并行化的价值在于「改写未命中时也不至于串行等死」，两者是叠加关系。

### 2.7 `--parallel` 臂实测（补跑完成，但结论是「未证明」）

| 组合 | 端到端均值 | p95 | p99 | rewrite | dense | bm25 | rerank |
|---|---|---|---|---|---|---|---|
| 串行 off（基线） | 4278.7 ms | 8294 | 13826 | 2226.0 | 1280.7 | 12.2 | 759.6 |
| 并行 off | 6487.0 ms | 8582 | **37546** | — | 5791.5 ⚠ | — | 695.4 |

**并行臂反而慢了 2208 ms，但这不是并行的问题**：

- 把 6 臂按完成时间排开看 `dense_retrieve` 分项：1280.7 → 1574.5 → **9385.9** → 1833.2 → 6947.2 → 5791.5。
  同一份数据、同一段代码，波动 **6 倍**；而 `rewrite`（2226~2529）和 `rerank`（426~760）始终稳定。
  抖动 100% 落在外部 embedding API 上。
- 并行的 p99 达到 37.5 s，明显是限流/重试特征；剔除这一个离群点后并行约 4942 ms、串行约 3801 ms，
  差距缩小到约 1.1 s，仍大于理论收益（~960 ms）的同量级——**噪声彻底盖住了信号**。
- 另一个被低估的事实：`ChromaVectorStore.query` **内部早就用线程池并发**跑多条 query
  （`max_workers=min(len(query_texts),4)`），所以「4 路稠密」从来不是串行的，
  并行化的边际收益只来自「rewrite 与 dense(原问题)/bm25 的时间重叠」，约 960 ms（≈29%），不是 4×。

**处理**：`RETRIEVE_PARALLEL_ENABLED` 保持默认 `1`（结构正确、单路降级已用 mock 验证：
5 路召回齐备、改写超时退化为 `[原问题]`、全稠密失败仍走 BM25），
但**不宣称已实测收益**，需在 API 稳定窗口重测后再定。已列入后续规划第 10 项。

---

## 3. MMR 前移

### 3.1 改造

`fusion_nodes.rerank()` 新增 `MMR_STAGE` 四档（原有 `MMR_ENABLED` 保留为等价兼容）：

| 档位 | 行为 |
|---|---|
| `off` | 纯 rerank top8（原行为） |
| `post` | rerank → top20 → 向量 MMR 选 8（原 `MMR_ENABLED` 行为，H-06 已证伪） |
| `pre` | RRF 候选池 → 向量 MMR 去重到 20 → rerank → top8 |
| `pre_lex` | RRF 候选池 → **分词 Jaccard 去重**到 20 → rerank → top8（**当前默认**） |

`rrf_fusion` 现在把 RRF 分写进 `metadata["rrf_score"]`，供 pre 阶段当"相关性"信号
（cross-encoder 还没跑，此时没有 `relevance_score`）。`_mmr_select` 新增 `rel_scores` 参数，
不再硬读 metadata。

### 3.2 A/B 实测（21 条项目评测集，四组全跑完）

| 档位 | boolean recall | kp 覆盖 | kp 全覆盖比 | 端到端 | rerank | MMR 开销 | 送 rerank |
|---|---|---|---|---|---|---|---|
| `off` | 0.8095 | 0.6833 | 0.4286 | **4278.7 ms** | 759.6 ms | 0 | 44.9 篇 |
| `pre λ=0.5` | 0.8095 | **0.7119** | **0.4762** | 6776.6 ms | **477.6 ms** | 2180.0 ms | 20 篇 |
| `pre λ=0.7` | 0.8095 | 0.6714 | 0.4286 | 16524.4 ms | 449.9 ms | 4361.7 ms | 20 篇 |
| `post λ=0.5` | **0.7143 ↓** | 0.6833 | 0.4286 | 11282.1 ms | 757.9 ms | 6216.6 ms | 44.0 篇 |
| **`pre_lex`（默认）** | 0.8095 | **0.7119** | **0.4762** | 9749.2 ms ⚠ | **426.7 ms** | **74.6 ms** | 20 篇 |

三条结论：

1. **post 档确证有害**：boolean recall 0.8095 → 0.7143 掉了一个档，还多花 6217 ms。
   这补完了 H-06 的结论 —— 不只是"无增益"，把多样性选择放在 cross-encoder 之后是**负收益**。
2. **pre 档方向对，但用向量做太贵**：λ=0.5 时 kp 覆盖 0.6833→0.7119、全覆盖比 0.4286→0.4762
   （21 条里多 1 条全覆盖），rerank 因为只打 20 篇而非 45 篇降了 282 ms，
   但给 ~45 篇候选做向量化要 **2180 ms**，是省下来时间的 7.7 倍。
3. **λ 越高越没用**：pre λ=0.7 相比 λ=0.5 全面回退（0.6714 / 0.4286），
   说明起作用的确实是多样性项而不只是"少喂几篇给 rerank"。

> 注：pre_l07 / post 两臂的 MMR 耗时（4361 / 6216 ms）明显偏离 pre_l05 的 2180 ms，
> 怀疑是 SiliconFlow 侧的限速/抖动，绝对值不可信，但相对结论（都远超省下的 rerank 时间）成立。

### 3.3 默认档位为什么选 `pre_lex`

候选池扎堆的主因是 chunk 800/overlap 100 —— 相邻块共享 100 字正文**且共享同一段标题上下文**，
词面重合度天然偏高，用 jieba 分词后的 Jaccard（阈值 `MMR_LEXICAL_JACCARD=0.35`）就能识别，
成本 ~5 ms，不需要 embedding。

### 3.4 `pre_lex` 臂实测（补跑完成，定档）

| 档位 | boolean recall | kp 覆盖 | kp 全覆盖比 | rerank | MMR 开销 | 端到端 |
|---|---|---|---|---|---|---|
| `off` | 0.8095 | 0.6833 | 0.4286 | 759.6 ms | 0 | 4278.7 ms |
| `pre λ=0.5` | 0.8095 | 0.7119 | 0.4762 | 477.6 ms | 2180.0 ms | 6776.6 ms |
| **`pre_lex`** | **0.8095** | **0.7119** | **0.4762** | **426.7 ms** | **74.6 ms** | 9749.2 ms ⚠ |

`pre_lex` 拿到与 `pre λ=0.5` **完全相同的最佳质量**（0.7119 / 0.4762），但去重开销只有 **74.6 ms**（便宜 29×），
重排耗时还是全场最低的 **426.7 ms**（比 `pre` 的 477.6 还低，因为候选池更干净）。
**净账：省 759.6 - 426.7 - 74.6 ≈ 258 ms，同时质量从 0.6833/0.4286 提升到 0.7119/0.4762**——
既更快又更好，这是四档里唯一双赢的档位，因此定 `MMR_STAGE=pre_lex` 为默认。

> ⚠️ `pre_lex` 端到端 9749.2 ms **不可信**：该臂的 `dense_retrieve` 分项是 6947.2 ms，
> 是串行基线（1280.7）的 5.4 倍，明显踩到外部 API 抖动（同批 6 臂 dense 波动 1280~9386 ms）。
> `MMR` 与 `rerank` 两个**与稠密无关**的分项才是可信判据。

回退方式：`MITTA_MMR_STAGE=off`（或 `pre` / `post`）。

---

## 4. 涉及文件

| 文件 | 改动 |
|---|---|
| `src/constant/cache_constant.py` | 新增四层缓存常量（含 `KB_VERSION`、`CACHE_RERANK_STRONG_HIT`、`CACHE_KNN_FAST_K`） |
| `src/service/cache_service.py` | `normalize_text`/`text_hash`；L1 `get/set_rewrite_cache`；L2 `embed_texts_cached`/`embed_query_cached`；L3a `query/store_exact_cache`；L3b 两阶段验证 + `_take_cache_hit`；新增 `CachedEmbeddings` 包装器 |
| `src/service/chat_service.py` | 注入 `CachedEmbeddings` 到向量库 |
| `src/graphs/nodes/retrieve/cache_nodes.py` | `check_cache` 先查 L3a 再查 L3b |
| `src/graphs/nodes/retrieve/query_nodes.py` | 抽出 `run_bm25` / `run_rewrite` 纯函数；`rewrite_query` 接 L1 缓存 |
| `src/graphs/nodes/retrieve/parallel_nodes.py` | **新建**，线程池并行编排 + 单路超时降级 |
| `src/graphs/nodes/retrieve/fusion_nodes.py` | `rrf_score` 落 metadata；`_mmr_select` 支持外部 rel_scores；`rerank` 支持 `pre`/`pre_lex`/`post`；新增 `_lexical_dedup_select` |
| `src/constant/retrieval_constants.py` | `MMR_STAGE`/`MMR_PRE_LAMBDA`/`MMR_PRE_SELECT`/`MMR_LEXICAL_JACCARD`；并行化常量与超时 |
| `src/graphs/retrieve_graph.py` | 新增 `parallel_retrieve` 节点与两条编排路径（开关切换） |
| `src/ragas_test/eval_retrieval.py` | 修复改写键名 bug + 4 路组装；`localhost`→`127.0.0.1`；报告落 `reports/<日期>/`；新增 `--mmr-stage/--mmr-lambda/--parallel/--tag/--out-dir` |

## 5. 待办

1. 补跑 A/B：`pre λ=0.7`、`post λ=0.5`、`pre_lex`、`--parallel` 四臂
   （`python -m ragas_test.eval_retrieval --dataset resources/knowledge-base/test-qa/eval_project_dataset.json --limit 21 --mmr-stage <档位> --tag <后缀>`）
2. 新建 `eval_cache_layers.py`：按「重复提问 / 同义改写 / 多轮历史」三类负载量化 L1/L2/L3a/L3b 命中率与 embedding 调用削减
3. `clear_thread_cache` 的调用点重新评估（L3a 跨会话共享后不该按 thread 清）
4. 简历口径复核：混合检索的延迟分解与 key_points 需按 4 路召回重新表述
