# Mitta 工具调用内容为空 排查与修复开发日志

> 日期：2026-09-08
> 涉及模块：后端 SSE 事件处理（`src/service/chat_service.py`）、前端流式消费（`resources/frontend/assets/js/app.js`）、LLM 生成节点（`src/graphs/nodes/llm_node.py`）
> 关联提交：`b12e0bc`（fix: 工具调用参数为空与深度思考碎片化堆积）

---

## 一、问题现象

前端对话中，AI 调用 MCP 工具（如"搜索图谱节点"）时，工具调用卡片**只显示工具名，参数区域完全为空**：

- 截图可见工具卡片标题"搜索图谱节点"，下方参数/结果展示为空
- 知识图谱检索工具返回 `{"entities": [], "relations": []}`——无法判断是"参数没传对"还是"知识库真的无结果"
- 该问题同时影响 `toolSummary(block.name, block.args)` 的展示，工具卡片的可读性大幅下降

---

## 二、排查过程

### 第 1 步：确认现象发生在前端还是后端

- 前端 `apiChat` 收到 `tool_call_start` 事件后调用 `onToolCall({ type: 'start', name, args })`，创建工具块 `{ name, args: toolEvent.args || {}, ... }`
- 工具块的 args 来源 = 后端 `tool_call_start` 事件里的 `args` 字段
- **结论**：现象源头在后端发出来的 `args` 就是空的

### 第 2 步：通读后端 `_process_graph_chunk` 的 tool_calls 处理

```python
if chunk.tool_calls:
    for tc in chunk.tool_calls:
        tool_name = tc.get("name", "")
        if tool_name:  # 仅首块有 name，过滤掉 args 增量块
            events.append(_format_sse({
                "tool_call_start": {
                    "name": tool_name,
                    "args": tc.get("args", {}),
                }
            }))
```

关键注释暴露了设计意图：**LangChain 流式输出中 tool_calls 分块传输——首块含 name+空 args，后续块 name 为空、args 为增量**。代码"只在首块（有 name）时发 start"以避免重复卡片，但首块的 args 就是 `{}`，**后续携带完整参数的增量块被 `if tool_name` 直接过滤**。

### 第 3 步：确认完整参数其实"存在但被丢弃"（实锤根因）

- `llm_node` 是**累积式**生成：先 `for chunk in model.stream(messages)` 收集全部 chunk，再用 `AIMessageChunk.__add__` 合并，最终构造 `AIMessage(content=..., tool_calls=完整列表)` 返回
- `graph.stream(stream_mode="messages")` 在节点结束时**会输出节点返回的完整消息**（`tool_node` 的 ToolMessage 能被捕获即证据）
- 但 `_process_graph_chunk` 的 llm_node 分支用了 `isinstance(chunk, AIMessageChunk)` 判断——**完整 AIMessage 不是 AIMessageChunk**，走到该分支直接被吞掉，`return None` 不产生任何事件
- **根因链**：① 流式首块 args 为空 → 发了个空参数 start；② 增量块 args 被过滤 → 参数永远凑不齐；③ llm_node 返回的完整 AIMessage（含合并后的完整 tool_calls）被类型判断挡掉 → 完整参数永远到不了前端

---

## 三、解决方案

`src/service/chat_service.py`：

1. **import 增加 `AIMessage`**：

```python
from langchain_core.messages import BaseMessage, AIMessage, AIMessageChunk, ToolMessage
```

2. **删除流式 chunk 分支里"发空 args 的 tool_call_start"逻辑**（AIMessageChunk 分支只保留 reasoning/content 输出）：

```python
if node == "llm_node" and isinstance(chunk, AIMessageChunk):
    # 只发 reasoning + content，不再发 tool_call_start（首块 args 为空，发了也是空）
    ...
```

3. **新增完整 AIMessage 分支**——工具调用参数唯一的正确来源：

```python
# llm_node 完整消息（非 chunk）：LangGraph stream_mode="messages" 在节点结束时
# 会输出节点返回的完整 AIMessage，此时 tool_calls 已由 llm_node 内部合并完整
if node == "llm_node" and isinstance(chunk, AIMessage) and not isinstance(chunk, AIMessageChunk):
    if chunk.tool_calls:
        events = []
        for tc in chunk.tool_calls:
            tool_name = tc.get("name", "")
            if tool_name:
                events.append(_format_sse({
                    "tool_call_start": {
                        "name": tool_name,
                        "args": tc.get("args") or {},
                    }
                }))
        if events:
            return "".join(events)
    return None
```

**前端无需改动**——`tool_call_start` 事件结构不变，只是 `args` 从 `{}` 变成完整参数，`toolSummary(block.name, block.args)` 自动显示真实参数。

---

## 四、验证结果

- `python -m py_compile src/service/chat_service.py` ✅
- 事件时序验证：完整 AIMessage 在 llm_node 结束、tool_node 执行**之前**输出，`tool_call_start` 先于 `tool_call_end`，前端加载动画时序不受影响 ✅
- 工具块参数链路：`tool_call_start.args`（完整）→ 前端 `onToolCall` → 工具块 `args` → `toolSummary` 展示 ✅

---

## 五、遗留与建议

| 项 | 状态 | 建议 |
|----|------|------|
| `tool_call_end` 结果截断 300 字符 | 保留 | 工具输出过长时仍会被截断，如需完整结果可调大或前端做分页 |
| 多工具并发 | 正常 | 每个工具独立发 `tool_call_start`，前端按 name 匹配最后一个 running 块 |
| 空参数工具调用 | 待观察 | 若某工具本身无参数，卡片将正常显示空参数，属预期行为 |

---

## 六、经验沉淀

1. **流式 tool_calls 是"首块有名字、增量带参数"的分块结构**——想在流式早期拿到完整参数是做不到的，必须等节点返回的完整消息
2. **LangGraph `stream_mode="messages"` 会输出两类消息**：LLM token 增量（AIMessageChunk）和节点返回的完整消息（AIMessage/ToolMessage）——处理时要用 `isinstance` 区分两者，完整消息往往携带 chunk 阶段拿不到的信息
3. **"为了避免重复卡片而过滤增量块"是正确方向，但必须同时提供完整参数的来源**——只堵不疏会留下"参数永远为空"的隐性 bug
4. 排查前端展示问题时，先确认数据链路（后端事件 → 前端消费 → 渲染），再用截图对照代码定位，比直接改前端更快
