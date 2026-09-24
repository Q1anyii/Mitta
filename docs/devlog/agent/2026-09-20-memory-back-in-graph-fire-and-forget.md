# 2026-09-20 正文流完即结束加载态·方案反转：memory_node 回图 + 节点内 fire-and-forget

docs_sync: none（已同步 2026-09-21：项目详解 00 篇主图 memory_node 行、03 篇 §3.8 补 fire-and-forget 段落、05 篇 §3.5/四章第 4 条/Q3/六章口述、08 篇 done 先行+chibi 异步；根 README 节点表/功能特性/「流式输出与工具调用状态」+新增「记忆异步化」节；项目描述 Bullet B1/B4；提交见下）

## 类别
agent（流式完成态优化 · 方案反转）

## 背景
第一轮实现（commit `21b8318`，devlog：`2026-09-20-done-event-and-memory-out-of-graph.md`）把 memory_node **移出主图**，长期记忆提取改由 chat_service 在图 stream 结束后后台执行。用户实测后**否决该方案**，明确指令"回退 memory 全量移除、不拆分 memory、前端不用补"，要求按 handoff `H-20260920-01`「改动点 A」的既定路线重做：**memory_node 保留在主图内**，改为节点内部 fire-and-forget（长期记忆 LLM 提取 + store.put 放后台线程、节点立即返回），chibi hook 异步化、done 事件先行。前端（app.js/style.css）保持第一轮状态不动（用户已认可）。

根因（本轮优化目标）：memory_node（长期记忆 LLM 提取 1~3s）与 chibi hook（30% 概率再调一次 LLM）原本同步压在同一条 SSE 流末尾，导致正文字打完后转圈仍转几秒才解锁发送按钮。

## 改动
1. `src/graphs/main_graph.py`：**恢复 memory_node 入图**——docstring 改回含 memory_node 的图流程并注明二轮 fire-and-forget 方案；恢复 `CachePolicy`、`memory_node/_memory_cache_key`、`CACHE_MEMORY_NODE_TTL` 导入；`memory_node_bound = partial(memory_node, store=store, model=model)`；`add_node("memory_node", ..., cache_policy=CachePolicy(ttl=CACHE_MEMORY_NODE_TTL, key_func=_memory_cache_key))`；条件边改回 `["tool_node", "memory_node"]` + `add_edge("memory_node", END)`（对照 `git show 21b8318^` 原始形态）。
2. `src/graphs/nodes/routes.py`：`route_after_llm` 无工具分支**回退返回 `"memory_node"`**，删除 `from langgraph.constants import END`。
3. `src/graphs/nodes/memory_node.py`：**内部 fire-and-forget 改造**——函数内 `import threading`；快速路径（`tool_status in ("idle","unavailable")` 且无检索）保留同步跳过；真正需要提取的轮次把「读 store 档案 → LLM 提取/合并（MEMORY_EXTRACT_PROMPT）→ 用户名行兜底 → store.put」整体包进内联 `_extract_and_persist()`，`threading.Thread(target=..., daemon=True, name=f"memory-bg-{user_id[-8:]}").start()` 后节点立即返回。异常 catch + `logger.error` 静默，不影响主图与 SSE 流。
4. `src/service/chat_service.py`：
   - `_run_graph` 内务段：删掉 `_persist_long_term_memory(...)` 调用及"memory_node 已移出图"注释，保留先 `event_queue.put(_format_sse({"done": True}))`；chibi 改为后台线程 `_chibi_async`（try 内 `_maybe_add_chibi` → 有文本则落库 `_append_thread_event` + 推 SSE；except `logger.debug` 静默；**finally 里 `event_queue.put(_SENTINEL)`**），`_threading.Thread(daemon=True, name=f"chibi-{thread_id[-8:]}")` 启动——SENTINEL 移入该线程 finally 保证 [DONE] 在 chibi 事件之后才发，SSE 连接关闭前 chibi 一定能送达（验收 2"后处理不丢"）。
   - **删除 `_persist_long_term_memory` 方法**（memory 已回图，不再需要）。
   - `invoke`：删除 `graph.invoke` 之后的 `_persist_long_term_memory(user_id, thread_id, config, graph)` 调用。
   - 异常分支（error+SENTINEL）与 finally（注册表注销 `_active_generations`）保持不动。

## 根因/结论
- 第一轮"移出主图"虽达到 done 先行，但偏离 handoff「改动点 A」明确指定的实现路线（"选这个实现而不是把 memory 移出主图：保留图结构和 checkpointer 兼容，只动 memory_node 内部"），且需要 chat_service 用 `graph.get_state` 补 long-term 持久化、引入双入口维护成本。
- 二轮方案：memory_node 保留在图内，但把最耗时的 LLM 画像提取移入 daemon 线程，节点函数快速返回 → `graph.stream` 立即结束 → done 事件先行；记忆入库链路（短期 checkpointer + 长期 store.put）与第一轮前完全一致，无入口分叉。

## 验证
- `ast.parse`：main_graph / routes / memory_node / chat_service 全部通过。
- `import`：`graphs.main_graph`、`graphs.nodes.routes`、`graphs.nodes.memory_node`、`service.chat_service` 全部通过。
- 行为断言：routes 无工具分支返回 `"memory_node"`；main_graph 含 `memory_node` 节点 + CachePolicy + `add_edge("memory_node", END)`；memory_node 含 `threading` fire-and-forget；`ChatService` 无 `_persist_long_term_memory` 属性。
- 运行时冒烟（工具回环对话、刷新续接、done 时序）待用户起服后补。

## 待办（交文档撰写 Agent）
- 第一轮 devlog `2026-09-20-done-event-and-memory-out-of-graph.md` 描述的是"移出主图"方案，与二轮相反——README / 项目详解 / docs 中"memory_node 在图内"与 SSE 事件类型的描述需按二轮方案校正（图结构含 memory_node、内部异步化、done 先行、chibi 后置）。
