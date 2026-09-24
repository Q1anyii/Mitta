# 核心设计说明

> 本文档承接根 README 原「核心设计说明」章节的完整内容，供面试深挖与排障参考。涉及指标数字以 `docs/AGENT_EVAL_MATRIX.md` 与实测报告为准。

## 记忆体系

| 类型 | 存储 | 隔离维度 | 生命周期 |
|---|---|---|---|
| 短期记忆 | PostgreSQL Checkpointer | thread_id | 会话级，可恢复 |
| 长期记忆 | PostgreSQL Store | user_id | 跨会话持久 |
| 节点缓存 | Redis | 输入哈希 | TTL 10~900 秒 |
| 检索缓存 L1 改写 | Redis STRING（JSON） | `rw:{prompt_ver}:{hash(问题‖历史)}` | TTL 24h（跨用户共享） |
| 检索缓存 L2 向量 | Redis STRING（float32 二进制） | `emb:{model_tag}:{hash(文本)}` | TTL 7d（跨用户共享） |
| 检索缓存 L3a 精确 | Redis STRING（JSON） | `rcache:x:{kb_ver}:{hash(问题)}` | TTL 900s 命中续期（跨用户共享） |
| 检索缓存 L3b 语义 | Redis + LSH 分桶 | thread_id + bucket_id | TTL 900s 命中续期 |
| BM25 索引 | RedisSearch HASH | doc_id | 持久化，知识库重建时重建 |
| 登录态 | Redis | user_id | access 15 分钟 / refresh 30 天 |

## MCP 工具常驻事件循环

MCP 工具通过 `langchain_mcp_adapters` 加载为 async 工具，闭包捕获绑定创建时事件循环的 `ClientSession`。同步图（ToolNode）在线程池执行时会临时新建事件循环，跨循环调用 session 会失败/挂起（Windows 下 mcp 库 cancel scope 泄漏还会注入 CancelledError 中断整图）。解决方案：

- 启动时创建专用守护线程运行独立事件循环（`mcp-tool-loop`），MCP 连接建立与工具调用全部提交到该循环（`asyncio.run_coroutine_threadsafe`）
- `make_sync_tool` 将 async 工具包装为同步 StructuredTool，含 30 秒调用超时，超时由 ToolNode 转错误消息，不中断对话链路
- 单个 MCP 服务器连接失败不影响其他服务器（120 秒连接超时 + 故障降级跳过；冷启动时 uvx/npx 首次需下载依赖，超时过短易导致全部服务器被跳过，故取较大值）
- MCP 工具按服务器名注入 tags（`SERVER_TAGS` 映射）、按工具名注入精准 tags（`TOOL_TAGS`），供工具筛选规则层命中并按强弱排序
- 关闭时按序在工具循环内释放 MCP 子进程连接，避免资源泄漏

### 分组 + 分级启动（内存优化）

| 组 | 服务器 | 启动方式 | 目的 |
|---|---|---|---|
| 第一方（常驻 6 台） | filesystem / mittatools / sqlite / sequential-thinking / memory / time | 启动即连接 | 核心能力开箱即用 |
| 第三方（懒加载） | context7 / dbhub | `McpLazyLoader`：启动仅闪连预热 schema → 首次命中才真实连接 → `DynamicToolNode` 动态路由 | 节省 150-300MB 内存（uvx/npx 子进程），低配服务器可跑 |

## 已知故障模式与排查要点

线上出现过两类「工具在但不可用」的故障，排查的关键区分是**「工具不在」还是「工具在但坏了」**：

| 现象 | 根因 | 排查入口 | 处置 |
|---|---|---|---|
| 模型答「没有网页抓取工具」 | `mcp_servers.json` 里 `cwd` 指向空目录，`client.py` 脚本预检失败 → 整个 server 被 warning 跳过（工具全缺） | 启动日志搜 `MCP 服务器脚本不存在`；核对实际注册工具数 | `cwd` 改镜像代码根 `/app`；只改挂载配置，CI rsync 同步 + 重启 api 即可 |
| 模型答「抓取组件缺少依赖模块 `No module named 'bs4'`」 | 延迟 import 的可选依赖未列入 `requirements.txt`；server 启动正常、工具照常注册，只有真调用才报错 | 健康检查看不出问题；需直连工具函数试调一次 | `requirements.txt` 补依赖 → **必须 CI 重建镜像**才生效 |

> 延迟 import 的可选依赖目前没有启动期校验，只靠 `requirements.txt` 注释提醒；如需彻底防复发，可在 `main.py` lifespan 里对可选依赖做 try import 并暴露到 `/health`。

## 智能工具筛选

每轮对话时，`ToolFilter.select_tools(query, tools)` 执行两层筛选并集，只把候选工具暴露给 LLM：

1. **规则层**：检查工具 `tags`（如 filesystem 工具含 `["文件","目录","读写","file"]`），query 中包含关键词即命中，零延迟
2. **语义层**：工具描述向量化存入向量库 `MCP_TOOLS` 集合，用 query 做语义检索（top_k=12，距离阈值 0.6），失败自动熔断降级为纯规则层
3. 两层结果按工具名去重并集；无命中时不 bind 空列表（OpenAI 兼容 API 会 400），改用裸模型并注入"无工具可用"提示
4. 多轮指代增强：输入含"继续/刚才/那个"等指代词时，拼接最近一轮 AI 回复前 200 字符辅助筛选

## 节点级缓存（LangGraph CachePolicy + Redis）

> 注：retrieve_node 和 tool_node 缓存已删除，原因：
>
> 1. 子图 retrieve_graph 内置缓存机制，外层设置缓存目的减少一次子图创建，后续可把子图缓存机制抽出
> 2. tool_node 的缓存 key 不带 tool_call_id，若缓存复用影响 ToolMessage 导致工具调用失败

LangGraph `CachePolicy` 配合 `RedisCache`，在图编译时注入，节点结果按 TTL 缓存到 Redis：

| 节点 | 缓存键 | TTL | 策略 |
|---|---|---|---|
| memory_node | 消息轮次+输入+AI回复（仅 executed/unavailable 轮） | 10s | idle 闲聊轮返回随机键永不命中 |

缓存键自定义设计：默认 key_func 对节点输入整体 pickle 哈希，而 Send payload 含每轮变化的 messages，会导致缓存键每轮都变、永不命中。自定义 key_func 只取稳定部分（用户输入/工具参数），确保缓存可命中。

## 检索缓存四层（CacheService + Redis Search + LSH）

除 LangGraph 节点级缓存外，`CacheService` 基于 Redis 构建了**四层缓存**。原实现只有一层「检索结果语义缓存」，问题在于它**每次命中都要先跑一次 embedding** 才能查 LSH 桶——省下的只是重排和召回，embedding 开销一分没省，延迟收益被砍半、embedding 调用成本完全没降。因此按「谁依赖谁」拆成四层，让每一层单独可命中：

| 层 | 缓存内容 | 缓存键 | 命中条件 | 跨用户共享 | TTL |
|---|---|---|---|---|---|
| **L1 改写** | LLM 查询改写结果 | `rw:{prompt_ver}:{sha256(norm(问题)‖norm(历史))[:32]}` | 归一化后问题+历史完全相同 | ✅ 全局共享（不含用户态） | 24h |
| **L2 向量** | bge-m3 文本向量 | `emb:{model_tag}:{sha256(norm(文本))[:32]}` | 归一化后文本完全相同 | ✅ 全局共享 | 7d |
| **L3a 精确结果** | 完整检索结果（含重排分） | `rcache:x:{kb_ver}:{sha256(norm(问题))[:32]}` | 归一化后问题完全一致 | ✅ 全局共享 | 900s（命中续期） |
| **L3b 语义结果** | 完整检索结果（含重排分） | `retrieve_cache:{thread_id}:{bucket_id}`（LSH 分桶） | LSH 桶内 + rerank ≥ 0.5 | ❌ 按会话隔离 | 900s（命中续期） |

**归一化**（决定 L1/L2/L3a 能否跨用户）：全角空格→半角、trim、连续空白折叠为单空格、ASCII 转小写。`"Redis  是什么？"`、`"Redis 是什么？ "`、`"redis 是什么？"` 三个写法会落到同一个 key。

**为什么 L3b 不能省掉 rerank**：bge-m3 原始 query 向量对短问题区分度差（正是项目要上 rerank 的原因），只用向量 KNN 会把「怎么部署」和「怎么回滚」判成一条。所以 L3b 的 rerank **是命中判据本身，不是可选优化**，真正能省掉 rerank 的是 L3a（文本 hash 精确匹配，无需任何语义判断）。

**L3b 两段式验证**：先取 KNN 近邻 top `CACHE_KNN_FAST_K=3` 精排，分数 ≥ `CACHE_RERANK_STRONG_HIT=0.7` 直接判命中（强命中快通道，绝大多数命中走这条）；否则才把整个桶的候选全量精排，≥ `CACHE_RERANK_HIT_SCORE=0.5` 判命中。未命中一律走完整 RAG。

**失效策略**：

| 变更 | 失效范围 |
|---|---|
| 知识库重灌/切 chunk | `MITTA_KB_VERSION` 自增 → L3a、L3b **全部**失效 |
| 改写 prompt 改版 | `MITTA_REWRITE_PROMPT_VER` 自增 → L1 全失效 |
| 换 embedding 模型 | L2 key 自带 `model_tag`，新旧并存不串味，老条目自然过期 |
| 时间 | L1 24h / L2 7d / L3 900s 动态续期 |

> 已知限制：L3a 是跨会话共享的，而 `clear_thread_cache` 目前按 thread 清理，会误删本可共享的条目，需改为按 key 清理。

**各层实际收益**（本地 redis-stack 实测 / 分项推算）：

| 层 | 命中后省掉什么 | 实测/推算收益 |
|---|---|---|
| L1 | 一次 LLM 改写调用 | 改写分项 2226 ms → ~0 ms（重复提问场景） |
| L2 | 一次 embedding 调用 | **首次 469 ms → 二次 2 ms**（同文本，1024 维，向量逐位一致） |
| L3a | embedding + 4 路召回 + RRF + 重排，**全链路** | **~1 ms**（0 embedding / 0 rerank，直接反序列化返回） |
| L3b | 4 路召回 + RRF + 部分重排 | 高并发下把整条 RAG 流水线降到「1 次 embedding + 3 条候选重排」 |

**降级策略**：Redis 不可用（或 `CACHE_LAYER_ENABLED=0`）时静默降级为不缓存，不阻塞检索主链路。

⚠ 部署注意：Windows 下 Redis 必须用 `127.0.0.1` 而非 `localhost`——`localhost` 会解析到 IPv6 `::1`，而 redis-stack 只监听 IPv4，报错 10054 后 **BM25 会静默退化为空召回**（不抛异常，极难发现）。**修复**：入库脚本 ingest_knowledge.py Step4 原本只 HSET 写内容、漏调 cache_service.create_sparse_index()，导致 RediSearch 上根本没有 kb_bm25 索引、FT.SEARCH 恒空；补一行幂等建索引后稀疏路从恒空恢复为正常召回，双路互补真实成立。

## 流式输出与工具调用状态

- 使用 `stream_mode=["messages", "custom"]` 捕获图中所有 LLM token 事件与自定义事件；按 `meta["langgraph_node"]` 过滤只输出 llm_node 的增量，custom 通道承载 `retrieve_node` 的 ack 预响应结构化 dict
- SSE 事件类型：`content`（文本 token）、`ack`（检索期开场白预响应）、`tool_call_start`（工具名+参数）、`tool_call_end`（工具名+结果摘要）、`done`（正文流完，只推送不落库，前端立即解锁发送）、`chibi`（袖珍分身吐槽，后台线程异步发送）、`error`（异常）、`[DONE]`（结束）
- 前端监听 `tool_call_start/end` 事件，在 AI 消息下方显示"正在调用工具：xxx"加载条
- 流式模式下 tool_calls 分块传输，通过 `AIMessageChunk.__add__` 合并所有 chunk 提取完整工具调用，避免取最后一个 chunk 导致 tool_calls 为空

## 检索期 ack 预响应

`retrieve_node` 在进入 `retrieve_graph.invoke` **之前**，通过 `from langgraph.config import get_stream_writer` 向 custom 通道推送 `{"ack": text, "persona": persona}`（0 LLM 调用；writer 不可用静默降级）。文案来自 `src/constant/ack_constant.py` 的 `ACK_OPENINGS`（按 persona crazy/kind/cappie 分组 + `DEFAULT_ACK`），`pick_ack_text(persona)` 选取。前端 `onAck` 把开场白立即作为助手消息初始值（检索期间助手气泡 0 延迟出现）+ `ragThinking` 指示器，首个正文 token 到达后移除。断点重放 `_applyEventsToMsg` 也处理 `ev.ack`，刷新后开场白与正文连贯不拆条。

## 记忆异步化

`memory_node` **保留在主图内**（`route_after_llm` 无 tool_calls 仍走它），但真正需要提取的轮次，把「读 store 档案 → LLM 提取/合并 → 用户名行正则兜底 → store.put」整体包进内联 `_extract_and_persist()`，用 `threading.Thread(target=..., daemon=True).start()` 后台执行后**节点立即返回**——`graph.stream` 随即结束、`done` 事件先行，长期记忆 LLM 提取（1~3s）不再压在图流末尾阻塞收尾。chibi 同步改为 `_chibi_async` 后台线程，SENTINEL 移入该线程 finally，保证 `[DONE]` 在 chibi 事件后发出。

## 文件上传与解析

1. 前端上传文件 → `POST /api/chat/upload` → 保存到 PostgreSQL `user_files` 表（base64 编码，单文件上限 10MB）
2. 保存后立即调用 `chat_service.parse_and_cache_file()` 解析文本（阻塞执行，接口返回即解析完成）
3. 解析结果缓存到内存 `_file_content_cache`（key=`{user_id}:{file_id}`），避免重复解析
4. 发送消息时前端传 `file_ids` → 后端从缓存读取文件内容 → 以"【文件名】+内容"格式拼接到 `input_str` → 传入 LLM
5. 支持 txt/md/csv/json/py/js 等纯文本格式（UTF-8/GBK 编码兼容）；PDF 使用 PyPDFLoader 解析；不支持的格式返回 `parsed=false`
6. 删除文件时同步清除解析缓存

## 安全设计

项目在认证、工具调用、输入处理与部署四个层面做了分层防护：

**认证与会话**

- JWT 双 token：access 15 分钟过期、refresh 30 天；refresh 只存 Redis 不下发前端，access 过期时后端用 refresh 静默续签（对前端透明）
- refresh token 带 `jti`+`iat` 轮换，续签继承绝对过期时间**不滑动**，杜绝无限续签
- 密码 bcrypt 哈希（72 字节截断）；注册与找回新密码要求 8–64 位且字母数字混合
- 密码找回走一次性验证码：Redis TTL + `GETDEL` 用后即焚，SMTP 凭据从环境变量读取
- 登录/找回端点按 IP + userId 双维度计数，失败指数退避；聊天主接口按 JWT sub 做滑动窗口限流（30 次/60s），Redis 不可用自动降级内存窗口
- 会话归属 fail-closed：非本人 thread_id 一律 403，统一走 `verify_thread_access`

**MCP 与工具调用**

- `/mcp` 管理端点挂 JWT 鉴权中间件，`user_id` 服务端从 token 强制解析，不信任请求体里的字段，防越权调用他人 MCP 配置
- stdio 启动命令白名单 + 包名白名单，显式拒绝 `-c` / `-e` / `-m` / `--require` 等直接执行代码的 flag
- SSE 内网地址校验走 `ipaddress` 解析 + `getaddrinfo`，封堵 `127.1`、`[::1]`、十进制/八进制 IP 绕过，解析失败 fail-closed
- `read_local_file` 用 `Path.resolve()` 前缀校验，防 `../` 目录穿越
- MCP 配置文件路径白名单，限制在项目 `resources/`、`config/` 与用户主目录

**输入与前端**

- 前端渲染 LLM 输出前过 DOMPurify，封堵 `v-html` 注入偷 JWT
- 文件上传扩展名白名单（不含 `.html` / `.svg`），图片类按 magic bytes 校验文件头，堵 `.exe` 改名 `.png`
- SPA 静态兜底 `resolve()` 后校验仍在前端目录内，越界 404
- 全局异常处理器：日志记全栈，对客户端只返通用错误

**部署与运行时**

- Dockerfile 非 root 运行（appuser）
- docker-compose 所有中间件端口绑 `127.0.0.1`，不暴露公网
- 数据库 / 对象存储凭据强制从 `.env` 注入，无默认弱口令
- 脚本与评测代码不硬编码密码，统一读环境变量
- 编码前安全检查清单见 `docs/SECURITY_INPUT_CHECKLIST.md`

## 用户级 MCP 热重载

MCP 配置从「全局文件 + 重启生效」升级为「PostgreSQL 按用户存储 + 运行时热重载」：

- **存储隔离**：`user_mcp_servers` 表按 `user_id` 存储，每个用户独立配置，互不影响
- **自动重建**：`ChatService._user_graph_cache` 以 `(config_hash, graph, mcp_connections)` 缓存用户图，每次对话调用 `_get_user_graph(user_id)` 时计算配置 MD5，hash 变化则关闭旧 MCP 连接、建立新连接、重建 LangGraph
- **主动重载**：`POST /api/mcp/reload` 主动 pop 缓存条目并关闭旧子进程连接，让配置立即生效（不等下一条消息的 hash 检测）
- **安全校验**：保存时校验命令白名单、包名白名单、Windows→Linux 路径自动转换、filesystem 目录隔离、禁止敏感环境变量、sse 禁止内网地址
- **降级策略**：用户 MCP 连接失败时静默降级为全局工具，不阻塞对话

## 深度思考（reasoning_content）

DeepSeek 模型返回的 `reasoning_content`（思考过程）在 langchain_openai 的标准解析中会被丢弃。通过 `utils/deepseek_patch.py` monkey-patch `langchain_openai.chat_models.base` 的消息解析逻辑，将 `reasoning_content` 补回 `AIMessage.additional_kwargs`，经 SSE 流式推送到前端：

- 前端可切换「深度思考」开关与推理强度（low/medium/high），状态持久化到 localStorage
- 思考过程以折叠面板展示在 AI 回复上方，点击展开/收起，流式更新时自动滚动到底部
- 思考内容不参与最终回答，但可帮助用户理解模型推理链路
