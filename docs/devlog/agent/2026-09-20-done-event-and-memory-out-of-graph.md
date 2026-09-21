# 2026-09-20 done 事件先行 + memory_node 移出主图（H-20260920-01）

docs_sync: none（已同步 2026-09-21：本方案已被 c5ce743 反转，作为背景记录；项目详解 00 篇主图 memory_node 行、03 篇 §3.8、05 篇 §3.5 均按现役 fire-and-forget 口径更新并标注回退原因；根 README 节点表/流式输出/「记忆异步化」节；简历 Bullet B1/B4；SSE 事件类型补 done/chibi。提交见下）

## 类别
agent（SSE 流协议 / 记忆时机改造）

## 背景
线上正文字打完后转圈还转好几秒才解锁发送按钮：`memory_node`（长期记忆 LLM 提取 1~3s）和 chibi hook（30% 概率再调一次 LLM）同步压在同一条 SSE 流末尾，前端只能等 `[DONE]` 收尾。验收要求：正文结束即结束加载态、后处理不丢、chibi 气泡加头像、工具回环不误判、断连/异常不回归。

## 改动
1. **`src/graphs/main_graph.py` — memory_node 移出主图**：
   - 删除 `memory_node` 导入/节点/边与 `CACHE_MEMORY_NODE_TTL`/`CachePolicy` 死 import（CachePolicy 已无实际使用）；
   - `llm_node` 条件边改为 `["tool_node", END]`，docstring 同步更新。
2. **`src/graphs/nodes/routes.py` — 修复遗留致命 bug**：`route_after_llm` 无工具分支原返回 `"memory_node"`（节点已删，无工具回环时图运行抛 NodeNotFound），改为返回 `END`（`from langgraph.constants import END`）。
3. **`src/service/chat_service.py` — `_run_graph` 改为"done 先行、内务异步"**：
   - 图 stream 正常跑完（= 正文流完）先推 `{"done": True}`（只推送不落库——刷新续接由 generation-status 判定，事件流无需重放 done）；
   - 随后新增 `_persist_long_term_memory()` 后台执行 memory_node 逻辑（get_state → memory_node 复用；store 缺失/状态空/异常均静默，不中断对话）；
   - chibi hook 保持在后（done 后仍可达前端）。
   - **`invoke` 非流式入口同步补**：`a_invoke`（MCP agent server 走此路径）原来依赖图内 memory_node，移出后若不补则 MCP 入口不持久化长期记忆。
4. **前端 `resources/frontend/assets/js/app.js`**：
   - `apiChat` 形参加 `onDone`，SSE 循环 chibi 处理后加 `if (chunk.done && onDone) onDone()`；
   - `sendMessage` 新增 `finalized` + `finalizeReply`（会话校验 → 清工具态 → 重排思考/正文 → 停 loading/streaming → 落库滚动），apiChat 调用传入；流结束/异常收尾路径加 `&& !finalized` 防重（done 与流末尾都收尾时只执行一次）；
   - chibi 气泡竖排 + 36px 圆形头像：`<img class="chibi-avatar" :src="b.avatar">`，`handleChibi` 按 `personaMode` 映射 crazy→mita_crazy.png、kind→mita_kind.png、其他→mita_pajama.png。
5. **`resources/frontend/assets/css/style.css`**：`.chibi-bubble` 改 flex-column 竖排，新增 `.chibi-avatar`（36px 圆形、2px var(--ink) 描边）与 `.chibi-bubble-main`。

## 根因/结论
- 阻塞源是"记忆提取 + chibi 同步压在图流末尾"；选路线 2（彻底）：以"图跑完"作为正文完的权威信号（工具回环时图不结束，天然不误判），done 先行、内务异步，两者解耦。
- 记忆不丢：短期记忆由 checkpointer 在图执行期间入库；长期记忆 store.put 由 `_persist_long_term_memory` 在 done 后同一 worker 线程完成，失败静默打日志。
- 断连不回归：异常分支（error+SENTINEL）与 finally（注销注册表）未动；GeneratorExit 只停推送不停止 worker。

## 验证
- `ast.parse` / `import` 通过：main_graph、routes、chat_service（含 `_persist_long_term_memory` 存在性）；routes 无工具分支返回 END；
- `node --check app.js` 通过；
- 前端补丁点回读确认：apiChat 签名带 onDone（L382）、done 处理（L473）、finalizeReply（L2628）、apiChat 调用传 finalizeReply（L2732）、CHIBI_AVATAR_MAP（L1734）、chibi-avatar 模板（L1159）；CSS 竖排+36px 头像落地。
- 剩余运行时验证（需真实环境）：线上/本地起服后跑一轮带工具回环的对话确认 done 时序、刷新续接不回归。

## 待办
- 运行时冒烟（工具回环对话 + 刷新续接）待用户起服后补验；
- 文档撰写 Agent：README/项目详解中"图流程（memory_node 在图内）"与"SSE 事件类型"需同步（docs_sync: required）。
