# 开发日志（Devlog）

> 记录重要 Bug 修复与关键决策，按时间倒序。

---

## 2026-09-18 · 评测体系：路由 prompt 主题脱节修复 + 语义缓存评测链路打通

### 背景

将 `ragas_test/`（2026-09-20 改名 `agent_test/`）从宽泛的 RAGAS 检索评测升级为覆盖整个 Agent 系统的评测矩阵（E1–E14）。
本轮完成 E1（动态路由）、E6（语义缓存）、E13（在线实测）三项实测，修复两个问题。

### 问题 1：路由分类器主题脱节（E1 揭露）

**现象**：`eval_routing.py` 实测分类准确率仅 47.1%、检索召回率 0%——所有技术概念题
（如"TypedDict 和 BaseModel 的区别"）都被判 `needs_retrieval=False`，直接走非检索路径。

**根因**：`src/constant/prompt_constants.py` 的 `CLASSIFIER_PROMPT` 仍是旧业务路由文案
（"在线学习平台"：账号登录、课程购买、作业提交……），与实际知识库主题
（Mitta 技术文档 01-10 篇：Python 最佳实践、LangGraph、RAG、工程实践）完全脱节。
分类器按业务主题硬编码，知识库换主题后必然失效。

**修复**：重写 `CLASSIFIER_PROMPT` 为通用判定——不再绑定任何业务主题，只按
「问题是否需要外部知识」分类（需要具体知识/事实/数据 → yes；寒暄/闲聊/通用常识/
仅凭对话可答 → no）。注释明确：知识库内容可由用户自定义入库，分类器不得绑定主题。

**验证**：E1 重跑，18/18 用例全对：分类准确率 100%、检索召回 100%、误报 0%、
非检索精确率 100%，平均耗时 1066ms / P95 2216ms。

**经验**：意图分类 prompt 一旦绑定业务主题，就与知识库强耦合；知识库可自定义入库的
系统，分类器必须按"是否需要外部知识"这类与内容无关的通用信号判定。

---

### 问题 2：语义缓存评测连不上 Redis / 环境变量被覆盖（E6）

**现象**：`eval_semantic_cache.py` 连不上 Redis；且传入的 `REDIS_DB_URL` 环境变量不生效。

**根因**：
1. `src/service/cache_service.py:25` 的 `load_dotenv(override=True)` 会**无条件覆盖**
   进程环境变量——任何 `$env:REDIS_DB_URL` 注入都被 `.env` 的 `redis://172.29.183.97:6380`
   覆盖回去，且 6380 是无 RedisSearch 的普通 redis-server，无法建 BM25 索引。
2. Windows 直连 `172.29.183.97:6379`（WSL docker 容器 sorts-redis）在容器重启窗口期
   间歇性 10061/10054。

**修复**：`eval_semantic_cache.py` 改为 `CacheService(redis_db_url=...)` 显式传参
（绕开 `load_dotenv` 覆盖），默认指向 `redis://:sorts_dev@localhost:6379`
（WSL localhost 转发，容器 healthy 后可用）。

**验证**：E6 实测通过（exit 0）：
- 同义改写命中率 100%（3/3），store 693ms、查询平均 345ms
- 无关 query 误命中率 0%（3 条）
- 原文重复命中率 100%

**经验**：`load_dotenv(override=True)` 是评测脚本的隐性依赖陷阱——测试注入的环境变量
会被静默覆盖，评测脚本应直接注入依赖对象而非依赖环境变量。

---

### 问题 3（已确认，非本轮引入）：E13 内置候选账号全部失效

**现象**：内置测试账号（user_01/1234、zhangsan/1234、admin/admin123）线上登录全部 400。

**处理**：用户提供真实账号 qianyi/1234，E13 在线全链路实测通过：
health 200（416ms）、登录成功、SSE 对话（首 token 1348ms、总 2.88s、313 内容块、
无错误事件、无内部日志污染）、登出后旧 token 401、限流第 30/31 次命中 429
（Redis ZSET 滑动窗口 30 次/60s 生效）。

---

### 状态

- 评测矩阵 E1–E14 中仅剩 E2（工具筛选 22 用例离线评估）待跑，需本地启动内置 8 台真实 MCP Server。
- 完整回归 73/73 通过（`tests/test_agent_regression.py` + `test_config` + `test_jwt_utils` + `test_rand_id_util`）。
- git 已提交（6ba37ad），未推送。
