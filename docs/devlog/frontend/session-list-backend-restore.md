# Mitta 刷新后会话列表丢失变"新对话" 排查与修复开发日志

> 涉及模块：后端会话列表接口（`src/routers/chat_router.py`、`src/service/chat_service.py`）、前端会话恢复（`resources/frontend/assets/js/app.js`）

---

## 一、问题现象

用户在前端实测：**刷新页面后直接变成新对话**——侧边栏会话列表清空，当前线程变成"新会话"。与此同时在界面里找不到"知识库入库"的入口。

排查中确认两个基础事实：

1. **后端数据没有丢**：PostgreSQL checkpoints 表存在 937 条 checkpoint，qianyi 名下最近 3 个会话（`thread_mtu87341_wccvou` 等）均可通过 `GET /api/chat/{thread_id}/history` 正常读到（HTTP 200）。
2. **线上服务器是旧版**：远程 `mitta/main` 停在 `57eb000`，本地领先 11 个提交（含知识库管理、权限校验、断连续接等）——线上前端**根本没有**知识库管理区。

---

## 二、排查过程

### 第 1 步：确认会话归属校验不是元凶

前端 `loadCurrentMessages` 先调 `apiGetHistory` 做归属校验（403 则移除会话）。怀疑过"旧账号（QQ/user）残留登录态 → 访问 qianyi 会话 → 403 → 会话被移除 → 新对话"：

- Redis 确实有历史遗留 `user:QQ:refresh_token`、`user:user:refresh_token`，PG `userinfo` 却只有 `qianyi`
- 但**最近真实会话（9 月 9 日）owner 全部是 `qianyi`**，用 qianyi token 调 history 返回 200
- 且鉴权 `get_current_user` 只验 JWT + Redis，不查 PG 用户表——旧 refresh 理论上能续签出"QQ 身份"，但 qianyi 自己的会话不会触发 403

**结论**：归属校验不是根因。

### 第 2 步：追到前端会话存储架构

前端会话列表与当前线程 ID 完全依赖 localStorage：

```js
const sessions = ref(cache.get(sessionCacheKey(STORAGE_KEY.SESSIONS), []));
const currentThreadId = ref(cache.get(sessionCacheKey(STORAGE_KEY.CURRENT_THREAD), null));
```

- `sessionCacheKey` 按 `user.userId` 隔离（`mitta_sessions_${userId}`）
- `CACHE_TTL_DAYS = 7`，缓存 7 天过期
- `onMounted` 里只要 `sessions` 为空或 `currentThreadId` 失效 → `createNewSession()` → **变新对话**

**根因确认**：**会话列表只存在于浏览器 localStorage，后端没有按用户列出会话的接口**。任何导致 localStorage 读不到的情况（7 天 TTL 过期、浏览器清理、换 origin、key 与 userId 不匹配）都会让前端误判"会话丢失"并新建对话。后端数据其实都在，但前端无从恢复。

> 补充：本地 `localhost:18000` 与服务器 `121.199.38.43` 是不同 origin，localStorage 天然隔离——之前服务器域名的会话在本地打开必然"消失"，属于浏览器机制而非数据丢失。

### 第 3 步：发现后端已有能力未暴露

`chat_service.get_user_sessions(user_id)` 早已实现（按 `metadata @> '{"user_id": ...}'` 过滤 checkpoint，返回 thread_id/title/last_updated），但**没有路由暴露给前端**——这是本次修复的抓手。

---

## 三、解决方案

### 3.1 后端：新增 `GET /api/chat/sessions`

`chat_router.py` 新增会话列表接口（挂在 generation-status 之前，避免被 `{thread_id}` 路径吞掉）：

```python
@router.get("/api/chat/sessions")
def list_sessions(current_user: TokenData = Depends(get_current_user)):
    sessions = chat_service.get_user_sessions(str(current_user.user_id))
    return {"ok": True, "data": sessions}
```

复用现成的 `get_user_sessions`，零新增业务逻辑。

### 3.2 前端：onMounted 时从后端兜底恢复

`app.js` 新增 `apiListSessions()`，并在 `onMounted` 中——**localStorage 会话为空时先向后端拉取**，拉到再建新对话：

```js
if ((!sessions.value || sessions.value.length === 0) && user.value && user.value.userId) {
    try {
        const remote = await apiListSessions();
        if (remote && remote.length > 0) {
            sessions.value = remote.map(s => ({
                id: s.thread_id,
                title: s.title || '新会话',
                messages: [],
                isBlank: false,
                createdAt: s.last_updated ? new Date(s.last_updated).getTime() : Date.now()
            }));
            saveSessions();
        }
    } catch (e) {
        console.warn('从后端恢复会话列表失败:', e.message);
    }
}
```

---

## 四、验证结果

| 检查项 | 方法 | 结果 |
|--------|------|------|
| 路由注册 | `python -c "from routers.chat_router import router"` | 9 条路由，含 `/api/chat/sessions` ✅ |
| 接口返回 | 重启后端后 `GET /api/chat/sessions`（qianyi token） | 200，返回 7 个会话（thread_id/title/last_updated）✅ |
| 无 token | 同上无 Authorization | 401 ✅ |
| 前端语法 | `node --check app.js` | 通过 ✅ |
| 历史会话恢复 | `GET /api/chat/{thread_id}/history`（owner=qianyi） | 200 ✅ |

**端到端语义**：刷新 → localStorage 空 → `apiListSessions` 从 checkpoint 恢复 7 个会话 → `currentThreadId` 指向最新会话 → `loadCurrentMessages` 加载消息 → 不再是新对话。

---

## 五、遗留与建议

| 项 | 状态 | 建议 |
|----|------|------|
| 本地后端需重启 | 已重启 | 本地 `MITTA_API_PORT=18000` 进程 reload 未检测到文件变更，需重启加载新路由；线上需推送后 CI/CD 自动构建 |
| 线上仍是旧版 | 未推送 | 知识库管理区等 11 个提交需 push 后才上线上；推送前先清代理（`git -c http.proxy= -c https.proxy= push mitta main`） |
| 旧浏览器缓存 | 待强刷 | 用户需 Ctrl+F5 强刷加载新 app.js |
| 旧登录态无 role | 需重新登录 | role 列添加前登录的 localStorage user 对象无 role 字段 → `isAdmin=false` → 知识库管理区不显示；重新登录 qianyi 即可 |

---

## 六、经验沉淀

1. **localStorage 只能当缓存，不能当唯一数据源**——会话/用户数据必须能从后端恢复，前端只做"缓存优先、后端兜底"。
2. **归属校验要区分"数据没了"和"权限不够"**：403 会触发前端移除会话，若用户账号混乱（历史遗留 refresh_token）可能误伤；本次确认 qianyi 会话 owner 正确后才排除该分支。
3. **已有能力先查再用**：`get_user_sessions` 早已实现只是没暴露，新增接口零业务成本——排查时先盘点 service 层已有什么。
4. **"刷新后数据消失"类问题，先验证后端数据是否真的在**（checkpoint/history 200），再定位到前端读取路径，避免在错误方向排查。
