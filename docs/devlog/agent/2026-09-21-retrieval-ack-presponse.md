---
docs_sync: none（已同步 2026-09-21：项目详解 08 篇 2.1/2.2/3.4/3.5/3.13/3.14 多模式流式+ack 预响应、09 篇 2.2/3.13/3.14 前端 ack 预响应；根 README 功能特性/「流式输出与工具调用状态」+新增「检索期 ack 预响应」节；简历 Bullet B1；提交见下）
---

# H-20260921-01 · 检索期预响应：开场白 + "正在全力思考中" loading

## 背景

路由判定 `need_retrieval=true` 后，`retrieve_node` 同步跑 `retrieve_graph.invoke()`
（改写→稠密→BM25→RRF→rerank→过滤，约 7s），期间 SSE 无任何事件，前端空白等待。

## 方案

用 LangGraph 标准 **custom stream** 通道，在检索开始前推一句人格化开场白（0 LLM 调用）：

1. **`src/constant/ack_constant.py`（新增）**：按 persona 的开场白模板
   `ACK_OPENINGS`（crazy/kind/cappie）+ `DEFAULT_ACK` 兜底，`pick_ack_text(persona)`。
2. **`src/graphs/nodes/retrieve_node.py`**：`retrieve_graph.invoke` **之前**
   `from langgraph.config import get_stream_writer` 拿 writer，推
   `{"ack": text, "persona": persona}`；writer 不可用（非 stream 调用）静默降级。
3. **`src/service/chat_service.py`**：`graph.stream` 的 `stream_mode` 由单模式
   `"messages"` 改为 `["messages", "custom"]` 多模式；迭代解包改为 `for mode, item`：
   - `mode == "custom"`：item 即 writer 写入的 ack dict，直接 `_append_thread_event` 落库 + 推 SSE；
   - `mode == "messages"`：item 是 `(chunk, meta)` 元组，走原 `_process_graph_chunk_events` 过滤逻辑。
4. **前端 `app.js`**：
   - `apiChat` 新增尾参 `onAck`；SSE 主循环识别 `chunk.ack`，把它作为 `answer` 初始值
     （后续正文 content 自然追加其后，开场白=助手消息第一句），并回调 `onAck(ackText)`；
   - `sendMessage`：`onAck` 里立即把 `aiMsg.content = ackText`（助手气泡 0 延迟出现）、
     `ragThinking = true`；首个正文 content chunk（`onStream` 首次触发）时 `ragThinking = false`；
   - 新 ref `ragThinking`；模板在 tool-call 指示器旁加 `.rag-thinking-indicator`
     （复用 `.thinking-dots` 弹跳动画，文案"正在全力思考中..."）；
   - 断点重放 `_applyEventsToMsg`：`ev.ack` 分支把开场白设为 `latestText` 起点，
     后续 content 事件追加其后，刷新后开场白与正文都在、不拆成两条。
5. **`style.css`**：新增 `.rag-thinking-indicator` 布局（复用现有 thinking 动画样式）。

## 验收对应

- ≤300ms 出现：ack 在 retrieve_graph.invoke 之前推，0 LLM 调用；
- 同一条消息：ack 作为 answer 前缀，正文流式追加其后；
- 非检索/工具路径不回归：ack 只在 `retrieve_node`（即 need_retrieval=true 分支）推；
- 断点重放：ack 事件与正文事件同入 Redis `chat:events:{thread_id}`，重放逻辑已处理。

## 验证

- `ast.parse` 三个 Python 文件通过；`get_stream_writer` 导入通过；`node --check app.js` 通过。
- 最小图冒烟：`stream_mode=["messages","custom"]` 下 `mode=='custom'` 的 item 即 writer
  写入的 dict，解包形态与实现一致（langgraph 1.1.2）。

## 遗留

- 开场白文案按 persona 微调过，贴合 Mitta 人设；线上实测首屏响应体感待用户验证。
