# Mitta AI 流程图

## 1. 主对话图（main_graph）

<div align="center">
  <img src="figures/main-graph.svg" alt="Mitta 主对话图" width="95%">
</div>

### 节点说明

| 节点                | 职责             | 关键实现                                                                                                                                                                                               |
| ----------------- | -------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **router_node**   | 统一路由           | 一次 LLM 调用输出 `{persona, need_retrieval}`（`ROUTER_PROMPT`）；闲聊/自我介绍强模式短路 0 次 LLM；前端手选 `configurable.persona_override` 时 persona 直接采用、同调用只判 need_retrieval；解析失败兜底 persona=默认/手选、need_retrieval 保守 True |
| **retrieve_node** | 调用 RAG 子图检索知识库 | `retrieve_graph.invoke()`，Document 转 dict 存入 state（checkpoint 反序列化兼容）；进入子图前经 custom 通道推 ack 预响应开场白                                                                                                 |
| **llm_node**      | 核心生成节点         | 组装 System Prompt（默认+用户自定义+长期记忆+人格 prompt）→ ToolFilter 筛选工具 → 按人格白名单收缩 → `model.bind_tools()` → `model.stream()` → 合并 chunk 提取 tool_calls                                                           |
| **tool_node**     | 执行 MCP 工具      | LangGraph `ToolNode`，按工具名路由；CachePolicy 缓存同参数结果                                                                                                                                                    |
| **memory_node**   | 提取长期记忆         | LLM 从对话中提取用户档案写入 PostgresStore；idle 闲聊轮快速跳过；非闲聊轮提取包进节点内 daemon 线程 fire-and-forget，节点立即返回、`done` 事件先行                                                                                               |

### 条件路由

- **START → router_node**：统一路由（一次 LLM 出 persona + need_retrieval；闲聊/手选走短路），随后走条件边
- **router_node → route**：`needs_retrieval=True` 走检索链路，否则直接到 llm_node；快速短路命中时路由不做 LLM 调用直接 false
- **llm_node → route_after_llm**：`tool_calls` 非空走 tool_node，否则走 memory_node
- **tool_node → llm_node**：工具执行结果回到 LLM 生成最终回答（可多轮循环，工具调用上限按轮计数 `MAX_TOOL_ROUNDS=8`）

---

## 2. RAG 检索子图（retrieve_graph）

<div align="center">
  <img src="figures/retrieve-graph.svg" alt="Mitta RAG 检索子图" width="95%">
</div>

### 节点说明

| 节点                    | 职责                     | 关键实现                                                                                                                                                                                                                                 |
| --------------------- | ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **check_cache**       | 检索缓存两级查找               | 先 L3a 精确层 `query_exact_cache(question)`（文本 hash，0 embedding / 0 rerank），未命中再走 L3b 语义层 `query_cache(thread_id, question)`（LSH 分桶 + KNN + rerank 验证）；**命中后仍走 filter**（缓存文档已带 relevance_score，再过阈值过滤）                                   |
| **parallel_retrieve** | 并行编排（默认路径）             | 阶段一 `rewrite ∥ dense(原问题) ∥ bm25` 并发；阶段二 `dense(主查询) ∥ dense(子查询)` 并发；单路超时/失败只丢弃该路结果，不阻塞整体                                                                                                                                           |
| **retrieve**          | RRF 融合 + 去重            | Reciprocal Rank Fusion（k=60，**路级权重**）融合稠密多路 + BM25：rank_list 结构 `[原问题, 主查询, 子查询…, bm25]` 赋权 `[1.0, 1.0] + [0.4]*(n-3) + [1.0]`（子查询是兜底补充、降权 0.4 避免泛化查询噪声稀释主路排名）；按 doc_id 去重、按文本去重；RRF 分写入 `metadata["rrf_score"]` 供 pre 阶段多样性去重当相关性信号 |
| **rerank**            | 候选去重 + 在线重排 + 多样性选择    | `MMR_STAGE=off`（默认，2026-09-23 起）：RRF 候选池直接送 SiliconFlow `BAAI/bge-reranker-v2-m3` 精排 top_n=20 → 分数写入 `metadata["relevance_score"]`；`pre_lex` 档（词级 Jaccard 去重 44→20）保留可切换，探针诊断实测无增益故默认关闭                                              |
| **filter**            | 相关性阈值过滤                | 过滤 `relevance_score < 0.15` 的噪声文档；过滤后为空时兜底返回原始 top 3（宁可不准确也不返回空）；**缓存命中与未命中两路都汇入本节点**                                                                                                                                                |
| **store_cache**       | 写入 Redis（L3a + L3b 双写） | L3a `rcache:x:{kb_ver}:{hash(问题)}`；L3b `retrieve_cache:{thread_id}:{bucket_id}`；动态 TTL 900s，命中自动续期；**与 filter 同轮并行（rerank 双出边）**                                                                                                     |

### 并行编排

**真实依赖**：`rewrite` 依赖 question + history；`dense(原问题)` 与 `bm25` 只依赖 question，**都不依赖改写**；只有 `dense(改写后各路)` 必须等 rewrite。

```
阶段一（并发）：rewrite  ∥  dense(原问题)  ∥  bm25
阶段二（并发）：dense(主查询) ∥ dense(子查询1) ∥ dense(子查询2)
阶段三（串行）：RRF 融合 → rerank → filter
```

**为什么用线程池而不是 LangGraph 并行边**：主图是同步 `invoke`（`chat_service` 里 `asyncio.to_thread(graph.invoke)`），
LangGraph 的**同步 superstep 对同一批无依赖节点是串行执行**的，只加边不加执行器不会变快
（要真并发得把节点改 async + 全链路换 `ainvoke`）。所以在单节点内用 `ThreadPoolExecutor` 扇出。

**降级策略**（任一路失败只丢弃该路，不抛异常）：

| 失败路           | 降级动作                        | 影响                     |
| ------------- | --------------------------- | ---------------------- |
| rewrite 超时/异常 | `rewritten_queries = [原问题]` | 退化为「原问题 + BM25」两路，仍出结果 |
| dense(任一路) 失败 | 该路贡献 0 条候选                  | 其余路不受影响                |
| bm25 失败       | 稀疏路为空                       | RedisSearch 不可用时常见     |
| 全部稠密路失败       | 只剩 BM25                     | 仍走 rerank/filter，不返回空  |

超时常量：改写 10s / 稠密 8s / BM25 3s。注意 Python 无法强制杀线程，超时是「不再等待该 future 的结果」。

### 关键参数

| 参数                     | 值                                                                                                               | 位置                                                                                      |
| ---------------------- | --------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| 稠密召回 n_results         | 20/路（原问题 + 主查询 + 子查询，最多 4 路）                                                                                    | `graphs/nodes/retrieve/parallel_nodes.py`                                               |
| BM25 召回 top_k          | 20                                                                                                              | `graphs/nodes/retrieve/query_nodes.py` `run_bm25`                                       |
| RRF_K                  | 60                                                                                                              | `constant/retrieval_constants.py`                                                       |
| 重排候选 top_n             | 20                                                                                                              | `constant/retrieval_constants.py` `MMR_TOP_CANDIDATES`                                  |
| **MMR 档位** `MMR_STAGE` | `off`（默认，2026-09-23 起）／`pre_lex`（rerank 前词级 Jaccard 去重 44→20，曾默认、探针实测无增益后关闭）／`pre`（向量 MMR）／`post`（rerank 后，已证伪） | `constant/retrieval_constants.py`                                                       |
| MMR 词级去重阈值             | `MMR_LEXICAL_JACCARD=0.35`，`MMR_PRE_SELECT=20`                                                                  | `constant/retrieval_constants.py`                                                       |
| MMR λ                  | `MMR_LAMBDA=0.5`（post）／`MMR_PRE_LAMBDA=0.7`（pre）                                                                | `constant/retrieval_constants.py`                                                       |
| 并行编排开关                 | `RETRIEVE_PARALLEL_ENABLED=1`（默认），`RETRIEVE_PARALLEL_WORKERS=4`                                                 | `constant/retrieval_constants.py`                                                       |
| 单路超时                   | 改写 `REWRITE_TIMEOUT_SEC=10` / 稠密 `DENSE_TIMEOUT_SEC=8` / BM25 `SPARSE_TIMEOUT_SEC=3`                            | `constant/retrieval_constants.py`                                                       |
| 过滤阈值                   | 0.15（relevance_score ≥ 0.15，最多取 8 篇，空则兜底 top 3，常量 `RERANK_FILTER_THRESHOLD`）                                    | `constant/retrieval_constants.py` + `graphs/nodes/retrieve/fusion_nodes.py` filter_node |
| 缓存分层开关                 | `CACHE_LAYER_ENABLED=1`（默认）                                                                                     | `constant/cache_constant.py`                                                            |
| 缓存 TTL                 | L1 改写 24h／L2 向量 7d／L3 检索结果动态 900s（命中续期）                                                                         | `constant/cache_constant.py`                                                            |
| 缓存强命中阈值                | `CACHE_RERANK_STRONG_HIT=0.7`（两段式验证快通道）、`CACHE_RERANK_HIT_SCORE=0.5`、`CACHE_KNN_FAST_K=3`                       | `constant/cache_constant.py`                                                            |
| 知识库版本键                 | `KB_VERSION=v1`（入库重灌即失效全部 L3a/L3b）                                                                              | `constant/cache_constant.py`                                                            |
| Embedding 模型           | BAAI/bge-m3（1024 维）                                                                                             | `constant/embedding_constants.py`                                                       |
| 切分 chunk               | 800 / overlap 100（chunks 270）                                                                                   | `constant/embedding_constants.py`                                                       |
| 重排模型                   | BAAI/bge-reranker-v2-m3                                                                                         | `init.py`                                                                               |
| BM25 索引名               | kb_bm25                                                                                                         | `constant/cache_constant.py`                                                            |

### 混合检索设计思路

bge-m3 双编码器对中文技术查询区分度低（相关文档余弦相似度仅 0.4-0.6，排名 100+），而 BM25 对精确术语命中极高。两路互补：

- **稠密向量**：擅长语义相似（"如何避免默认参数陷阱" ≈ "可变默认参数的危害"）
- **BM25**：擅长精确关键词匹配（"可变默认参数""bcrypt""WebSocket" 直接命中）
- **RRF 融合**：只看排名不看绝对分数，统一两路量纲差异
- **rerank 精排**：交叉编码器对 query-doc 对做注意力计算，最终排序依据
- **阈值过滤**：用 rerank 分数（0~1）做统一过滤，0.15 以下视为噪声丢弃；过滤后为空时兜底返回原始 top 3，最多取 8 篇

**多样性去重（`MMR_STAGE` 四档，曾定档 `pre_lex`，2026-09-23 起默认 `off`）**：MMR 的位置比开关更重要，四档 A/B（21 条项目评测集，key_points 口径）如下——**历史定档依据**；后经 29 条 QUESTION_POOL 探针诊断（2026-09-23）MMR 实测无增益（项目集双盲 A/B recall 均 0.5238），生产默认回 `off`：

| 档位                 | boolean recall | kp 覆盖  | kp 全覆盖比 | 重排耗时      | 去重开销     | 送 rerank |
| ------------------ | -------------- | ------ | ------- | --------- | -------- | -------- |
| `off`              | 0.8095         | 0.6833 | 0.4286  | 759.6 ms  | 0        | 44.9     |
| `pre` λ=0.5        | 0.8095         | 0.7119 | 0.4762  | 477.6 ms  | 2180 ms  | 20       |
| `pre` λ=0.7        | 0.8095         | 0.6714 | 0.4286  | 449.9 ms  | 4362 ms  | 20       |
| `post` λ=0.5       | **0.7143 ↓**   | 0.6833 | 0.4286  | 757.9 ms  | 6217 ms  | 44.0     |
| **`pre_lex`（曾默认）** | 0.8095         | 0.7119 | 0.4762  | **426.7** | **74.6** | 20       |

结论：① `post`（rerank 后再多样性选篇）**是负收益**——recall 从 0.8095 掉到 0.7143，却多花 6217 ms，彻底证伪；② 向量 `pre` 方向对（kp 覆盖 0.6833→0.7119）但花 2180 ms 只省 282 ms 重排，净亏 7.7×；③ λ 调高反而变差（0.7119→0.6714），说明起作用的是**多样性项本身**，不是"少送几篇给 rerank"；④ `pre_lex` 用 jieba 词级 Jaccard（阈值 0.35）近似多样性，**不需要额外 embedding**，拿到与 `pre` λ=0.5 相同的最佳质量，开销只有 74.6 ms（便宜 29×），重排 759.6→426.7 ms，**净省 ~258 ms 且质量更好**。

> ⚠ **后续修正（2026-09-23）**：29 条 QUESTION_POOL 探针诊断与项目集双盲 A/B（`project_retrieval_eval_mmr_fair_off/on`，recall 均 0.5238）显示 **MMR 在现役链路上无增益**，生产默认 `MITTA_MMR_STAGE=off`（`retrieval_constants.py:42`）。上表保留为当时定档依据；`pre_lex` 作为可切换档位保留。

### 端到端实测（并行 vs 串行）

**端到端实测**（21 条项目评测集，同一份数据同一环境）：

| 组合                   | 端到端均值     | p95     | 改写     | 稠密       | BM25 | 重排    | MMR  |
| -------------------- | --------- | ------- | ------ | -------- | ---- | ----- | ---- |
| 串行基线（off）            | 4278.7 ms | 8294 ms | 2226.0 | 1280.7   | 12.2 | 759.6 | 0    |
| 串行 + pre_lex（曾默认）    | 9749.2 ms | —       | 2286.4 | 6947.2 ⚠ | 14.1 | 426.7 | 74.6 |
| **并行（parallel+off）** | 6487.0 ms | 8582 ms | —      | 5791.5 ⚠ | —    | 695.4 | 0    |

> ⚠ **读数口径**：本次 6 臂连跑期间，外部 embedding API 抖动剧烈——同一份数据、同一段代码，`dense_retrieve` 分项在 1280.7 ~ 9385.9 ms 之间跳变（6× 方差），而 `rewrite`（2226~2529 ms）与 `rerank`（426~760 ms）始终稳定。因此**只有稳定性分项可信，含稠密项的端到端均值不可跨臂比较**。并行臂结构收益的理论值约 960 ms（把 rewrite 与 dense(原问题)/bm25 重叠），远小于该抖动量级，本次 A/B **未能验证也未能否定**并行收益，需在 API 稳定窗口重测。

**理论分解**（剔除抖动，仅按依赖关系推算）：

| 组合                 | 推算端到端     | 说明                                                |
| ------------------ | --------- | ------------------------------------------------- |
| 串行基线               | ~3.5 s    | rewrite 2.2s + dense 1.3s + bm25 0.01s            |
| 并行（重写与首路召回重叠）      | ~2.5 s    | max(rewrite 2.2s, dense 0.3s) + dense 0.3s，省 ~29% |
| 并行 + L1 改写缓存命中     | ~0.6 s    | 改写 0 ms，只剩两阶段稠密                                   |
| **L3a 精确命中**（任意编排） | **~1 ms** | 0 embedding / 0 rerank，直接返回                       |

> 补充事实：`ChromaVectorStore.query` **内部已用线程池并发**跑多条 query（`max_workers=min(len(query_texts),4)`），所以「4 路稠密」本身早就不是串行的，并行的边际收益只来自「rewrite 与 dense(原问题)/bm25 的时间重叠」，不要按 4× 去估算。生产环境保留 `RETRIEVE_PARALLEL_ENABLED=1` 默认开启，`=0` 一键回退串行对比。
