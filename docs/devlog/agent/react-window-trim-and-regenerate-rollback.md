# Mitta ReAct 循环 token 膨胀与重新生成残留 排查与修复开发日志

> 涉及模块：图消息组装（`src/graphs/nodes/llm_node.py`）、历史清洗（`src/graphs/utils/history_repair.py`）、会话服务（`src/service/chat_service.py`）、聊天路由（`src/routers/chat_router.py`）、前端（`resources/frontend/assets/js/app.js`）
> 关联提交：`467c73f`（perf(chat): ReAct 循环消息滑动窗口裁剪 + 重新生成后端回滚）

---

## 一、问题现象

用户提出两个独立但相关的优化需求：

### 问题 1：ReAct 循环 token 全量累积

- 内部 ReAct（工具调用循环）时，每次 `llm_node` 都把整个会话消息**全量**传入 LLM
- 消息随对话轮数线性增长，token 消耗随轮数平方级膨胀
- 深度思考 + 多轮工具调用时，一轮对话的实际 token 消耗是普通问答的数倍

### 问题 2：重新生成按钮行为不符

- 点击"重新生成"时，正常语义应是**先清空 AI 对该段会话的回复，再重新输出**
- 当前行为：只在前端删除本地显示的消息，后端 checkpoint 中该轮旧回复仍残留
- 重新生成时新回复与旧回复叠加，token 只增不减

---

## 二、排查过程

### 第 1 步：确认问题 1 的消耗机制（读 llm_node）

`src/graphs/nodes/llm_node.py` 消息组装核心（修复前）：

```python
# ── 3. 短期记忆：checkpointer 按 thread_id 恢复的历史对话 ──
history = state.get("messages", [])

# ── 4. 组装 system prompt ──
messages = [SystemMessage(content=system_content)] + _repair_history(list(history))
messages.append(HumanMessage(content=user_content))
...
for chunk in model_with_tools.stream(messages):   # ← 全量历史传入
```

**确认**：
- 图流程为 `START → classify_node → (route) → retrieve_node → llm_node → (route_after_llm) → tool_node → llm_node（循环）→ memory_node → END`
- 每次 `llm_node` 进入（含 ReAct 循环中每次工具调用后的再入），都把 `state["messages"]`（checkpoint 恢复的**全部历史消息**）全量传入 `model.stream()`
- 若单轮消息约 L token、共 N 轮，总消耗约 **N²·L** 量级，随工具调用轮次平方级膨胀
- `_repair_history` 只做"双向清洗 tool_calls/ToolMessage 配对"（防悬空调用/孤儿 ToolMessage），**不做压缩/截断**
- 图中没有任何 trim/summarize 历史消息的机制

### 第 2 步：确认问题 2 的残留机制（读前端 regenerateMessage）

`resources/frontend/assets/js/app.js`（修复前）：

```js
function regenerateMessage(msg) {
    const idx = messages.value.findIndex(m => m.id === msg.id);
    if (idx <= 0) return;
    const userMsg = messages.value[idx - 1];
    if (!userMsg || userMsg.role !== 'user') return;
    messages.value.splice(idx - 1, 2);   // ← 只删前端本地消息
    saveMessages();
    inputText.value = userMsg.content;
    sendMessage();
}
```

**确认**：
- `splice(idx - 1, 2)` 只删除前端本地缓存中的用户消息 + AI 消息
- **后端 checkpoint 中该轮 HumanMessage + AIMessage 仍然存在**（甚至包括工具链 ToolMessage）
- 重新生成时 `llm_node` 恢复的 history 包含被"删除"的旧回复 → 新回复与旧回复叠加、token 重复累积
- 前端消息 id（`generateId()` 生成）与后端 checkpoint 消息 id（LangChain 生成）**不对应**，无法直接按 id 定位

---

## 三、解决方案

### 3.1 问题 1：滑动窗口裁剪 `_trim_history`

新增 `_trim_history`（`history_repair.py`）：

```python
DEFAULT_MAX_HISTORY_MESSAGES = 30   # 窗口大小：约 6 轮对话（含工具链消息）

def _trim_history(history: list, max_messages: int = DEFAULT_MAX_HISTORY_MESSAGES) -> list:
    if not history:
        return []
    if len(history) <= max_messages:
        return list(history)
    trimmed = list(history[-max_messages:])
    # 窗口开头若是 ToolMessage（其前置 AIMessage 被裁掉）→ 丢弃，避免孤儿 ToolMessage 400
    while trimmed and isinstance(trimmed[0], ToolMessage):
        trimmed.pop(0)
    return trimmed
```

**配对保护要点**：
- 窗口开头孤儿 ToolMessage → 直接丢弃（保留会在 API 透传时 400）
- 窗口末尾悬空 tool_calls → 不在此处剥离，交由 `_repair_history` 统一处理（已有逻辑）
- 窗口内完整配对 → 原样保留

`llm_node` 接入：

```python
history = _trim_history(list(state.get("messages", [])))   # 替换 state.get("messages", [])
```

### 3.2 问题 1 补充：检索文档防膨胀

```python
# 最多取前 5 篇、每篇截断到 2000 字符（保留头部与关键词），
# 检索结果本身已按相关性降序，截断不会丢失最相关部分
docs = [d[:MAX_DOC_CHARS] for d in docs[:MAX_RETRIEVAL_DOCS]]
```

常量：`MAX_RETRIEVAL_DOCS = 5`、`MAX_DOC_CHARS = 2000`（`llm_node.py` 顶部）。

### 3.3 问题 2：后端 rollback 接口 + 前端联动

**后端**（`chat_service.py` 新增 `rollback_session`）：

```python
def rollback_session(self, thread_id: str, query: str):
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = self.main_graph.get_state(config)
    messages = snapshot.values.get("messages", [])
    # 从后往前找最后一个 content 等于 query 的 HumanMessage（该轮起点）
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].type == "human" and str(messages[i].content) == query:
            target_idx = i
            break
    # 删除该轮起点及其后的所有消息（AI 回复、ToolMessage 工具链）
    from langgraph.graph.message import RemoveMessage
    remove = [RemoveMessage(id=m.id) for m in messages[target_idx:]]
    # update_state 触发 add_messages reducer：RemoveMessage 删除指定 id 的消息，
    # 其他消息保留——不能直接传裁剪后列表（add_messages 是追加语义，不会覆盖）
    self.main_graph.update_state(config, {"messages": remove})
```

**路由**（`chat_router.py`）：新增 `POST /api/chat/{thread_id}/rollback`，归属校验与 history/delete 一致（403）。

**前端**（`app.js`）：
- 新增 `apiRollbackSession(threadId, query)` 调 rollback 接口
- `regenerateMessage` 改造为 async：**先调 rollback 删除后端该轮记录 → 再本地 splice → 重新发送**；回滚失败不阻塞本地重发但提示用户（旧回复可能叠加）

---

## 四、验证结果

### 4.1 `_trim_history` 单元测试（5 用例全过）

| 用例 | 场景 | 期望 | 结果 |
|------|------|------|------|
| 超窗裁剪 | 40 条 → 30 条 | 保留最近 5 轮+ | ✅ |
| 开头孤儿 ToolMessage | 33 条，窗口开头是 ToolMessage | 丢弃孤儿，保留 30 条 | ✅ |
| 末尾悬空 tool_calls | 窗口末尾带 tool_calls 无 ToolMessage | 不主动剥离，`_repair_history` 剥离 | ✅ |
| 窗口内完整配对 | AI tool_calls + ToolMessage 都在窗口内 | 原样保留 | ✅ |
| 空/小窗口 | 空列表、未超窗 | 原样返回 | ✅ |

### 4.2 rollback 端到端测试（3 场景全过）

在真实 PostgreSQL checkpoint 上验证：

| 场景 | 结果 |
|------|------|
| 回滚第 2 轮 | 精确删除 HumanMessage+AI 回答，第 1 轮保留 ✅ |
| 回滚不存在的 query | 正确失败"未找到该轮用户消息" ✅ |
| 回滚第 1 轮 | 只留系统消息 ✅ |

### 4.3 语法检查

- `node --check resources/frontend/assets/js/app.js` ✅
- Python `ast.parse` 全文件 ✅

---

## 五、遗留与建议

| 项 | 状态 | 建议 |
|----|------|------|
| 窗口大小可调 | 已实现 | `DEFAULT_MAX_HISTORY_MESSAGES=30`，约 6 轮；对话更长时可调大或引入早期消息摘要 |
| 早期消息摘要压缩 | 未实现 | 当前策略是"丢弃"；如需长期记忆保真可加 summarize 早期轮次（增加一次 LLM 调用成本，需权衡） |
| 重新生成时的 reasoning/blocks 恢复 | 未实现 | rollback 删除的是 checkpoint 标准消息；前端本地 blocks 在 splice 时一并清除，无需后端处理 |
| 批量多轮 rollback | 未实现 | 当前按单个 query 锚点删除单轮；如需要"删除从某轮到末尾"可扩展接口支持 `from_index` |

---

## 六、经验沉淀

1. **ReAct 循环的 token 消耗是指数级陷阱**：`llm_node` 每次再入（工具调用后）都重新全量发送历史，工具链越深，下一轮重发的历史越长——滑动窗口是性价比最高的解法（O(N²·L) → O(W·L)，W 为窗口大小）
2. **"重新生成"必须在 checkpoint 层回滚**：前端 splice 只解决"显示"，后端 `update_state` + `RemoveMessage` 才能解决"上下文"——两者缺一，新回复必然与旧回复叠加
3. **LangGraph 的 add_messages 是追加语义**：回滚不能用"传裁剪后列表"覆盖，必须用 `RemoveMessage` 删除指定 id 的消息，否则 reducer 会把旧消息合并回来
4. **配对保护是裁剪的硬约束**：窗口裁剪一旦拆开 tool_calls/ToolMessage 配对，API 直接 400——裁剪逻辑必须与 `_repair_history` 形成"裁剪→修复"两段式管线
5. **前端消息 id 与后端 checkpoint id 不对应**：跨端定位轮次不能靠消息 id，用"用户消息文本"做锚点最可靠（同轮用户消息 content 唯一）
