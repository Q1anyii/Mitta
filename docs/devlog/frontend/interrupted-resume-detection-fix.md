# Mitta 中断回复无法自动续接 排查与修复开发日志

> 涉及模块：前端消息加载与续接检测（`resources/frontend/assets/js/app.js`）
> 关联提交：`42fee1f`（fix(chat): 修复中断回复无法自动续接）

---

## 一、问题现象

用户在流式生成中途刷新页面（或网络中断）后，页面停留在 **"（回复中断，请重新生成）"** 占位状态：

1. 深度思考块已完整渲染，但正式回答永远不出现
2. 页面下方另有一个只渲染了一行的新深度思考块（疑似中断后再次发送，同样中断）
3. 无论刷新多少次，占位文本都不消失

**关键疑点**：后端日志显示该会话 history 完整（human 用户问题 + 完整 ai 回复），`_active_generations` 注册表为空——说明**后台线程其实已经跑完整张图并提交了 checkpoint，回复内容没有丢**，问题一定出在前端检测环节。

---

## 二、排查过程

### 第 1 步：确认后端数据未丢失

直接调 `chat_service.get_history_session('thread_mtu8defq_res33r')` 返回 2 条消息：

```
- human | 前端如何用 Fetch + ReadableStream 接收 SSE 流式响应？
- ai    | 好呀谦亦！这个问题问得正是 AI Agent 前端接流的关键点呢 (｡•̀ᴗ-)✧ ...
```

`_active_generations` 为空 → 后台生成已结束且已提交完整回复。后端无问题。

### 第 2 步：通读前端续接触发条件（定位根因）

`loadCurrentMessages`（修复前）中，自动续接的触发条件为：

```js
const lastIncomplete = lastCached && lastCached.role === 'assistant'
    && !lastCached.content                          // ← content 必须为空
    && (!lastCached.blocks || lastCached.blocks.length === 0);
...
if (lastIncomplete && replyStillIncomplete) {
    startGenerationResume(currentThreadId.value);   // ← 续接轮询
}
```

而 catch 非主动停止分支（流中断时）写的是：

```js
aiMsg.content = aiMsg.content || '（回复中断，请重新生成）';   // ← 把占位写进 content
_syncTextBlock(aiMsg, aiMsg.content);
```

### 第 3 步：确认根因（关键定位）

- 流中断 → catch 分支把占位文本 **"（回复中断，请重新生成）"** 写入了 `aiMsg.content`（非空字符串）
- 刷新后 `loadCurrentMessages` 检测 `!lastCached.content` → **content 非空 → 判定"回复已完成"** → `lastIncomplete = false`
- 续接轮询 `startGenerationResume` **永不启动** → 页面一直停留在中断占位
- **根因**：续接检测只看"content 是否为空"，而 catch 分支把占位文本写进了非空 content——两种状态判定互相矛盾，导致中断消息被误判为"已完成回复"

---

## 三、解决方案

### 3.1 catch 分支：给中断消息打标记

流中断时不改变 content 展示，但显式标记 `interrupted`：

```js
if (aiMsg) {
    aiMsg.content = aiMsg.content || '（回复中断，请重新生成）';
    // 【续接标记】标记该消息为"未完成的中断回复"：content 会被写成占位文本（非空），
    // 自动续接的检测条件必须识别该标记而不是只看 content 是否为空——
    // 否则刷新后占位文本会被当作"已完成"，续接轮询永不启动。
    aiMsg.interrupted = true;
    _syncTextBlock(aiMsg, aiMsg.content);
    _syncReasoningBlock(aiMsg, aiMsg.reasoning || '');
}
```

### 3.2 loadCurrentMessages：统一"未完成判定"函数

识别三种中断形态：① `interrupted` 标记（新逻辑）；② 占位文本（兼容已存缓存的旧数据）；③ 空 content + 空 blocks（流中断时的空消息）：

```js
const isIncompleteAiMsg = (m) => m && m.role === 'assistant'
    && (m.interrupted
        || m.content === '（回复中断，请重新生成）'
        || (!m.content && (!m.blocks || m.blocks.length === 0)));
const lastIncomplete = isIncompleteAiMsg(lastCached);
...
const replyStillIncomplete = isIncompleteAiMsg(lastMsg);
if (lastIncomplete && replyStillIncomplete) {
    startGenerationResume(currentThreadId.value);
}
```

---

## 四、验证结果

用 node 对 `isIncompleteAiMsg` 跑 5 个用例：

| 用例 | 输入 | 期望 | 结果 |
|------|------|------|------|
| catch 打断标记 | `{interrupted:true, content:'（回复中断，请重新生成）'}` | true | ✅ |
| 旧缓存占位文本 | `{content:'（回复中断，请重新生成）'}` | true | ✅ |
| 空 content 空 blocks | `{content:'', blocks:[]}` | true | ✅ |
| 已完成回复 | `{content:'完整回复', blocks:[...]}` | false | ✅ |
| 用户消息 | `{role:'user', content:''}` | false | ✅ |

`node --check resources/frontend/assets/js/app.js` ✅

**端到端链路确认**：该会话后端 history 完整（后台线程已跑完）→ 修复后刷新 → 检测到 interrupted → 启动续接轮询 → 第一次查询 `generating=false` 但 history 已有完整回复 → `replyComplete` 成立 → 渲染完整回复。

---

## 五、遗留与建议

| 项 | 状态 | 建议 |
|----|------|------|
| 旧浏览器缓存的 app.js | 待用户强刷 | 需 Ctrl+F5 强刷加载新代码，否则仍走旧检测逻辑 |
| 中断时"thinking 块残留" | 保留原样 | 续接成功后由后端 history 渲染覆盖；若仍需保留思考过程需后端持久化 reasoning 字段 |
| 续接后深度思考/工具穿插丢失 | 已知取舍 | checkpoint 恢复的回复只有标准 content，不含 reasoning/blocks/tool_calls——刷新续接保证内容完整，但思考过程退化为纯文本，属架构边界 |

---

## 六、经验沉淀

1. **"占位文本"与"未完成判定"必须使用同一套信号**——占位文本被写进非空 content 后，任何"看 content 是否为空"的检测都会失效；状态标记（`interrupted`）比内容推断可靠
2. **兼容旧缓存**：线上已有用户缓存了旧格式（content=占位文本），修复时必须同时兼容旧形态，否则旧会话仍不续接
3. **后端与前端的状态判定要交叉验证**：遇到"数据没丢但页面不对"时，先分别确认后端 history / 前端检测各自的状态，再找两者之间的判定缝隙
4. 续接机制的触发条件是链式依赖（lastIncomplete → startGenerationResume → replyComplete），任何一个环节的判定条件与写入侧不一致，整条链就会静默失效
