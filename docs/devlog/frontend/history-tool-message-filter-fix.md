# Mitta 刷新续接后工具结果 JSON 被渲染成独立 AI 气泡 排查与修复开发日志

> 涉及模块：前端历史消息解析（`resources/frontend/assets/js/app.js` 的 `parseHistory`）
> 关联：`streaming-refresh-blank-session-fix.md` 修复"生成中刷新空白"后，续接渲染暴露出的历史消息 role 映射问题

---

## 一、问题现象

在一次带 MCP 工具调用（dbhub 查表）的对话生成中途刷新页面，续接渲染后界面出现一串**独立的"AI 助手"气泡**，每个气泡里是工具返回的原始 JSON：

```
AI 助手：●●● 正在思考...
AI 助手：{"success":true,"data":{"object_type":"table",...department,salary,title...}}   [复制/分享/重新生成]
AI 助手：{"success":true,"data":{"object_type":"column","pattern":"%hash%",...}}
AI 助手：{"success":true,"data":{"object_type":"column","pattern":"%password%",...}}
```

工具调用过程本应折叠在当前回复的工具块里（流式时由 blocks 穿插展示），不应作为一条条独立回复呈现。

---

## 二、排查过程

### 第 1 步：区分流式渲染与历史渲染路径

- **正常流式（不刷新）**：工具结果走 `apiChat` 的 `onToolCall` 回调，写入当前 `aiMsg.blocks`（折叠工具块），不经过 `parseHistory`——此路径正常。
- **刷新/续接**：界面由 `parseHistory(history)` 用后端历史重建，问题只可能在这里。

### 第 2 步：定位 role 映射（根因）

修复前 `parseHistory`：

```js
role: item.role === 'human' ? 'user' : 'assistant',
```

后端 `get_history_session` 把 LangGraph state 里每条消息序列化为 `{role: message.type, content}`。一次工具调用在**生成中途的 checkpoint** 里消息序列为：

```
human(用户问题) → ai(空正文, 仅挂载 tool_calls) → tool(结果JSON) ×N → ai(最终正文，尚未产生)
```

LangChain 消息 `.type` 取值为 `human / ai / tool / system`。旧映射把 **`tool`（ToolMessage 工具结果）和空正文的中转 `ai` 全部当成 `assistant`**，于是：

1. 每条工具结果 JSON 变成一个独立 AI 气泡
2. 仅发起 tool_call、正文为空的中转 AIMessage 变成"正在思考..."空气泡

### 第 3 步：发现连带隐患——续接轮询提前误判完成

续接轮询 `startGenerationResume` 里：

```js
const replyComplete = lastMsg.role === 'assistant' && (lastMsg.content || lastMsg.blocks?.length);
```

工具结果被当成 assistant、且 JSON content 非空 → **在工具调用阶段就误判 replyComplete=true**，提前用一堆工具 JSON 覆盖界面并停止轮询，真正的最终回复反而再也等不到。这与截图现象一致。

> 注：图正常跑完后，最终 checkpoint 的 messages 只保留 human + 最终 ai（实测已完成会话 history 仅 2 条），工具中间消息只存在于"生成中途"的 checkpoint——所以该 bug 只在生成中刷新续接时暴露。

---

## 三、解决方案

重写 `parseHistory`，从"无脑 map"改为"按 role 过滤后再映射"：

```js
for (const item of history) {
    // ① ToolMessage（工具结果）与 SystemMessage（系统提示词）不作为独立气泡展示
    if (item.role === 'tool' || item.role === 'system') continue;
    const role = item.role === 'human' ? 'user' : 'assistant';
    const content = extractContentText(item.content) || '';
    // ② 仅挂载 tool_calls、正文为空的中转 AIMessage 没有可展示内容，跳过
    if (role === 'assistant' && !content.trim()) continue;
    result.push(normalizeMessage({ id: generateId(), role, content, time: formatTime() }));
}
```

工具调用过程在流式时已通过 blocks 折叠展示；后端 history 不含 tool_calls 结构，续接重建时只呈现"用户问题 + 最终正文"，工具原始 JSON 不再泄漏到对话流。

---

## 四、验证结果

node 复刻五种 history 序列：

| 输入序列 | 修复后保留 | 结论 |
|---------|-----------|------|
| human + 空ai + tool×3（截图时刻，最终ai未出） | 仅 1 条 user | ✅ JSON 气泡/空气泡全滤除，续接继续等最终回复 |
| human + 空ai + tool + 最终ai | user + 最终 ai | ✅ |
| 纯对话 human + ai | user + ai | ✅ 不受影响 |
| 含 system | system 被滤，剩 user + ai | ✅ |
| 空数组 | 空 | ✅ |

`node --check app.js` 通过。真实已完成会话 `GET history` 实测为 human+ai 两条，与过滤逻辑一致。续接轮询的 replyComplete 在工具阶段不再误判（过滤后末尾是 user），只有最终 ai 正文落库才完成。

---

## 五、遗留与建议

| 项 | 说明 |
|----|------|
| 续接后工具过程不可见 | 后端 history 只存标准 content、不存 tool_calls，刷新续接后只展示最终正文，工具调用过程退化为不可见（与"深度思考块刷新后退化"同属 checkpoint 结构边界）；流式不刷新时工具块正常展示 |
| 浏览器旧缓存 | 需 Ctrl+F5 加载新 app.js |
| 若未来要在历史里还原工具链 | 后端 get_history_session 需额外返回 tool_calls/tool 消息结构，前端再按 blocks 重建，而非简单过滤 |

---

## 六、经验沉淀

1. **历史消息重建必须精确区分消息 type**——`非 human 即 assistant` 的偷懒映射在引入工具调用后必然出错，LangGraph 的 ai/tool/system 各有语义。
2. **完成态判定不能只看"最后一条非空"**——工具结果也是非空文本，会冒充"最终回复"；判定"回复完成"要基于角色与正文语义，过滤掉 tool 后再判断。
3. **中间态 checkpoint 与最终态结构不同**：工具/中转消息只在生成中途存在，图跑完后被收敛，这类 bug 只有在"中途刷新/中断续接"时才复现，排查时要主动构造中间态数据。
