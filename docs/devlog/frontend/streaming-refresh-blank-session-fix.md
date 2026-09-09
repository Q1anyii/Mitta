# Mitta 流式生成刚开始就刷新 → 跳成空白"新会话" 排查与修复开发日志

> 涉及模块：前端消息加载与自动续接（`resources/frontend/assets/js/app.js` 的 `loadCurrentMessages` / `startGenerationResume`）
> 关联：与 `session-list-backend-restore.md`（localStorage 列表丢失恢复）是两个不同问题，本篇针对"生成中刷新"时序

---

## 一、问题现象

用户实测复现路径非常明确：

1. 发送一条消息，**在 AI 刚开始生成（最开始发送时）点击浏览器刷新**
2. 主区域**跳成空白的"新会话"界面**，但侧边栏仍高亮原来的会话（currentThreadId 没变）
3. 再刷新一次，才显示出生成到一半的消息（工具调用、部分回复）

上一轮提交（`3ad49dd`，localStorage 为空时从后端恢复会话列表）**没有命中**——因为生成中刷新时 localStorage 里的 sessions / currentThreadId 都还在，根本不会走"列表恢复"分支。

---

## 二、排查过程

### 第 1 步：确认发送时会话已同步落 localStorage

`sendMessage` 在用户消息 push 后**同步**调用了 `saveMessages()` + `saveSessions()`，空 AI 占位 push 后又 `saveMessages()` 一次。所以生成中刷新时，本地缓存一定有"用户消息 + 空 AI 占位"，sessions 列表也在——**排除"列表没存"**。

### 第 2 步：定位 loadCurrentMessages 的消息源选择（根因①）

刷新后 `onMounted → loadCurrentMessages`，修复前逻辑：

```js
const lastCached = cached[cached.length - 1];                 // 空 AI 占位
const lastIncomplete = isIncompleteAiMsg(lastCached);         // true（空 content + 空 blocks）
if (cached.length > 0 && !lastIncomplete) {
    messages.value = cached;                                  // 用本地
} else {
    messages.value = parseHistory(history);                   // ← 走这里，用后端
}
```

而"刚开始生成"的瞬间，LangGraph 后台线程**还没提交第一个 checkpoint**，后端 `get_history_session` 返回 **空数组 `[]`**（实测 HTTP 200，`get_state` 拿到空 StateSnapshot、messages 为空）：

```js
messages.value = parseHistory([]);  // = [] → 界面被清空 → 看起来就是"新会话"
```

**根因①：本地末尾是在途空占位时，代码无条件用后端 history 兜底；而此刻后端 history 恰好为空，反而把"至少还有用户消息"的本地缓存清空了。**

### 第 3 步：定位续接轮询为何没救回来（根因②）

修复前续接触发条件：

```js
const lastMsg = messages.value.length ? messages.value[messages.value-1] : null;
const replyStillIncomplete = isIncompleteAiMsg(lastMsg);  // messages 已被清空 → lastMsg=null → false
if (lastIncomplete && replyStillIncomplete) {             // true && false → 不启动
    startGenerationResume(...);
}
```

**根因②：界面被空 history 清空后 `lastMsg=null`，续接轮询的启动条件依赖"messages 最后一条仍是在途占位"，于是永不启动**——只能等用户再刷新一次（那时 checkpoint 已落库，history 非空，才显示半截内容）。

### 第 4 步：后端时序交叉验证

- `get_history_session`：thread 未落 checkpoint 时返回 `[]`（HTTP 200，不抛错）
- `is_generation_active`：查内存字典 `_active_generations`，该字典在后台 worker `start()` **之前同步注册**——**不依赖 checkpoint**，所以生成刚开始也能正确返回 `generating=true`
- 结论：前端只要"保留本地 + 用 generation-status 驱动续接"，就能等后台跑完后自动补全，无需用户二次刷新

### 第 5 步：发现第三种在途形态被漏判

旧 `isIncompleteAiMsg` 只认"空 content + 空 blocks"。但生成中还可能是：

- 形态 A：空 content + 空 blocks（刚发送、模型未吐字）——旧逻辑能认
- 形态 B：空 content + **已有 reasoning/tool block**（思考/工具调用阶段，截图即此形态）——旧逻辑因 `blocks.length>0` 判为"已完成"，**不续接**，刷新后冻结在工具调用中途
- 形态 C：已有部分正文 content（流式吐字中途刷新）——本地内容无法自证是否完成，需问后端 generation-status

---

## 三、解决方案

### 3.1 在途判定扩展为四形态

```js
const isIncompleteAiMsg = (m) => {
    if (!m || m.role !== 'assistant') return false;
    if (m.interrupted || m.content === '（回复中断，请重新生成）') return true;
    if (m.content && m.content.trim()) return false;          // 已有正文
    if (!m.blocks || m.blocks.length === 0) return true;      // 形态 A：纯空占位
    const hasText = m.blocks.some(b => b.type === 'text' && b.content && b.content.trim());
    return !hasText;                                          // 形态 B：只有思考/工具块，正文未产出
};
```

### 3.2 消息源选择：绝不用空 history 清空本地

```js
if (lastIncomplete && remoteReplyComplete) {
    messages.value = remoteMsgs;        // 后台已补全落库 → 用后端完整回复
} else if (lastIncomplete) {
    messages.value = cached.length ? cached : remoteMsgs;  // 后端还空 → 保留本地（用户消息+占位）
} else if (cached.length) {
    messages.value = cached;
} else {
    messages.value = remoteMsgs;
}
```

### 3.3 续接启动不再依赖"被清空后的 messages"

- 形态 A/B：保留本地后末尾仍是在途占位 → 直接 `startGenerationResume`
- 形态 C：本地末尾 assistant 已有部分正文，异步查一次 `generation-status`，仍在生成则启动续接
- catch（history 网络失败回退本地）分支同样补查 `generation-status`，弱网下也不丢续接

续接轮询内部不变：每 2s 拉 history + status，等后台提交完整 assistant 回复后渲染覆盖（后端只存标准消息，补全后工具穿插/思考块退化为纯文本正文，属既有取舍）。

---

## 四、验证结果

### 4.1 判定逻辑单测（node 复刻六种场景）

| 场景 | 界面是否空白 | 是否启动续接 | 结论 |
|------|------------|------------|------|
| 形态A 刚发送空占位 + 后端空 history | 有内容（2 条） | 是 | ✅ 修复命中（原 bug 场景） |
| 形态B 工具块无正文 + 后端空 | 有内容 | 是 | ✅ |
| 形态C 部分正文 + 后端空 | 有内容 | 走 status 兜底 | ✅ |
| 后端已补全完整回复 | 用后端 | 否 | ✅ |
| 本地无缓存 + 后端有历史 | 用后端 | 否 | ✅ |
| 已完成会话刷新 | 用本地 | 否 | ✅ |

### 4.2 后端接口实测（本地 18000）

- `GET /api/chat/<不存在的thread>/history` → `[]`，HTTP 200（不抛错）
- `GET /api/chat/<不存在的thread>/generation-status` → `{"generating":false}`，HTTP 200（不 403）
- `node --check app.js` 通过

### 4.3 端到端时序（修复后）

生成中刷新 → history=[] 但**保留本地用户消息+占位（不再空白）** → 本地末尾在途 → 启动续接（status=generating）→ 后台线程跑完提交 checkpoint → history 出现完整 assistant → 自动渲染完整回复，全程无需二次刷新。

---

## 五、遗留与建议

| 项 | 状态 | 说明 |
|----|------|------|
| 浏览器旧缓存 | 需 Ctrl+F5 | 加载新 app.js 后生效 |
| 续接后工具/思考块退化 | 既有取舍 | checkpoint 只存标准 content，补全后为纯文本正文，正文内容完整 |
| 极端情况 POST 未到后端就刷新 | 可手动重发 | 此时 `_active_generations` 未注册、status=false，轮询停止；但本地用户消息保留，不会空白 |
| MCP filesystem 路径报错（截图中） | 另案 | 本地 Windows 路径与默认配置里的容器路径 `/app/user_files/...` 不一致，属 MCP 默认配置适配问题，与本 bug 无关 |

---

## 六、经验沉淀

1. **"用后端兜底本地"前必须判断后端数据是否为空**——在途场景下后端可能比本地更"旧"（checkpoint 尚未提交），空数据覆盖本地就是这次的直接元凶。
2. **状态恢复的启动条件不能依赖"被恢复动作改写过的状态"**——续接轮询用"messages 已被清空后的 lastMsg"做判据，等于让故障自我屏蔽；启动信号应来自刷新前持久化的本地末尾状态。
3. **"是否在生成"的权威来源是后端生成注册表，不是前端消息内容**——前端无法从半截正文判断是否完成，`generation-status` 基于内存注册表、不受 checkpoint 落库时机影响，是更可靠的信号。
4. 复现时序类 bug 要卡准时间窗：本例只在"发送后、首个 checkpoint 落库前"刷新才出现，晚几秒刷新就正常——时间窗决定了排查方向必须落在"后端尚无数据"的分支。
