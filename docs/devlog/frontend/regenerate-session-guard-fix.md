# 重新生成期间切换会话导致旧回复残留 — 修复日志（v2：事件流根因）

## 问题现象

点击 AI 回复的「重新生成」按钮后，如果**立即切换到其他会话，再切回原会话**，原会话**所有旧回复仍然完整显示**——重新生成似乎从未生效。且期间切换到的会话可能被误发旧问题（历史上曾触发）。

## 排查过程

1. **服务器直接复现（干净场景）**：登录线上环境 → 新建会话发消息 → 等回复完成 → 点「重新生成」。实测旧回复**立即清空**、新回复正常生成。→ 排除基础链路问题。
2. **服务器复现（多轮场景）**：发两条消息后对**第一条**回复重新生成。实测该轮旧回复立即清空、后续轮次正常保留。→ 后端回滚、前端 splice 逻辑本身正确。
3. **后端接口验证**：`POST /api/chat/{thread_id}/rollback` 实测 **243ms** 返回「已回滚该轮回复，删除 2 条消息」；`chat_service.rollback_session` 用 `RemoveMessage` 按 id 批量删除，锚点定位正确。→ 排除后端慢点。
4. **幂等排除**：后端幂等键为 `(user_id, client_message_id)`，重新生成会生成**新** client_message_id，不会命中 `DUPLICATE` 分支 → 排除刷新续接/去重重放链路。
5. **线上新代码完整复现（关键）**：在已部署 `regThreadId` 修复的线上版本，用浏览器自动化执行「点重新生成 → 立即切走 → 切回」：**旧回复仍出现**。进一步对比数据：
   - 后端 PG checkpoint：该会话 history 已为空（rollback 删除成功）✓
   - 前端 localStorage 缓存：**被写回 [旧 user, 旧 AI]**（续接重放导致）
   - **Redis 事件流（`chat:events:{thread_id}`）**：`GET events?after=-1` 返回**旧一轮完整事件**（seq 0 起的思考/正文增量）——**rollback 只删了 PG checkpoint，没删 Redis 事件流**
6. **根因定性**：`regenerateMessage` 的前端修复（乐观删除+守卫）对「回滚期间切走」场景有效，但 rollback 实测仅 ~50ms，用户几乎不可能在回滚完成前切走——实际链路是：**sendMessage 已发出（新回复在后台生成）→ 用户切走再切回 → `loadCurrentMessages` 因本地 AI 占位未完成而触发 `startGenerationResume` 续接轮询 → `apiGetEvents(thread, -1)` 从 seq=-1 把 Redis 里的旧一轮事件整体重放到新 AI 占位 → 旧回复内容"复活"并 `saveMessages()` 写回缓存**。

## 解决方案

### v1（前端，已部署）

重写 `regenerateMessage`（app.js），引入三层防护：

1. **乐观删除**：点击**立即** `splice` 删除该轮消息并 `saveMessages()` 落缓存。即使后端回滚较慢，界面也瞬间清空旧回复；用户切走再切回时，本地缓存已无旧回复。
2. **回滚带超时**：`Promise.race` 包裹 `apiRollbackSession`（5s 超时），避免后端异常时 `await` 永久挂起、界面长时间无响应。
3. **失败恢复 + 会话守卫**：
   - 回滚失败/超时 → 若仍在原会话，把 `removed` 消息恢复回数组并保存，取消本次重新生成并 toast 提示（保证前后端一致，刷新后不会旧回复复现）；
   - 回滚成功但用户已切走（`currentThreadId !== regThreadId`）→ 直接 return，**不**把旧问题发送到新会话、**不**污染新会话缓存。

### v2（后端，根治）

`chat_service.rollback_session` 回滚成功后在删除 PG checkpoint 的基础上，**同步清空该会话的 Redis 事件流**（`DEL chat:events:{thread_id}`）：

- 重新生成的新事件将从 seq 0 重新开始写入；
- 前端续接轮询 `apiGetEvents(thread, -1)` 只拿到**新一轮**事件，旧一轮的思考/正文事件不再被重放；
- 彻底消除「旧回复被续接重放复活」的污染源。

## 验证

- `node --check app.js` 语法通过；`python -m ast` 解析 `chat_service.py` 通过。
- 线上实测（v1 部署后）：
  - **不切走**：点重新生成 → 旧回复立即清空 → 新回复正常生成 ✓
  - **切走再切回**（v1 环境）：旧回复仍被续接重放 → 复现 bug，确认 v1 不足
  - **v2 部署后**（清事件流）：切回时续接只重放新事件 → 旧回复不再复活（逻辑推导 + 数据对比验证）
- 遗留说明：已触发过 bug 的会话，其 localStorage 缓存可能已被写回旧回复（污染数据）；修复部署后对该会话**重新生成一次**或刷新即可恢复（缓存优先逻辑会读到后端正确 history）。

## 影响范围

- `resources/frontend/assets/js/app.js` — `regenerateMessage` 函数整体重写（v1）。
- `src/service/chat_service.py` — `rollback_session` 增加清空 Redis 事件流（v2）。
