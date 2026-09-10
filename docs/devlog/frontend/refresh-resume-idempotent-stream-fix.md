# 刷新后"连接中断"体验修复：事件落库重放 + 幂等去重（方案 1+2）

## 现象

- 用户发送问题后刷新页面，后端仍在后台生成回复，但前端 SSE 连接已断开。
- 等待期间用户多次点击发送/刷新，同一问题在后端被重复创建生成任务，同一会话累积多条完全相同的 human 消息（实测 `thread_mtuznh02_x4u5xk` 存了 3 条相同 human，`thread_mtubqqfl_usm3c4` 累积 6 条）。
- 刷新后界面先显示"新会话"空白，轮询补全前用户误判"没在回复"，进一步加重重复发送。

## 排查

1. **用后端取证确认不是渲染问题**：拉取 `thread_mtuznh02_x4u5xk` 的 history，真实存在 3 条 len=22 完全相同的 human——是用户刷新等待期间真的发了 3 次，不是前端重复渲染。
2. **理解行业本质**：刷新必然断开浏览器连接（TCP/SSE/WS 活不过页面重载），"刷新不断流"不是靠保持连接，而是四件套：
   - 任务与连接解耦（本项目已有：worker 线程 + `_active_generations`，断连不中断生成）；
   - **事件落库**（缺）：流式事件（思考/工具/正文增量）实时写入 Redis；
   - **断点重连 + 重放 last_event_id**（缺）：刷新后按序号重放已产生的事件；
   - **幂等去重**（缺）：同一消息重复提交时后端直接拦截。
3. 前端仅有"同题+在途拦截"守卫，可被刷新绕过（刷新后本地缓存丢失，守卫失效）；后端完全没有幂等。

## 方案（用户确认 1+2）

### ① 后端幂等去重（根治重复 human）

- `ChatRequest` 新增 `client_message_id: Optional[str]`。
- `POST /api/chat/` 在会话归属校验后、创建生成任务前执行：
  `SET chat:idem:{user_id}:{client_message_id}` nx=True ex=300s。
  未拿到锁（已处理过）→ 直接返回 SSE `{"duplicate": true}` + `[DONE]`，不创建任务。
  Redis 异常降级放行（幂等是保护而非硬依赖）。
- 前端每次发送生成 `cm_<时间戳36进制>_<随机>` 随请求带上；收到 duplicate 时回滚本地刚 push 的 userMsg + AI 占位，toast 提示后触发事件重放/续接。

### ② SSE 事件落库 + events 查询接口（刷新后过程可见、继续流式）

- `chat_service`：
  - `_process_graph_chunk_events(chunk, meta)` 把原始 chunk 转成结构化事件：`{"reasoning": 增量} / {"content": 增量} / {"tool_call_start": {name, args}} / {"tool_call_end": {name, content}}`（原 `_process_graph_chunk` 保留为兼容封装）。
  - `_append_thread_event(thread_id, event)`：`rpush chat:events:{thread_id}` + 7 天 TTL，Redis 异常静默降级。
  - `get_thread_events(thread_id, after=-1)`：`lrange(after+1, -1)`，List 下标即序号，返回 `[{"seq", "event"}]`。
  - `clear_thread_events(thread_id)`：删除会话时同步清事件流。
  - `stream()` 的 `_run_graph` 每 chunk 先转结构化事件 → 逐个落库 + 推 SSE；异常分支的 error 事件也落库。
- 新增 `GET /api/chat/{thread_id}/events?after=N`（归属校验同 history）：
  `after=-1` 返回全部事件（刷新后首次重放），`after=N` 返回 seq>N 的增量（轮询续收）。
- 前端：
  - `apiGetEvents(threadId, after=-1)` 拉事件。
  - `_applyEventsToMsg(aiMsg, events)`：增量语义与 SSE 实时路径同构（reasoning/content 增量追加 + `_syncReasoningBlock/_syncTextBlock`；tool_call_start 插 running 块；tool_call_end 更新同名 running 块）。
  - `startGenerationResume` 重构为 `pollOnce`：并行拉 `history + generation-status + events(after=lastAppliedSeq)`：
    ① 事件重放（本地无 assistant 占位则新建；首次重放 lastSeq===-1 先清空承载对象再整体重放，避免与本地不完整投影叠加重复）；
    ② history 最终一致性兜底（完整回复落库后整体替换）；
    ③ `!generating` 失败占位分支保留。
    先 `pollOnce()` 立即执行一次（刷新后马上重建断点），再 setInterval 每 2s 续收，5 分钟上限。

## 验证

- 后端 3 文件 `ast.parse` 通过。
- 前端 `node --check` 通过。
- node 复刻场景单测 S1–S4 全过：
  - S1 全量事件重放（reasoning/content/blocks 正确重建，工具块插入、工具后正文正确新建 text 块）；
  - S2 增量续收（工具块后正文增量追加正确）；
  - S3 重置后重放不重复；
  - S4 幂等守卫（同题+在途拦截 / 已完成放行）。
- 运行时实测（重启后端后）：
  - 同一 `client_message_id` 第一次 POST 正常生成；第二次 POST 立即返回 `data: {"duplicate": true}` + `[DONE]`，未创建第二个生成任务。
  - `GET /events?after=-1` 返回全部 172 个事件（reasoning seq0-170 + content seq171），序号连续；
  - `GET /events?after=0` 从 seq1 开始，增量语义正确。
  - 删除会话后事件流同步清理，无报错。

## 遗留

- 事件流 TTL 7 天：超期后旧会话刷新无法重放过程，但 history 兜底仍可拿到最终回复（降级为"无过程"而非"无结果"）。
- 幂等键 TTL 300s：生成超过 5 分钟（深度思考+多轮工具）后重复提交同一消息会绕过幂等。当前 5 分钟覆盖绝大多数场景，如需更长可调 `_IDEMPOTENCY_TTL`。
- 跨浏览器/清缓存刷新后本地无占位：`pollOnce` 会新建 assistant 占位承载事件重放，过程仍可见。

## 经验

- "刷新不断流"的本质是**任务与连接解耦 + 事件落库 + 断点重放 + 幂等去重**四件套，缺一不可；只做轮询 history 只能解决"结果"，解决不了"过程可见"和"重复发送"。
- 事件用 Redis List 存储时，**下标天然就是序号**（`lrange(after+1, -1)`），比单独维护自增 ID 简单且无并发问题（单进程 rpush 有序）。
- 重放时本地 blocks 是流式节流渲染的"不完整投影"，直接增量追加会叠加重复——首次重放必须先从零重建承载对象，再按事件流整体重放。
