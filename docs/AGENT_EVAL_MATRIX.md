# Agent 系统评测体系（Agent-Level Evaluation Matrix）

> 定位：将原有 `ragas_test/`（2026-09-20 改名 `agent_test/`）从「RAG 检索评测」升级为「**整个 Agent 系统**」的评测体系，
> 覆盖路由、检索、生成、工具装配/安全/兜底、记忆、缓存、限流、认证、在线实测全链路。
> 每个项目描述中的指标都有对应评测，没有指标的也为其定义指标测试。

## 评测维度总览

| 编号 | 维度 | 脚本 | 关键指标 | 对齐的项目描述 | 运行方式 |
|---|---|---|---|---|---|
| E1 | 动态路由 | `eval_routing.py` | 意图分类准确率、检索召回率 | LangGraph 四节点动态路由（检索/非检索分流）。**现役口径（2026-09-22 后）**：统一路由评测 **37 条**（白盒调用现役 `router_node`，一次判人格 + 检索，固定 `temperature=0` 重跑两次）：意图路由 **91.43% / 94.29%**（只报区间）、检索召回 90%→100%；人格四分类 **32/32=100%**（两次稳定）。❌ 作废：17/17、16/17、94.12%（旧 `classify_node` 已摘除，单次采样口径） | 离线白盒（LLM） |
| E2 | 工具筛选 | `evaluate_tool_filter.py` | recall@k / precision@k | 工具装配规则层+语义层并集召回 | 离线白盒（真实 MCP 工具） |
| E3 | 工具装配降级/熔断 | `eval_tool_assembly.py` | 并集召回、语义层异常降级、熔断生效 | 语义层异常自动降级规则层并熔断 | 离线白盒（mock 向量库） |
| E4 | MCP 安全校验 | `eval_tool_safety.py` | 命令白名单拦截率、包名校验拦截率、敏感 env 拦截率、内网 url 拦截率 | 命令白名单、包名校验、敏感变量拦截 | 纯函数离线 |
| E5 | 工具结果兜底 | `eval_tool_truncation.py` | 异常→ToolMessage 转换率、描述截断生效、文档截断生效、工具调用上限的按轮语义、**失败熔断生效** | 工具返回结果长度截断与异常兜底；上限计数为 per-turn（新 HumanMessage 归零）；同工具连续失败 2 次本轮禁用 | 纯函数离线 |
| E6 | 语义缓存 | `eval_semantic_cache.py` | 同义改写命中率、误命中率、embedding 调用降低 | LSH 分桶+KNN 候选+reranker 阈值两级判定。**现役口径（E6-B，2026-09-22）**：隔离会话命中率 **97.2%**（35/36）；生产默认（12 条+候选 3）**61.1%**；误命中硬负 0/8 + 跨域 0/6；embedding 调用实测降 **12.5%**（24 query 流 96→84 条）。瓶颈 = KNN 候选数（recall@3 61.1% / @12 88.9%），非 reranker 阈值。❌ 作废：67%、120→40、573→350ms | 离线白盒（真实 Redis） |
| E7 | 混合检索 | `eval_retrieval.py` | **key_points 事实点 recall（2026-09-19 H-07 后）**、P95 延迟、单路 vs 混合、`--diagnose`、RRF 路级权重 | 21 条项目专属集：单路 **0.7476** / 混合 **0.7119**、boolean avg 单/混均 **0.8095**、median 1.0；RRF 支持路级权重（`ae98c37`，子查询降权 0.4 避免泛化查询稀释主路排名）。历史口径不得混用：H-07 前 0.5690/0.5357、试点 0.7583/0.7833、boolean 0.3111/0.2667、coverage 0.77 | 离线白盒 |
| E8 | RAGAS 五指标 | `ragas_eval.py` + `eval_ragas_judge.py`（H-07 P3） | context_precision/recall、faithfulness、answer_relevancy、answer_correctness | RAG 检索增强五大指标。**现役口径（2026-09-23 全量重建后，28 条自建 probe 集 LLM-judge）**：**0.6593 / 0.7432 / 0.9464 / 0.9929 / 0.7575**。历史：21 条集 0.6381/0.8005/0.959/0.9881/0.7976（H-07 P3，已作废） | 离线（LLM 评分，**不入 CI**） |
| E9 | 记忆 | `eval_memory.py` | 写入/读取延迟、重复写入减少 | 生产容器内实测（2026-09-20）：写 P95 3.01 / 读 P95 2.09 ms；公网对照 28.00 / 51.87 ms | 离线白盒（公网直连 / 容器内） |
| E10 | 限流 | `eval_rate_limit.py` | 拦截准确率、Redis 降级内存、路径过滤 | 30 次/60s、Redis 异常降级内存 deque | 离线白盒 |
| E11 | 认证 | `eval_jwt.py` | 续签成功率、校验耗时、并发 | JWT 双 Token 无感续签 100% | 离线白盒 |
| E12 | SSE 流 | `eval_sse.py` | 首 token 延迟、流纯净度 | SSE 流式对话。现役（线上，每场景 n=3）：shortcut p50 1608ms / no_retrieval 1914ms / retrieval 12348ms；流纯净度 18/18 | 在线 HTTP |
| E13 | 在线实测 | `eval_online.py` | health、登录、对话、限流 429、登出失效 | 线上全链路 | 在线 HTTP（www.mittaai.xyz） |
| E14 | CI 回归 | `tests/test_agent_regression.py` | 路由/安全/兜底/缓存 key 纯函数断言 | 回归守护（**不含 RAGAS**） | pytest（CI） |
| E15 | 人格路由 | `persona_router_eval.py` | 四分类分流准确率、混淆矩阵 | 多人格 v1：**32/32=100%**（两次稳定，固定 temperature=0；典型样本基线）。历史：16/16（v1 首测，乐观基线） | 离线白盒（LLM） |

## 指标与项目描述的对应关系

- 混合检索 recall（**key_points 事实点口径，H-07 后**：21 条项目集 单路 **0.7476** / 混合 **0.7119**、boolean avg 单/混均 **0.8095**）→ E7 `eval_retrieval.py`；H-07 前 0.5690/0.5357、试点 0.7583/0.7833、boolean（0.3111/0.2667）与 coverage（0.77）均为历史口径，不得混用
- 多人格路由（手选短路/自动四分类、人格 prompt 注入、按人格工具白名单）→ E15 `persona_router_eval.py`（32/32）
- 第三方 MCP 免改代码接入（JSON 配置注册）→ E4 `eval_tool_safety.py`（配置校验）+ E2/E3（装配）
- 命令白名单、包名校验、敏感变量拦截 → E4
- 工具返回长度截断与异常兜底 → E5
- 双层记忆（短期按会话、长期按用户 LLM 每轮提取增量合并）→ E9
- 工具装配规则层+语义层并集召回、语义异常降级+熔断 → E2 + E3
- 缓存 LSH 分桶+KNN 候选+reranker 两级判定、命中率 61%~97%（误命中 0）→ E6
- JWT 双 Token 无感续签 100%、登出即时失效 → E11
- 限流 Redis ZSET 滑动窗口 30 次/60s、Redis 异常降级内存 deque → E10
- 动态路由（意图分类→检索→生成→记忆）→ E1（37 条 91%~94%）

## 单元测试与运行方式

### 单元测试

使用 pytest 框架，覆盖核心工具模块：

| 测试文件 | 覆盖模块 | 用例数 |
|---|---|---|
| `tests/test_config.py` | 环境变量加载/校验/布尔解析 | 32 |
| `tests/test_jwt_utils.py` | JWT 签发/验证/过期/密码哈希(bcrypt) | 12 |
| `tests/test_rand_id_util.py` | 随机 ID 生成/唯一性/int 范围 | 10 |
| `tests/test_agent_regression.py` | 动态路由/MCP 安全/工具兜底/记忆缓存 key/工具名解析 | 19 |

**运行方式**：

```bash
cd src
pytest ../tests/ -v
```

**最新结果**：**73 passed**（32+12+10+19），覆盖配置/JWT/ID 生成/Agent 回归（`test_access_token_expiration` 秒级精度断言已加 2s 容差）。该 4 文件组合被 `agent-regression.yml`（E14）纳入 CI 门禁（不含 RAGAS）。

### 评测运行方式

评测矩阵脚本统一约定：`conda activate langchain1.2`，`cd src`，`python -m agent_test.<script>`；离线白盒脚本需 Redis/PostgreSQL/在线 LLM/Embedding 可用，纯函数脚本无外部依赖。运行示例：

```bash
cd src
python -m agent_test.eval_routing         # E1 动态路由（需 LLM）
python -m agent_test.eval_tool_safety     # E4 MCP 安全（纯函数）
python -m agent_test.eval_semantic_cache  # E6 语义缓存（需 WSL RedisSearch + embed + reranker）
python -m agent_test.eval_online --username qianyi --password xxx  # E13 线上实测（默认 https://www.mittaai.xyz）
python -m agent_test.ragas_eval           # E8 RAGAS 五项指标（LLM-as-judge，耗时大，仅线下评估，不进 CI）
pytest ../tests/ -v                       # E14 CI 回归（73 用例，含 test_agent_regression.py）
```
## 执行约定

1. 环境：`conda activate langchain1.2`，`cd src`，`python -m agent_test.<script>`。
2. 离线白盒脚本直接调用内部模块（需 Redis/Postgres/LLM 可用）；纯函数脚本无外部依赖。
3. 在线实测脚本需要服务器在线（默认 `https://www.mittaai.xyz`）。
4. **RAGAS 五指标（E8）耗时大，只做线下评估，不接入 CI。**
5. 每个脚本输出 `*_eval_report.json` 到 `agent_test/reports/<日期>/` 目录，可与历史报告对比。
6. 评测阈值校准基准（与代码现状一致）：重排过滤 `>= 0.15`（`RERANK_FILTER_THRESHOLD`，2026-09-19 H-07 由 0.25 放宽；此前 0.3→0.25 为 9/18 第一次放宽）、检索 top 8（`MAX_RETRIEVAL_DOCS=8`）、切分 800/100（`CHUNK_SIZE/CHUNK_OVERLAP`）、MMR 默认关闭（`MMR_ENABLED=False`，fair 评测证伪无增益）、RRF 路级权重（子查询降权 0.4）、
   缓存 rerank 命中 `CACHE_RERANK_HIT_SCORE=0.5`、KNN 候选生产默认 3（`recall@3 61.1%`，瓶颈项）、限流 30 次/60s、工具语义阈值 `TOOL_DISTANCE_THRESHOLD=0.6`、`TOP_FILTER_TOOLS=12`、`temperature=0`（路由/人格评测固定）。

## 实测记录（2026-09-23 · 28 条 probe 集全量重建 + LLM-judge）

- **背景**：评测集升级为 28 条自建 probe 集（`resources/knowledge-base/test-qa/_probe_question_pool.json`，`--limit 28`），取代 21 条旧集成为现役生成端口径；`eval_ragas_judge.py --dataset ... --output ragas_judge_probe_after_rebuild.json` 全量重建后实测。
- **E8 生成质量（LLM-judge 五指标）**：**context_precision 0.6593 / context_recall 0.7432 / faithfulness 0.9464 / answer_relevancy 0.9929 / answer_correctness 0.7575**（28 条，DeepSeek temp=0）。生成端高（faithfulness/answer_relevancy ≥0.94），短板在检索端 context（precision 0.66 / recall 0.74）——与 E7 检索口径互相印证。
- **E7 检索端（probe 集专项报告，2026-09-23 reports）**：`probe_report.json` / `probe_no_rewrite` / `probe_no_rerank_sort` 三份对照 + `retrieval_eval_rrf_weights` 系列（RRF 路级权重 0.4 落地前后对照）；probe 集跑分低于项目集（泛化提问更接近真实用户，检索难度更高）。
- **口径纪律**：21 条集（H-07 P3）与 16/17、94.12% 等全部标记为历史口径，**不得与新口径混用**；对外只报 28 条现役值。

## 实测记录（2026-09-18）

| 维度 | 结果 | 说明 |
|---|---|---|
| E3 工具装配 | 6/6 通过（100%） | 并集/去重/降级/熔断均符合预期 |
| E4 MCP 安全 | 11/11 通过（100%） | 命令/包名/env/sse-url/type 白名单拦截率 100%，合法配置放行不误伤 |
| E5 工具兜底 | **13/13 通过（100%）** | 异常→提示转换、ENOTDIR 纠正方向、描述截断 200、文档截断、轮次上限常量、**新轮计数归零 / 轮内封顶 / 按轮 vs 会话累计防回退**、**失败熔断 4 条（连续 2 次熔断 / 仅 1 次不熔断 / 失败-成功-失败不熔断 / 节点提示不误判）** |
| E14 CI 回归 | 73/73 通过 | pytest 四个测试文件（19 项 Agent 新增 + 54 项既有），可入 CI |
| E13 在线实测 | health ✓ / 登录 ✓ / SSE ✓ / 登出失效 ✓ / 限流 429 ✓ | 账号 qianyi 实测：SSE 首 token 1348ms、总耗时 2.88s、流纯净无污染；登出后旧 token 401；限流第 30/31 次命中 429 |
| E1 动态路由 | 分类准确率 **94.12%**、检索召回 **88.89%**、误报 0% | 已重写 `CLASSIFIER_PROMPT`：改为按「是否需要外部知识」通用判定（知识库可自定义入库，不绑定主题）。**最近复跑为 16/17**，唯一失败项「为什么 RAG 检索后还需要重排序（Rerank）？」被判无需检索。**⚠️ 对比基准有污染**：该次复跑与 `6ba37ad`（17/17 全对）之间 `CLASSIFIER_PROMPT` 未改动，但检索链路已变（H-07 重切 chunk / `RERANK_FILTER_THRESHOLD` 0.25→0.15），且单次 LLM 温度采样波动不可排除，故此 94.12% **不是对 17/17 的回退**。**（2026-09-22 已按现役口径重跑 37 条并固定 temperature=0，结论见 E1 总览——本行仅作历史记录。）** |
| E6 语义缓存 | 同义命中率 100%（3/3）、误命中率 0%（0/3）、原文命中 100% | 真实 redis-stack（RedisSearch 容器 6379）+ embed + bge-reranker 全链路实测；查询平均 345ms |

### 实测记录补充（2026-09-19 H-07 晚）

- **H-07 RAG 质量优化全包**（`d58d29e`，对应 `h07_p0p1_report.json` + `ragas_judge_report.json`）：
  - **P0 重切 chunk**：`CHUNK_SIZE 300→800`、`CHUNK_OVERLAP 50→100`，重入库后 chunks **405→270**；
  - **P1 放宽召回**：`RERANK_FILTER_THRESHOLD 0.25→0.15`（常量）、`MMR_TOP_SELECT 5→8`、`MAX_RETRIEVAL_DOCS 5→8`、`MMR_ENABLED=False`（fair 证伪关闭）；
  - **P2 生成引用约束**：`llm_node` 检索分支要求事实句标 `[文档 i]` 角标、无依据说"知识库暂未覆盖"；前端 `app.js` 正则转 `<sup class="cite-ref">`；
  - **P3 LLM-as-judge**：新增 `eval_ragas_judge.py`，21 条集五指标 **context_precision 0.6381 / context_recall 0.8005 / faithfulness 0.959 / answer_relevancy 0.9881 / answer_correctness 0.7976**（DeepSeek temp=0）；
  - **结果**：21 条项目集 key_points 单路 **0.7476** / 混合 **0.7119**（H-06 fair 基线 0.4524/0.4524）、boolean avg 单/混均 **0.8095**、median 1.0；延迟混合 avg 5.6s（改写 2.7s 为主）。
- **MMR fair 口径证伪**（`5866ebf`）：MMR on/off 在 filter=0.25 时代 21 条集 key_points 均 **0.4524**、无增益（rerank top5 本身不扎堆），rerank 阶段 374ms→1810ms 变慢——`MMR_ENABLED` 置 False 关闭，拓扑保留节点。

### 实测记录补充（2026-09-19 晚）

- **E7 新增 key_points 事实点口径**（`eval_retrieval.py` `evaluate_key_points`）：把 ground_truth 拆成核心事实点（以能在知识库某 chunk 找到原句为准，`--diagnose` 输出各级候选池丢失明细）。**诊断结论**：原 45 条 test-qa 评测集未入库（405 chunks 中 0 个，GT 与知识库文本同一性为零）——旧 boolean/coverage 天然偏低是**评测集与知识库错位**，非检索链路问题。
- **双轨制 B 项目专属评测集**（`eval_project_dataset.json` 21 条，以真实入库内容 01~10.md 为唯一出题源，88 个 key_points 逐一 grep 反作弊）：实测 key_points 单路 **0.5690** / 混合 **0.5357**、全中 5/21、boolean median 升至 1.0；**暴露 filter 0.25 的真实召回损失**（混合 < 单路，本应命中的文档被过滤）。
- **E15 人格路由实测**（`persona_router_eval.py` + `persona_router_eval_report.json`）：16 条典型表达（四类各 4）**16/16=100%**，混淆矩阵对角线全满、errors 空；报告诚实标注「典型样本、边界模糊用例未覆盖、100% 为乐观基线」。

### 实测记录补充（2026-09-18 晚）

- **E7 检索 recall 口径已改 boolean**：`eval_retrieval.py` 新增 `--metric boolean|coverage`（默认 boolean，句级要点覆盖：句子关键词命中≥60%、覆盖句占比≥50% 即 1），并修复过滤后不足 5 条按 RRF 顺序补足的口径问题；实测 45 条：单路 avg **0.3111** / 混合 avg **0.2667**（阈值 0.25）。
- **E2 工具筛选已实测**：22 条用例 / 41 工具 / top_k=12，`avg_recall=0.8939`、`avg_precision=0.1406`、`zero_hit=0`；脚本含本地化 MCP 配置适配（`_localize_mcp_configs`）与显式关连接修复，`cd src && python -m agent_test.evaluate_tool_filter --max-cases 22` 可复现。

### 实测记录补充（2026-09-19 深夜）

- **E5 扩充至 9 条并全绿**（`a4e1bd4`）：新增 3 条"按轮计数"回归用例（新轮起点归零 / 轮内累计达上限 / **按轮 vs 会话累计防回退锁**）。第 3 条同时计算新旧两种口径并断言二者分道扬镳，用于锁死代际语义——把计数改回"整个会话累计"会立刻变红。另修正既有第 5 条长期挂红的期望值（`MAX_RETRIEVAL_DOCS` 5→8，H-07 P1 放宽时漏改评测集）。结果：**9/9 通过（100%）**，此前 7/9。

### 实测记录补充（2026-09-19 深夜 · E5 失败熔断 13/13）

- **背景（线上实测截图）**：用户问「如何构建项目」，模型连续调用 `sequentialthinking`，每次都超时失败，直到 8 次轮次额度耗尽才被强制停止，回复里说"工具调用次数到上限啦"——**用户拿到的是一句认输，不是一个答案**。
- **根因**：原有两道防线（次数上限 8 / 连续**同参**重复检测）都拦不住这个形态。`sequentialthinking` 每次调用的 `thought` 参数都不同（step1/step2/…），所以"同参重复"永远命中不了；而次数上限是兜底，触顶时额度已经烧光了。**缺的是"这个工具已经坏了，别再试了"这一层判断。**
- **改法**：新增第三道防线 `_failed_tool_names()`——按本轮统计各工具**连续失败**次数（成功即清零），连续失败达 `MAX_TOOL_FAILURES=2` 后从本轮可 bind 列表里摘掉该工具。是**摘工具**而非**停整轮**：坏工具摘掉后其余工具仍可用，模型能换路走。
- **用例（4 条，`failure_circuit_breaker` 维度）**：连续 2 次触发熔断 / 仅 1 次不熔断（留重试机会）/ **失败→成功→失败不算连续**（防止把"偶尔抖动"误杀）/ **节点注入的"次数已达上限"提示不被误判为工具失败**（否则熔断会自我强化，每轮多禁一个）。
- **效果**：同一份"5 次超时"历史，熔断在第 **2** 次失败后即生效，**省下 6 次**轮次额度给可用工具。
- **同期修复的编造问题**：截图里模型还说了"上一条请求里我已经把它跑完了——5 步思考链都跑通了"，这是**把没成功的事讲成已做完**。system prompt 原有「不编造」针对的是知识库内容，没覆盖"工具执行结果"，已补一条明文禁令。

### 实测记录补充（2026-09-19 21:0x · E1 复跑波动）

- **E1 复跑 16/17（94.12%）**：`routing_eval_report.json` 由 17/17 回落，唯一失败项为「为什么 RAG 检索后还需要重排序（Rerank）？」，被 `CLASSIFIER_PROMPT` 判为无需检索。17 条用例集本身未变（9 检索 + 8 非检索），`CLASSIFIER_PROMPT` 自 `6ba37ad` 起未再改动。
- **归因（不下定论）**：变量不唯一——① 两次复跑之间 RAG 链路已变（H-07 重切 chunk、`RERANK_FILTER_THRESHOLD` 0.25→0.15）；② **已核对 `src/init.py:15`，`model` 未固定 `temperature`**（默认采样），意图分类为单次 LLM 调用、同 prompt 存在采样波动；③ 检索召回率 88.89% 与分类准确率 94.12% 同值，恰为同一条用例同时挂掉两项所致，非两处独立退化。因此**不能判定为"路由能力回退"**。
- **后续**：需在 `init.py`（或评测脚本内单独构造 model）固定 `temperature=0` 并重复 3 次取众数，才能区分"波动"与"回退"；在此之前 `routing_eval_report.json` 的落盘结果仅作单次快照，不作结论。**本项已登记为待办。**
