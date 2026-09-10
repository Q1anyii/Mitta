# AI 回复中切换会话导致错乱 + 发送按钮锁死

## 遇到的问题

1. AI 回复过程中切换到其他会话，会话内容错乱（旧会话的流式内容/工具调用
   状态污染新会话界面）；
2. 切换会话后发送信息按钮无法点击（一直处于禁用状态）。

## 排查过程

1. **发送按钮锁死**：`canSend = inputText.trim() && !isLoading.value`。`isLoading`
   是全局单值：会话 A 发送消息后 `isLoading=true`，直到 A 的流式请求 `finally`
   才复位。期间切到会话 B，`switchSession` 只切换 `currentThreadId` 和加载消息，
   **从未重置 `isLoading`** → B 的发送按钮被 A 的生成状态锁死。

2. **会话错乱**：`sendMessage` 的流式回调（onStream / onToolCall / onReasoning /
   renderTimer flush / 流结束块 / catch / finally）闭包直接持有并操作：
   - `aiMsg`（发送时 messages 末尾的 AI 占位对象）
   - `messages.value`、`saveMessages()`、`currentToolCall`、`scrollToBottom()`
   切到会话 B 后这些回调继续执行：`scrollToBottom` 滚动 B 的容器、`currentToolCall`
   显示 A 的工具调用、`saveMessages` 用当前 thread 缓存、异常分支还可能
   `createNewSession()` 误删当前会话——旧会话的异步事件全部串进新会话视图。

## 解决方案

引入"发送会话快照 + 生成集合"双重机制，全部改动在
`resources/frontend/assets/js/app.js`：

1. **`_generatingThreads = new Set()`**（setup 顶部）：记录正在生成回复的
   thread_id 集合，作为"会话级生成状态"的权威来源。
2. **`sendThreadId` 快照**：发送时记录 `const sendThreadId = currentThreadId.value`
   并 `_generatingThreads.add(sendThreadId)`。
3. **全回调守卫**：onStream / onToolCall / onReasoning / renderTimer flush /
   流结束落库块 / catch 各分支，入口统一校验
   `currentThreadId.value === sendThreadId`，不一致则直接跳过 UI 更新——
   后台线程继续跑完图并提交 checkpoint，切回该会话时 `loadCurrentMessages`
   从后端 history / 事件流恢复完整回复（与"断连不中断生成"同一套兜底）。
4. **会话级 isLoading**：
   - `switchSession(id)`：`isLoading = _generatingThreads.has(id)` +
     `currentToolCall = null` + `stopGenerationResume()`；
   - `createNewSession()`：`isLoading = false`（新会话必然未在生成）；
   - `finally`：`_generatingThreads.delete(sendThreadId)`，仅当当前视图仍是
     发送会话时才复位 `isLoading/streaming/currentToolCall/focusInput`。
5. **403 分支修正**：误归属时移除的是 `sendThreadId`（发送时的会话），
   且仅当视图仍在该会话时才 `createNewSession()`，避免误删当前会话。

## 验证

- `node --check` 语法通过。
- 逻辑推演场景：
  - A 生成中切到 B：B 的 `isLoading=false` → 发送按钮立即可用 ✓
  - A 的流式回调继续触发：守卫拦截，B 界面/缓存不被污染 ✓
  - 切回 A：`loadCurrentMessages` 从后端恢复完整回复（后台已落库）✓
  - A 生成完、B 生成中：`_generatingThreads` 中只有 B，B 的按钮在 B 的
    生成期间正确锁定 ✓
  - 删除生成中的会话：switchSession 重算 isLoading，守卫防止旧流污染 ✓

## 附带收益

- 修复"切换会话后发送按钮永久锁死"；
- 修复"切回旧会话看到错乱内容/工具状态残留"；
- 会话级生成状态为后续多会话并发（多标签页/多开）提供正确基础。
