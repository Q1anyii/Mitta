---
docs_sync: none
---

# H-20260921-02 · 检索引用折叠块（citations）：回答下方展示出处

## 背景

RAG 检索路径的回答无法验证来源。需求：正文下方显示可折叠"参考了 N 段项目文档"块，
默认收起，展开看到 top3 出处（文件名 + 前 50 字片段），让用户能核对答案来自知识库原文。

## 方案

复用 H-01 的 LangGraph custom stream 通道（同一 `get_stream_writer()`）：

1. **`src/graphs/nodes/retrieve_node.py`**：`retrieve_graph.invoke()` 跑完、output 转 dict 后，
   取 top3 构造 `refs=[{file, snippet}]`：
   - `file` = chunk metadata 的 `source`（入库时写的文件名），兜底 `category` / "未知文档"；
   - `snippet` = `page_content` 去换行后前 50 字；
   - 推 custom 事件 `{"type":"citations","refs":[...]}`；失败静默降级。
2. **`chat_service.py`**：无需改——H-01 的 custom 分支已对所有 custom 事件
   `ev=item` 原样落库（`_append_thread_event`）+ 推 SSE，citations 自动走通。
3. **前端 `app.js`**：
   - `apiChat` 新增尾参 `onCitations`；SSE 主循环识别 `chunk.type==="citations"`，回调 `onCitations(refs)`；
   - `sendMessage`：`onCitations` 把 refs 绑到当前助手消息 `aiMsg.citations`；
   - 模板：正文下方加 `.citations-block`，默认收起（`msg.citationsOpen` 初始 false），
     `📚 参考了 N 段项目文档 ▾`，点击展开列出文件名（粗体）+ snippet（浅灰小字）；
   - 断点重放 `_applyEventsToMsg`：`ev.type==="citations"` 分支设 `aiMsg.citations`，刷新后折叠块还在。
4. **`style.css`**：`.citations-block` 弱视觉样式（浅灰边、小字号、浅背景），不抢正文。

## 时序与边界

- retrieve_node 推完 citations 再进 llm_node 流式正文 → 前端先收到折叠块数据，正文开始时块已在；
- 只走 `retrieve_node`（即 need_retrieval=true）才推；闲聊/直答/工具路径不推、不出现空块；
- 检索结果为空兜底 top3 时仍推，不报错；
- 展开/收起状态本地消息会话内即可，不落库。

## 验证

- `ast.parse retrieve_node.py` 通过；`node --check app.js` 通过。

## 遗留

- 线上实测折叠块视觉与 top3 出处准确性待用户验证。
