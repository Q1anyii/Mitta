# Mitta 自定义 System Prompt 功能未生效 排查与修复开发日志

> 涉及模块：用户路由（`src/routers/user_router.py`）、请求模型（`src/schemas/request_schemas/user_schema.py`）、用户档案服务（`src/service/user_profile_service.py`）、前端个人设置页（`resources/frontend/assets/js/app.js`）
> 关联提交：`7921953`（fix: system prompt 并入 profile 接口、用户名实时同步、删除会话与 sqlite MCP 修复）

---

## 一、问题现象

个人设置页面提供"助手风格"自定义输入框，用户填写后点击保存，但 AI 行为没有任何变化：

- 后端 `user_router` 中独立实现的 `get_user_system_prompt_api` / `update_user_system_prompt`（GET/PUT `/api/users/{user_id}/system-prompt`）**从未被调用**
- 设置页保存时只触发了 `update_user_profile`（PUT `/api/users/{user_id}/profile`），system_prompt 通道完全没有生效
- 前端的"助手风格"字段写的是 `assistant_style`，而对话系统读取的是 `system_prompt`——两个字段名对不上，数据链路从源头断裂

---

## 二、排查过程

### 第 1 步：确认前端点击保存走了哪个接口

个人设置页"保存"按钮绑定 `saveProfile`：

```js
async function saveProfile() {
    const res = await fetch(`${API_BASE}/api/users/${user.value.userId}/profile`, {
        method: 'PUT',
        body: JSON.stringify({
            username: profileForm.value.username,
            avatar: profileForm.value.avatar,
            system_prompt: profileForm.value.system_prompt || '',
        }),
    });
    ...
}
```

- 保存走的是 **profile 接口**（用户名/头像/系统提示词一起提交）
- 独立 `/system-prompt` 接口在前端**无任何调用点**——两个接口互相独立，前端从未触发它

### 第 2 步：追查"助手风格"字段断裂

- 前端设置表单最初存 `assistant_style` 字段
- 后端对话图读取的是用户档案里的 `system_prompt`
- **根因一**：字段名不一致（`assistant_style` vs `system_prompt`），前端写的数据后端读不到
- **根因二**：即使后端有独立 `/system-prompt` 接口，前端也没有接入，等于两条通道都不通

### 第 3 步：明确两条可选修复路径

| 方案 | 做法 | 代价 |
|------|------|------|
| 方案一 | 前端保存时改为调用独立 `/system-prompt` 接口 | 需要新增一次网络请求，且与 profile 保存解耦，逻辑分散 |
| **方案 A（采纳）** | 删除独立 `/system-prompt` 接口，把 `system_prompt` **并入 profile 接口**，与用户名/头像同表单同接口 | 一个接口统一管理用户自定义信息，前端零新增请求 |

最终采纳方案 A：项目目前只有设置页"助手风格"一个入口，没有独立 system prompt 管理页面的需求，保留两个接口只会造成职责混乱。

---

## 三、解决方案

### 1. 请求模型（`user_schema.py`）

- `ProfileUpdateRequest` 增加 `system_prompt: Optional[str]` 字段
- 删除 `assistant_style` 字段与独立的 `SystemPromptUpdateRequest` 模型

### 2. 用户路由（`user_router.py`）

- **GET `/profile`**：返回结构增加 `system_prompt`（不存在时返回空串），前端打开设置页能回显已保存的自定义设定
- **PUT `/profile`**：
  - 增加 3000 字上限校验
  - 将 `system_prompt` 透传给 `user_profile_service.update_basic_info()`
  - **system_prompt 变更时清空该用户所有会话的检索缓存**（防止旧 prompt 生成的缓存污染新回答）——清除失败不影响主流程，缓存 TTL 到期会自然过期
  - 新增 `[profile-debug]` 调试日志，记录收到的请求体
- **删除**独立的 GET/PUT `/system-prompt` 接口

### 3. 用户档案服务（`user_profile_service.py`）

`update_basic_info()` 增加 `system_prompt` 参数：

```python
def update_basic_info(self, user_id, username=None, avatar=None, system_prompt=None) -> bool:
    fields = {}
    if username is not None:
        fields["username"] = username
    if avatar is not None:
        fields["avatar"] = avatar
    if system_prompt is not None:   # None 表示不更新，空字符串表示清除
        fields["system_prompt"] = system_prompt
    if not fields:
        return False
    self._upsert_profile(user_id, fields)
    return True
```

**关键约定**：`system_prompt=None` 表示不更新该字段；`""`（空字符串）表示清除已保存的自定义设定。这样用户只改用户名时不覆盖已存的 system_prompt。

### 4. 前端（`app.js`）

`saveProfile` 改为单接口提交（用户名 + 头像 + system_prompt），保存成功后：
- 同步更新本地 `user` 缓存
- 同步刷新 `AI_CALL_NAME` 称呼缓存（否则主页问候语仍显示旧用户名）
- 成功后关闭弹窗

---

## 四、调试插曲（两次"没生效"的排查）

### 第一次反馈"没生效"

- 表象：线上测试 system_prompt 无变化
- 排查结论：**改动从未推送，用户测试的是线上旧版**。本地改完代码不推送到服务器，线上行为不可能变化——先确认代码版本是否真的到了被测环境

### 第二次反馈"logger.info 没被运行到"

- 表象：`update_basic_info` 里 `logger.info(f"{system_prompt}")` 没有任何输出，以为分支没走到
- 排查结论：**本地后端进程没有 `--reload`**，改完代码不重启进程，跑的仍是旧代码；同时前端 file:// 打开时浏览器缓存了旧 JS，也看不到新逻辑。用户 Ctrl+F5 强刷 + 重启后端后确认"好了，已实现"
- 经验：本地联调必须**重启后端 + 强刷前端**，二者缺一都会误判"没生效"

---

## 五、验证结果

- `PUT /api/users/{user_id}/profile` 携带 `system_prompt` → 返回成功，`GET /profile` 能读回 ✅
- 只更新用户名（`system_prompt=None`）→ 不覆盖已存 system_prompt ✅
- 传 `system_prompt=""` → 清除自定义设定 ✅
- 超过 3000 字 → 返回错误 ✅
- system_prompt 变更 → 该用户所有会话检索缓存被清除 ✅
- 独立 `/system-prompt` 接口已删除，前端无残留调用 ✅

---

## 六、遗留与建议

| 项 | 状态 | 建议 |
|----|------|------|
| `[profile-debug]` 调试日志 | 保留 | 稳定后可降级或移除，避免生产日志噪音 |
| 全局 system_prompt 覆盖规则 | 待确认 | 当前是"用户自定义优先"，需确认与对话图默认 system prompt 的合并顺序 |
| 多用户 system_prompt 隔离 | 正常 | 按 user_id 存储，天然隔离 |
| 错误提示文案 | 已修正 | "不能超过 3000 字"与代码校验值一致（此前有过文案/校验不一致） |

---

## 七、经验沉淀

1. **接口"存在"不等于"被调用"**——排查功能不生效，第一步是确认前端请求到底打到了哪个接口，而不是默认接口本身有问题
2. **同表单同接口**：用户自定义信息（用户名/头像/提示词）用同一个 profile 接口统一管理，避免维护多个独立通道造成字段断裂
3. **None 与空字符串语义必须显式区分**：`None=不更新`、`""=清除`，否则"只想改用户名"会把提示词一起清掉
4. **缓存失效要跟随配置变更**：system_prompt 变了还命中旧缓存，等于新配置不生效——这是"改了没用"的高频隐藏原因
5. **本地联调三件套**：重启后端（无 --reload 时必须手动重启）+ 强刷前端（file:// 有缓存）+ 确认被测版本，缺一不可
