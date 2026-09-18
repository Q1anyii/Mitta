# Agent 系统评测体系（Agent-Level Evaluation Matrix）

> 定位：将原有 `ragas_test/` 从「RAG 检索评测」升级为「**整个 Agent 系统**」的评测体系，
> 覆盖路由、检索、生成、工具装配/安全/兜底、记忆、缓存、限流、认证、在线实测全链路。
> 每个项目描述中的指标都有对应评测，没有指标的也为其定义指标测试。

## 评测维度总览

| 编号 | 维度 | 脚本 | 关键指标 | 对齐的项目描述 | 运行方式 |
|---|---|---|---|---|---|
| E1 | 动态路由 | `eval_routing.py` | 意图分类准确率、检索召回率 | LangGraph 四节点动态路由（检索/非检索分流） | 离线白盒（LLM） |
| E2 | 工具筛选 | `evaluate_tool_filter.py` | recall@k / precision@k | 工具装配规则层+语义层并集召回 | 离线白盒（真实 MCP 工具） |
| E3 | 工具装配降级/熔断 | `eval_tool_assembly.py` | 并集召回、语义层异常降级、熔断生效 | 语义层异常自动降级规则层并熔断 | 离线白盒（mock 向量库） |
| E4 | MCP 安全校验 | `eval_tool_safety.py` | 命令白名单拦截率、包名校验拦截率、敏感 env 拦截率、内网 url 拦截率 | 命令白名单、包名校验、敏感变量拦截 | 纯函数离线 |
| E5 | 工具结果兜底 | `eval_tool_truncation.py` | 异常→ToolMessage 转换率、描述截断生效、文档截断生效 | 工具返回结果长度截断与异常兜底 | 纯函数离线 |
| E6 | 语义缓存 | `eval_semantic_cache.py` | 同义改写命中率、误命中率、embedding 调用降低、延迟对比 | LSH+KNN+reranker 两级判定、embedding 减少 67%、573→350ms | 离线白盒（真实 Redis） |
| E7 | 混合检索 | `eval_retrieval.py` | recall@5（boolean 句级要点覆盖，默认）、P95 延迟、单路 vs 混合 | 单路 0.3111 / 混合 0.2667（boolean）；历史 coverage 中位数 0.77 已标注 | 离线白盒 |
| E8 | RAGAS 五指标 | `ragas_eval.py` | context_precision/recall、faithfulness、answer_relevancy、answer_correctness | RAG 检索增强五大指标 | 离线（LLM 评分，**不入 CI**） |
| E9 | 记忆 | `eval_memory.py` | 写入 P95、重复写入减少 | 长期记忆写入 P95 ≈ 46ms | 离线白盒 |
| E10 | 限流 | `eval_rate_limit.py` | 拦截准确率、Redis 降级内存、路径过滤 | 30 次/60s、Redis 异常降级内存 deque | 离线白盒 |
| E11 | 认证 | `eval_jwt.py` | 续签成功率、校验耗时、并发 | JWT 双 Token 无感续签 100% | 离线白盒 |
| E12 | SSE 流 | `eval_sse.py` | 首 token 延迟、流纯净度 | SSE 流式对话 | 在线 HTTP |
| E13 | 在线实测 | `eval_online.py` | health、登录、对话、限流 429、登出失效 | 线上全链路 | 在线 HTTP（www.mittaai.xyz） |
| E14 | CI 回归 | `tests/test_agent_regression.py` | 路由/安全/兜底/缓存 key 纯函数断言 | 回归守护（**不含 RAGAS**） | pytest（CI） |

## 指标与项目描述的对应关系

- 混合检索 recall@5（boolean 口径：单路 0.3111 / 混合 0.2667）→ E7 `eval_retrieval.py`；历史 coverage 口径（中位数 0.77）已标注为历史产物，不得混用
- 第三方 MCP 免改代码接入（JSON 配置注册）→ E4 `eval_tool_safety.py`（配置校验）+ E2/E3（装配）
- 命令白名单、包名校验、敏感变量拦截 → E4
- 工具返回长度截断与异常兜底 → E5
- 双层记忆（短期按会话、长期按用户 LLM 每轮提取增量合并）→ E9
- 工具装配规则层+语义层并集召回、语义异常降级+熔断 → E2 + E3
- 缓存 LSH+KNN+reranker 两级判定、embedding 减少 67%、573→350ms → E6
- JWT 双 Token 无感续签 100%、登出即时失效 → E11
- 限流 Redis ZSET 滑动窗口 30 次/60s、Redis 异常降级内存 deque → E10
- 动态路由（意图分类→检索→生成→记忆）→ E1

## 执行约定

1. 环境：`conda activate langchain1.2`，`cd src`，`python -m ragas_test.<script>`。
2. 离线白盒脚本直接调用内部模块（需 Redis/Postgres/LLM 可用）；纯函数脚本无外部依赖。
3. 在线实测脚本需要服务器在线（默认 `https://www.mittaai.xyz`）。
4. **RAGAS 五指标（E8）耗时大，只做线下评估，不接入 CI。**
5. 每个脚本输出 `*_eval_report.json` 到 `ragas_test/` 目录，可与历史报告对比。
6. 评测阈值校准基准（与代码现状一致）：重排过滤 `>= 0.25`（`fusion_nodes.py`，2026-09-18 由 0.3 放宽）、
   缓存 rerank 命中 `CACHE_RERANK_HIT_SCORE=0.5`、限流 30 次/60s、工具语义阈值 `TOOL_DISTANCE_THRESHOLD=0.6`、`TOP_FILTER_TOOLS=12`。

## 实测记录（2026-09-18）

| 维度 | 结果 | 说明 |
|---|---|---|
| E3 工具装配 | 6/6 通过（100%） | 并集/去重/降级/熔断均符合预期 |
| E4 MCP 安全 | 11/11 通过（100%） | 命令/包名/env/sse-url/type 白名单拦截率 100%，合法配置放行不误伤 |
| E5 工具兜底 | 6/6 通过（100%） | 异常→提示转换、ENOTDIR 纠正方向、描述截断 200、文档截断、轮次上限 |
| E14 CI 回归 | 73/73 通过 | pytest 四个测试文件（19 项 Agent 新增 + 54 项既有），可入 CI |
| E13 在线实测 | health ✓ / 登录 ✓ / SSE ✓ / 登出失效 ✓ / 限流 429 ✓ | 账号 qianyi 实测：SSE 首 token 1348ms、总耗时 2.88s、流纯净无污染；登出后旧 token 401；限流第 30/31 次命中 429 |
| E1 动态路由 | 分类准确率 100%、检索召回 100%、误报 0% | 已重写 `CLASSIFIER_PROMPT`：改为按「是否需要外部知识」通用判定（知识库可自定义入库，不绑定主题），17/17 用例全对（9 检索 + 8 非检索） |
| E6 语义缓存 | 同义命中率 100%（3/3）、误命中率 0%（0/3）、原文命中 100% | 真实 redis-stack（RedisSearch 容器 6379）+ embed + bge-reranker 全链路实测；查询平均 345ms |

### 实测记录补充（2026-09-18 晚）

- **E7 检索 recall 口径已改 boolean**：`eval_retrieval.py` 新增 `--metric boolean|coverage`（默认 boolean，句级要点覆盖：句子关键词命中≥60%、覆盖句占比≥50% 即 1），并修复过滤后不足 5 条按 RRF 顺序补足的口径问题；实测 45 条：单路 avg **0.3111** / 混合 avg **0.2667**（阈值 0.25）。
- **E2 工具筛选已实测**：22 条用例 / 41 工具 / top_k=12，`avg_recall=0.8939`、`avg_precision=0.1406`、`zero_hit=0`；脚本含本地化 MCP 配置适配（`_localize_mcp_configs`）与显式关连接修复，`cd src && python -m ragas_test.evaluate_tool_filter --max-cases 22` 可复现。
