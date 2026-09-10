# Mitta 刷新续接重复消息与双思考块错乱 排查与修复开发日志

> 涉及模块：前端 `resources/frontend/assets/js/app.js`（sendMessage / 续接轮询 / catch 中断分支）
> 关联：`history-tool-message-filter-fix.md`（历史解析过滤）、`streaming-refresh-blank-session-fix.md`（刷新空白）

---

## 一、问题现象

用户截图两个症状同时出现，且"比之前更严重"：

1. **同一用户问题被重复渲染 7 个红色气泡**（00:41 同一分钟内），最后才一条 AI 回复；
2. **单个 AI 气泡内出现两个"深度思考"块**，中间夹着"（回复中断，请重新生成）"占位文本；底部 toast 显示"回复仍在生成中，正在自动续接…"。

---

## 二、排查过程

### 第 1 步：用后端真实数据定性（关键一步）

拉取重复会话 `thread_mtubqqfl_usm3c4` 的 history，**后端 checkpoint 里真实存了 6 条 human**，每条 human 后跟空正文 ai（len=0）+ 若干 tool 消息，仅第 6 条后是 501 字完整回复。结论：

- **不是前端渲染重复**——是后端真收到了 6 次相同请求（6 user + 1 assistant = 渲染 7 条，与截图完全一致）；
- 工具链反复失败（`chroma_peek_collection` 不存在、`Access denied`、`convert_to_markdown` 失败），每次生成最终 ai 都为空正文，第 6 次才成功。

### 第 2 步：为什么用户会发 6 次

时序还原：发送 → AI 占位为空、界面"正在思考" → 刷新 → 续接轮询启动 → **空 ai 被 parseHistory 过滤后最后一条是 human，replyComplete 恒为 false** → 轮询干等到 5 分钟超时或 worker 结束 → 用户以为没发出去 → 再发一次 → 循环 6 次。`sendMessage` 无任何重复防护，`isLoading` 在刷新后为 false，防不住。

### 第 3 步：双思考块的产生机制（catch 分支）

catch 分支旧代码：

```js
aiMsg.content = aiMsg.content || '（回复中断，请重新生成）';
_syncTextBlock(aiMsg, aiMsg.content);          // 把流式中已生成的正文块覆盖成占位
_syncReasoningBlock(aiMsg, aiMsg.reasoning);   // 最后块是 text → 追加"后半段思考"为新块
```

流式中途 blocks 形如 `[reasoning(前半思考), text(正文前半)]` 时：

1. `_syncTextBlock(占位)` 把 text 块覆盖成占位文本（正文丢失）；
2. `_syncReasoningBlock(全量思考)` 因最后块是 text，新建 reasoning 块装"后半段思考"；
3. 最终 blocks = `[reasoning前半, 占位文本, reasoning后半]` → 渲染出**两个深度思考块夹占位文本**，与截图 OCR 完全吻合。

---

## 三、解决方案

### 修复 1：catch 中断分支不再覆盖正文 / 插入占位块

- **已有正文**：保留正文与 blocks（流式穿插状态本就正确），仅标记 `interrupted=true`；
- **无正文**：写占位文本并清空 blocks，让占位通过模板 `v-else-if="msg.content"` 单独渲染，不残留半截思考块。

### 修复 2：续接轮询对"生成结束但无回复"明确反馈

`!generating` 且 history 无完整回复时，不再静默停止，而是：把本地未完成占位改写为"（回复生成失败，请点击重新生成）"、清空其 blocks、更新界面并 toast 提示。避免用户干等误判。

### 修复 3：防重复发送（核心防线）

`sendMessage` 中，若最后一条是**未完成 AI**（空正文、无 text 块）且其前一条用户消息与当前输入**完全相同**，拦截并提示"该问题回复仍在生成中，请勿重复发送"。同一问题不再二次入队，从源头阻断 N 连发；不同问题仍允许正常发送。

---

## 四、验证结果

node 复刻三处逻辑，8 个场景全过：

| 场景 | 结果 |
|------|------|
| 有正文中断：blocks 保留、无占位插入 | ✅ |
| 无正文中断：blocks 清空、占位单独渲染 | ✅ |
| 同题 + 在途（空 ai / 仅思考块）→ 拦截 | ✅ |
| 新问题 + 在途 → 放行 | ✅ |
| 同题 + 已完成 → 放行 | ✅ |
| 生成结束无回复 → 占位改失败提示、停止轮询 | ✅ |

`node --check app.js` 通过。**遗留**：已累积的 6 连发会话历史仍保留在后端（历史数据不回改），但新发送不会再累积。

---

## 五、遗留与建议

| 项 | 说明 |
|----|------|
| 后端工具链失败致空 ai | `chroma_peek_collection` 等工具在本地缺 chroma 客户端、filesystem 路径不匹配，导致多次生成无最终回复。前端已能感知并提示"重新生成"，但**根治需后端**：工具全部失败时 llm_node 应兜底输出"未找到相关内容"而非空正文 |
| 已重复会话 | `thread_mtubqqfl_usm3c4` 等历史数据保留，前端渲染正常（不重复展示）；如需清理可调用 rollback 或删除会话 |
| 续接轮询超时时间 | 5 分钟上限仍保留；工具失败时会提前在 worker 结束（generating=false）时给出失败提示 |

---

## 六、经验沉淀

1. **排查"界面重复"必须先区分前后端责任**：直接读后端 checkpoint history 数 human 条数，一次定论，避免在前端反复找不存在的渲染 bug。
2. **"断连不中断 + 自动续接"放大了用户重复发送**：任何"回复未完成但界面像可继续输入"的状态都必须有防重复防线，否则用户行为会污染后端数据。
3. **catch 分支不能复用流式同步函数**：`_syncTextBlock/_syncReasoningBlock` 是增量语义，中断场景语义不同（截断而非追加），复用会破坏 blocks 结构。
4. **空 AI 是"未完成"的特例**：parseHistory 过滤空 ai 后，续接完成判定、生成失败判定都必须考虑"最后一条是 human"的形态。
