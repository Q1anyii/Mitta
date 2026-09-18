docs_sync: required

# 多人格 v1 落地（A+B 阶段）：五人格米塔 + persona_router + chibi hook

## 变更概要
按 `docs/PERSONA_MULTIAGENT_PLAN.md` 落地多人格 v1：
- 4 对话人格：cappie（帽子，执行/全量工具）、kind（善良，只读陪伴）、crazy（疯狂，meta 台词层/无工具）、manager（短发，技术审查只读）
- chibi（袖珍米塔）后置 hook：主回答后 30% 概率补一句短吐槽
- persona_router_node：每轮意图路由，手选短路 / 小模型四分类 / 非法兜底 cappie

## 改动文件（8 个）
1. `src/constant/persona_constant.py`（新）：5 段 prompt 全文 + PERSONAS 注册表 + get_persona()
2. `src/graphs/state.py`：OverAllState 加 `persona: Optional[str] = None`
3. `src/schemas/request_schemas/chat_schema.py`：ChatRequest 加 `persona: Optional[str] = None`
4. `src/graphs/nodes/persona_router_node.py`（新）：四分类路由节点
5. `src/graphs/main_graph.py`：START→persona_router_node→classify_node
6. `src/graphs/nodes/llm_node.py`：人格 prompt 注入 + 工具白名单 + 空工具分支区分
7. `src/service/chat_service.py`：stream() 透传 persona_override + _maybe_add_chibi()
8. `src/routers/chat_router.py`：chat 接口传 persona

## 关键设计决策
- **cappie = 现状基线**：persona_key=cappie 时不拼人格 prompt、不过滤工具，严格满足"不传 persona 行为与现状完全一致"硬验收
- **persona 默认 None**：让 router 首轮必跑；旧 checkpoint 无该字段读 None 也能跑
- **白名单套在 ToolFilter 结果之上**：不改 ToolFilter 本身；crazy allowed_tools=[] 走裸模型分支
- **空工具区分**：非默认人格主动无工具时不注入"当前没有可用的工具"系统提示（人格 prompt 自带能力边界）
- **chibi hook**：SSE 流结束后 30% 随机，复用 deps.model，异常静默，追加 `{"chibi": text}` 事件

## 验收要点（B.3）
- 不传 persona = 现状行为完全一致
- 4 人格切换日志 `[persona] key=... tools_selected=...`
- 手选合法值短路不调 LLM；非法值兜底 cappie
- 自动分类日志含 `persona_router_token`（usage_metadata）

## 未做（后续迭代）
- 前端人格 tab + chibi 小气泡渲染（CDN SPA，目录未定位）
- kind/manager 白名单工具名与真实 MCP 工具名首次启动需看日志核对
