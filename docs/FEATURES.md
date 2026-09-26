# 功能特性与工程亮点

> 本文档承接根 README 的「功能特性」与「工程亮点速览」完整内容，供深入理解与开源读者参考。

## 功能特性

- **统一意图路由**：`router_node` 一次 LLM 调用同时输出 `{persona, need_retrieval}`，按需走检索链路，避免无谓延迟；闲聊/自我介绍命中正则时直接短路、跳过 LLM 调用，进一步压低首 token 延迟
- **多人格路由层**：4 个对话人格（帽子 cappie 默认 / 善良 kind / 疯狂 crazy / 短发 manager）由每轮 `router_node` 分发；前端手选时 persona 直接用用户值（同一次调用只判 need_retrieval）；人格 prompt 无条件叠加进 System Prompt（语气层，不推翻事实层）；**按人格配置工具白名单**（善良 23 个纯只读 / 短发加 git 只读 4 个 / 疯狂零工具走裸模型分支）；配 chibi 袖珍分身概率性后置吐槽（30%，SSE 独立事件不进主消息流）
- **RAG 增强检索**：查询改写（主查询 + 子查询）→ 稠密向量多路召回 + BM25 稀疏检索（RedisSearch）→ RRF 融合去重（**路级权重**：子查询降权 0.4，避免泛化查询稀释主路排名）→ SiliconFlow 在线重排 → 相关性阈值过滤
- **MCP 工具集成**：通过 Model Context Protocol 接入 filesystem、sqlite、sequential-thinking、memory、time、context7、dbhub 等外部工具，并自研本地 **mitta-tools**（git 操作/网络搜索/文件检索，12 个工具）；工具常驻事件循环，支持故障降级；**分组 + 分级启动**：第一方 6 台常驻、第三方 context7/dbhub 懒加载（`McpLazyLoader` 闪连预热 schema → 命中触发真实连接 → `DynamicToolNode` 动态路由），节省 150-300MB 内存
- **智能工具筛选**：规则层（tags 关键词命中）+ 语义层（向量检索）并集，每轮只暴露相关工具给 LLM，避免工具过多导致注意力稀释
- **双通道记忆**：
  - 短期记忆：PostgresSaver 按 `thread_id` 恢复多轮对话
  - 长期记忆：PostgresStore 按 `user_id` 保存用户档案（跨会话生效），提取在节点内 daemon 线程 fire-and-forget，不阻塞 SSE 收尾
- **用户自定义 System Prompt**：支持用户在个人信息界面上传自定义设定文件，与默认 Prompt 合并后作用于全局
- **文件上传与解析**：支持上传多种格式文件，上传后立即解析文本内容，发送消息时与用户输入一并送入 LLM
- **知识库增量更新 API**：通过 HTTP 接口向知识库增量上传文档（Chroma 向量 + RedisSearch BM25 双通道自动入库），支持文档列表查询、按来源/按文档删除，无需登录服务器跑脚本
- **流式输出**：`stream_mode=["messages","custom"]` 逐 token 输出，前端打字机效果；工具调用时实时显示加载状态；检索期 ack 预响应：`retrieve_node` 检索前推开场白（按人格 `ACK_OPENINGS`），助手气泡 0 延迟出现 + ragThinking 指示器，首 token 到达即移除
- **断点续传（刷新不中断）**：聊天任务与 SSE 连接解耦，每个思考/工具/正文事件按序号落 Redis List（TTL 7 天）；前端刷新或重连时先 `GET events?after=已消费序号` 重放缺失的增量事件重建界面，再续推新事件，强刷也能恢复思考过程与流式输出，且不会因重发而重复累积对话
- **用户级 MCP 热重载**：MCP 配置存 PostgreSQL 按用户隔离，网页端保存后通过 hash 检测自动重建对话图，`POST /api/mcp/reload` 主动清除缓存立即生效，无需重启后端
- **深度思考**：DeepSeek reasoning_content 流式输出，前端可切换思考开关与推理强度（low/medium/high），思考过程可折叠展开
- **轻量前端**：Vue 3 运行时（CDN 引入 `vue.global.prod.js`，**无构建工具 / 无 SFC / 无 package.json**，原生 JS + 模板字符串驱动），多主题 + 响应式移动端 + 高对比几何切角动效，工具调用记录穿插展示、复制/分享/重新生成；聊天区跳底悬浮按钮（距底 >200px 显示）+ 人格工具范围提示
- **安全认证**：JWT（access 15 分钟 + 隐式 refresh 30 天自动续签）+ bcrypt + 登出即时失效（Redis 删除 token）+ 请求限流
- **节点级缓存**：LangGraph CachePolicy + Redis，memory_node 结果按 TTL 缓存（retrieve/tool 节点缓存已移除，原因见「核心设计说明 → 节点级缓存」）

## 工程亮点速览

- **已上线公网**：`https://www.mittaai.xyz`（HTTPS 域名 + 证书），另有 Tauri 桌面端安装包（Windows，MSI/NSIS）
- **CI/CD 全自动部署**：GitHub Actions「境外构建 → 阿里云 ACR → 服务器拉取」混合方案；回归门禁（73 用例纯函数 pytest）**硬前置**，回归红不构建不部署；镜像变更检测跳过构建（仅源码/前端变更时 **~2-3 分钟**）；知识库**蓝绿入库**自动切换、失败自动回滚
- **Agent 系统评测矩阵 E1–E15**：统一路由 37 条（人格 32/32、意图 **91%~94%**）、工具筛选 recall **0.8939**、MCP 安全拦截 **11/11**、工具失败熔断 **13/13**、语义缓存命中率 **61%~97%**（误命中 0%）、生成质量 LLM-judge 五指标 **0.6593 / 0.7432 / 0.9464 / 0.9929 / 0.7575**（28 条自建评测集，完整矩阵见 `docs/AGENT_EVAL_MATRIX.md`）
- **在线实测**（`eval_online.py` + `eval_sse.py`）：SSE 首 token 分场景（线上，每场景 n=3）shortcut **1608ms** / no_retrieval **1914ms** / retrieval **12348ms**、流纯净度 **18/18**；登出即时失效 401 ✓、限流第 30/31 次正确触发 429 ✓

## 评测与指标速览（现役口径）

> 完整评测矩阵 E1–E15、历史口径演进与执行方式见 `docs/AGENT_EVAL_MATRIX.md`。以下仅为对外引用速览，**冲突时以评测矩阵文档为准**。

| 维度 | 现役指标 |
|---|---|
| 统一路由（E1） | 意图路由 91.43% / 94.29%（两次 temperature=0 重跑，只报区间）；人格四分类 32/32=100% |
| 工具筛选（E2） | avg_recall 0.8939、zero_hit=0（41 工具 / 22 用例 / top_k=12） |
| 工具装配（E3） | 并集召回/降级/熔断 6/6 通过 |
| MCP 安全（E4） | 命令/包名/env/sse/type 白名单拦截率 100%（11/11） |
| 工具兜底（E5） | 截断/异常转换/按轮计数/失败熔断 13/13 通过 |
| 语义缓存（E6） | 隔离会话命中率 97.2%（35/36）；生产默认（12 条+候选 3）61.1%；误命中硬负 0/8 + 跨域 0/6；embedding 调用实测降 12.5% |
| 混合检索（E7） | key_points：单路 0.7476 / 混合 0.7119、boolean avg 0.8095、零空结果（21 条项目集）；RRF 路级权重（子查询降权 0.4） |
| 生成质量（E8） | 28 条自建集 LLM-judge：0.6593 / 0.7432 / 0.9464 / 0.9929 / 0.7575 |
| 记忆（E9） | 生产容器内写 P95 3.01 / 读 P95 2.09 ms（读写 ≤5ms） |
| SSE 流（E12） | 线上每场景 n=3：shortcut p50 1608ms / no_retrieval 1914ms / retrieval 12348ms；流纯净度 18/18 |
| 在线实测（E13） | health/登录/对话/限流 429/登出失效 401 全链路通过 |
| CI 回归（E14） | 73 用例纯函数 pytest 全绿（入 CI 门禁，不含 RAGAS） |
