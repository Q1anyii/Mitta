# 目录结构

> 本文档承接根 README 原「目录结构」章节。根目录为 `Mitta/`（仓库 `Q1anyii/Mitta`）。

```
Mitta/
├── src/                                  # 后端源码
│   ├── main.py                           # FastAPI 入口：lifespan 资源管理 + 路由注册 + 全局异常
│   ├── init.py                           # 模型/Embedding/重排/System Prompt 初始化
│   ├── config.py                         # 环境变量加载/校验 + MCP/向量库配置文件管理
│   ├── constant/                         # 常量定义（按模块分类）
│   │   ├── cache_constant.py             # Redis 缓存/向量索引/Token key/节点 TTL
│   │   ├── embedding_constants.py        # 集合名/切分参数/模型名
│   │   ├── prompt_constants.py           # 记忆提取/意图分类提示词
│   │   ├── retrieval_constants.py        # TOP_K/距离阈值/RRF 参数/改写提示词
│   │   └── tool_constant.py              # 工具集合名/筛选 top_k/距离阈值
│   ├── context/
│   │   └── user_context.py               # CtxUser 请求级用户上下文
│   ├── graphs/                           # LangGraph 图定义
│   │   ├── main_graph.py                 # 主对话图：router→retrieve/llm→tool→memory
│   │   ├── retrieve_graph.py             # RAG 子图：cache→parallel_retrieve→rerank→filter→output
│   │   ├── routes.py                     # 意图路由分支（if/else，非 Send）
│   │   ├── state.py                      # GraphState 定义
│   │   ├── tool_filter.py                # 工具筛选：规则层 + 语义层
│   │   ├── utils/                        # 图内纯函数
│   │   │   ├── llm_circuit.py            # LLM 工具循环防护（次数上限/重复检测/硬熔断）
│   │   │   ├── history_repair.py         # 历史消息悬空 tool_calls 修复
│   │   │   └── user_profile.py           # 用户画像并入 system prompt
│   │   └── nodes/                        # 节点实现（router/llm/tool/memory/retrieve_*）
│   │       └── retrieve/                 # 检索子图节点（cache/parallel/fusion/output/query）
│   ├── mcp_client/                       # MCP 客户端
│   │   ├── client.py                     # MCP 连接管理/工具同步包装/故障降级/tags 注入
│   │   ├── mcp_tool_holder.py            # MCP 工具封装
│   │   ├── demo.py                       # MCP 调试示例
│   │   └── mcp_server/
│   │       ├── agent_server.py           # 内置 FastMCP 服务器（chat/get_user/summarize，JWT 鉴权）
│   │       ├── mcp_auth_middleware.py    # /mcp 端点 JWT 鉴权中间件
│   │       └── mitta_tools_server.py     # 本地实用工具集 FastMCP 服务器（git/搜索/文件，12 工具）
│   ├── middleware/
│   │   ├── rate_limit_middleware.py      # 通用限流（Redis ZSET 滑动窗口，验签后取 sub）
│   │   ├── auth_rate_limit.py            # 认证端点独立限流（IP+userId 双维度，指数退避）
│   │   └── request_context.py            # request_id 注入（contextvar + 日志 + 响应头）
│   ├── agent_test/                       # Agent 系统评测（评测矩阵 E1–E15，见 docs/AGENT_EVAL_MATRIX.md）
│   │   ├── ragas_eval.py                 # RAGAS 五项指标评估（E8，LLM-as-judge，不进 CI）
│   │   ├── eval_routing.py               # 动态路由评测（E1：意图分类准确率/检索召回）
│   │   ├── evaluate_tool_filter.py       # 工具筛选规则层+语义层准确率评估（E2，22 条用例 recall 0.89）
│   │   ├── eval_tool_assembly.py         # 工具装配并集/降级/熔断评测（E3）
│   │   ├── eval_tool_safety.py           # MCP 安全校验评测（E4：命令/包名/env/sse 白名单）
│   │   ├── eval_tool_truncation.py       # 工具结果截断与异常兜底评测（E5）
│   │   ├── eval_semantic_cache.py        # 语义缓存命中质量评测（E6：同义命中/误命中）
│   │   ├── eval_retrieval.py             # 检索召回率/延迟评估（E7：单路 vs 混合，key_points 口径 + --diagnose）
│   │   ├── eval_ragas_judge.py           # 生成质量 LLM-judge 五指标（生产链路，28 条自建评测集实测）
│   │   ├── persona_router_eval.py        # 人格路由四分类评测（E15：已并入 E1 统一评测，报告冻结 LEGACY）
│   │   ├── eval_memory.py                # PostgresStore 读写延迟/重复写入减少/对话画像评估（E9，含生产容器内实测）
│   │   ├── eval_rate_limit.py            # 限流拦截准确率/降级耗时/并发压测（E10）
│   │   ├── eval_jwt.py                   # JWT 登录态校验耗时/token 自动续签成功率（E11）
│   │   ├── eval_sse.py                   # SSE 首 token 延迟/流纯净度评估（E12）
│   │   ├── eval_online.py                # 线上全链路实测（E13：health/登录/SSE/登出失效/限流 429）
│   │   ├── eval_cache.py                 # 缓存命中率/Embedding 调用降低/污染率评估
│   │   ├── eval_cache_ttl.py             # 固定 TTL vs 动态 TTL 命中率对比
│   │   └── *_report.json/csv             # 各评估脚本输出的报告
│   ├── routers/                          # FastAPI 路由（按模块拆分）
│   │   ├── deps.py                       # 公共依赖（require_self_or_admin）
│   │   ├── auth_router.py                # 登录/注册/密码找回/登出（Redis 即时失效）
│   │   ├── chat_router.py                # 对话(SSE)/历史/删除/停止/文件上传
│   │   ├── user_router.py                # 个人资料/密码/Prompt/主题/记忆/文件
│   │   ├── knowledge_router.py           # 知识库增量 API（upload/documents/delete）
│   │   ├── mcp_router.py                 # 用户级 MCP 配置读写 + 热重载（/api/mcp/reload）
│   │   └── system_router.py              # 健康检查/认证页面/SPA 兜底（必须最后注册）
│   ├── schemas/                          # Pydantic 请求/响应模型
│   │   ├── request_schemas/
│   │   │   ├── chat_schema.py            # ChatRequest（含 file_ids）
│   │   │   ├── login_schema.py
│   │   │   └── user_schema.py
│   │   └── response_schemas/
│   │       └── login_schema.py
│   ├── service/                          # 业务服务层
│   │   ├── chat_service.py               # 对话编排：用户级图缓存(hash热重载)/流式输出/文件解析缓存
│   │   ├── login_service.py              # 用户登录/注册（PostgreSQL 连接池）
│   │   ├── user_profile_service.py       # 用户扩展信息（头像/风格/Prompt/主题/MCP 配置）
│   │   ├── mcp_config_service.py         # 用户级 MCP 配置（PostgreSQL 存储，按用户隔离，安全校验+路径转换）
│   │   ├── file_upload_service.py        # 文件上传（base64 存 PostgreSQL）/文本解析
│   │   ├── knowledge_service.py          # 知识库增量服务（文档入库/列表/删除，向量+BM25 双通道）
│   │   └── cache_service.py              # Redis 缓存/LSH 向量检索/重排验证
│   ├── utils/                            # 工具函数
│   │   ├── jwt_utils.py                  # JWT 签发/验证/自动续签/登出失效/密码哈希
│   │   ├── response_util.py              # 统一响应格式
│   │   ├── doc_util.py                   # Document ↔ dict 转换
│   │   ├── lsh_util.py                   # 局部敏感哈希（缓存快速过滤）
│   │   ├── tools_util.py                 # 工具安全过滤/向量化/格式化
│   │   └── deepseek_patch.py             # DeepSeek reasoning_content monkey-patch（补回 langchain_openai 丢失的思考内容）
│   └── vector/                           # 向量库抽象层
│       ├── vector_store.py               # VectorStore Protocol + Chroma/Milvus 实现
│       ├── embedding.py                  # EmbeddingProcessor：文档加载→切分→入库
│       └── retrieve_doc.py               # RetrievedDoc 数据结构
├── resources/
│   ├── config/
│   │   ├── vector_db.json                # 向量库配置（type/persist_path/collection）
│   │   ├── mcp_servers.json              # 全局默认 MCP 服务器配置（JSON 数组）
│   ├── frontend/
│   │   ├── index.html                    # Vue 3 CDN 入口（无构建）
│   │   ├── assets/css/style.css          # 设计系统（CSS 变量+切角+动效+响应式）
│   │   ├── assets/js/app.js              # Vue 组件+业务逻辑（模板字符串内嵌，setup/methods）
│   │   ├── assets/js/vendor/purify.min.js # DOMPurify（LLM 输出渲染前消毒）
│   │   ├── nginx.conf                    # Nginx 配置（静态托管+API代理+SSE缓冲关闭+gzip）
│   │   └── favicon.png
│   ├── system_prompt/
│   │   └── default_system_prompt.txt     # 默认 System Prompt（Mitta 角色设定）
│   ├── knowledge-base/                   # 编程知识库（Markdown）
│   │   ├── ingest_knowledge.py           # 知识库入库脚本（向量库 + RedisSearch BM25 双写；支持 --collection/--redis-url 覆盖，供 CI 蓝绿入库）
│   │   ├── cleanup_collections.py        # 清理旧 collection（--keep 显式保留活跃+上一版，删除其余 FAQ_KNOWLEDGE_BASE_*）
│   │   ├── 01~10-*.md                    # 分类知识文档
│   │   └── test-qa/                      # 测试 QA 集（eval_dataset.json 45 条 + eval_project_dataset.json 21 条）
│   ├── FAQ/                              # 在线学习平台 FAQ 知识库
│   └── chroma_db/                        # ChromaDB 持久化目录（Milvus 模式下不用）
├── tests/                                # 单元测试
├── docs/                                 # 项目文档（API.md / DESIGN_NOTES.md / FEATURES.md / DEPLOYMENT.md / PROJECT_STRUCTURE.md / AGENT_EVAL_MATRIX.md / architecture-flowcharts.md / devlog / figures）
├── scripts/                              # 运维脚本
│   ├── migrate_mysql_to_pg.py            # 一次性数据迁移脚本（MySQL → PostgreSQL 存量用户数据）
│   ├── rollback.sh                       # 一键回滚（切指定 short_sha 镜像重启）
│   └── backup_pg.sh                      # PG 每日备份（保留 7 天 + 磁盘水位提示）
├── .env.example                          # 环境变量模板
├── requirements.txt                      # Python 依赖
├── Dockerfile                            # 后端容器镜像
├── docker-compose.yml                    # 一键部署（PostgreSQL+Redis+API+Nginx，ChromaDB 免 Milvus）
└── README.md
```
