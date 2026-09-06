# Mitta AI API 文档

> 基础 URL：`http://localhost:8000`（开发）或你的域名（生产，经 Nginx 代理）
> 所有接口返回 JSON，除 SSE 流式接口外。

## 目录

- [通用约定](#通用约定)
- [认证接口](#认证接口)
- [对话接口](#对话接口)
- [用户接口](#用户接口)
- [MCP 配置接口](#mcp-配置接口)
- [系统接口](#系统接口)
- [SSE 流式事件格式](#sse-流式事件格式)
- [错误码](#错误码)

---

## 通用约定

### 认证方式

除登录/注册/密码找回/健康检查外，所有接口需要在请求头携带 JWT：

```
Authorization: Bearer <access_token>
```

Token 有效期 15 分钟，过期时后端通过响应头 `X-New-Token` 自动续签（前端 `syncTokenFromHeaders` 捕获更新），无需前端主动调用 refresh 接口。

### 统一响应格式

```json
// 成功（无数据）
{ "ok": true }

// 成功（带消息）
{ "ok": true, "message": ["操作成功"] }

// 失败（HTTP 400）
{ "ok": false, "message": "失败原因" }
```

### 权限控制

- `/api/users/{user_id}/*` 接口：仅本人或 `admin` 角色可访问（`require_self_or_admin` 依赖）
- 会话相关接口：校验 `thread_id` 归属，非本人返回 403

---

## 认证接口

### POST /api/login

用户登录，返回 JWT token 和用户信息。

**请求体**

```json
{
  "userId": "string (必填，用户ID)",
  "password": "string (必填，密码)"
}
```

**成功响应** `200`

```json
{
  "ok": true,
  "token": "eyJhbGciOiJIUzI1NiIs...",
  "user_info": {
    "user_id": "qianyi",
    "username": "郭建豪",
    "role": "学员",
    "avatar": null,
    "create_time": "2026-01-01T00:00:00"
  }
}
```

**失败响应** `400`

```json
{ "ok": false, "message": "用户 ID 或密码错误" }
```

---

### POST /api/register

用户注册。

**请求体**

```json
{
  "userName": "string (必填，用户名)",
  "userId": "string (必填，用户ID，唯一)",
  "password": "string (必填，最长64字符)"
}
```

**成功响应** `200`

```json
{ "ok": true, "message": ["注册成功"] }
```

**失败响应** `400`

```json
{ "ok": false, "message": "用户ID已存在" }
```

---

### POST /api/recover

密码找回/重置（根据 user_id 直接设置新密码，当前版本无邮箱验证）。

**请求体**

```json
{
  "userId": "string (必填)",
  "newPassword": "string (必填，新密码)"
}
```

**成功响应** `200`

```json
{ "ok": true }
```

---

### POST /api/logout

用户登出，删除 Redis 中的 access + refresh token，实现即时失效。

**请求头**：`Authorization: Bearer <token>`

**成功响应** `200`

```json
{ "ok": true }
```

> 无状态 JWT 本身无法作废，通过 Redis 白名单机制实现：登录时 token 存入 Redis，每次请求校验 Redis 中是否存在；登出时删除 key，下次请求校验失败即 401。

---

## 对话接口

### POST /api/chat/

发送消息，SSE 流式返回 AI 回复。

**请求头**：`Authorization: Bearer <token>`，`Content-Type: application/json`

**请求体**

```json
{
  "query": "string (必填，用户输入)",
  "thread_id": "string (必填，会话ID，用于 LangGraph Checkpointer)",
  "file_ids": [1, 2],
  "thinking_mode": false,
  "reasoning_effort": "low"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| query | string | 是 | 用户输入文本 |
| thread_id | string | 是 | 会话 ID，多轮对话恢复依赖此值 |
| file_ids | int[] | 否 | 已上传文件 ID 列表，解析内容拼接到 query |
| thinking_mode | bool | 否 | 是否开启深度思考，默认 false |
| reasoning_effort | string | 否 | 推理强度 `low`/`medium`/`high`，仅 thinking_mode=true 时生效，默认 low |

**响应**：`text/event-stream`，SSE 事件格式见 [SSE 流式事件格式](#sse-流式事件格式)

**错误**：`403` 无权使用该会话；`401` 未认证

---

### GET /api/chat/{thread_id}/history

获取会话历史消息。

**路径参数**：`thread_id` - 会话 ID

**成功响应** `200`

```json
{
  "messages": [
    {
      "role": "user",
      "content": "你好",
      "timestamp": "2026-09-06T10:00:00"
    },
    {
      "role": "assistant",
      "content": "你好！有什么可以帮你的？",
      "timestamp": "2026-09-06T10:00:01"
    }
  ]
}
```

**错误**：`403` 无权访问；`404` 会话不存在

---

### DELETE /api/chat/{thread_id}

删除会话及其历史消息。

**路径参数**：`thread_id` - 会话 ID

**成功响应** `200`

```json
{ "ok": true, "message": ["会话删除成功"] }
```

---

### POST /api/chat/{thread_id}/stop

标记停止当前会话的回复生成。实际停止由前端通过 `AbortController` 关闭 SSE 连接实现。

**成功响应** `200`

```json
{ "ok": true, "message": "已标记停止，前端将关闭连接" }
```

---

### POST /api/chat/upload

上传文件（multipart/form-data），保存后立即解析文本内容并缓存。

**请求头**：`Authorization: Bearer <token>`，`Content-Type: multipart/form-data`

**表单字段**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| file | File | 是 | 上传的文件（txt/md/csv/json/py/js/pdf 等） |
| thread_id | string | 否 | 关联会话 ID，None 表示全局文件 |

**成功响应** `200`

```json
{
  "ok": true,
  "data": {
    "file_id": 1,
    "file_name": "document.pdf",
    "file_type": "application/pdf",
    "file_size": 102400,
    "parsed": true,
    "content_length": 5000
  }
}
```

> 单文件上限 10MB，base64 存储在 MySQL `user_files` 表。

---

### DELETE /api/files/{file_id}

删除用户上传的文件，同步清除解析缓存。

**路径参数**：`file_id` - 文件 ID

**成功响应** `200`

```json
{ "ok": true, "message": ["文件删除成功"] }
```

---

## 用户接口

> 所有 `/api/users/{user_id}/*` 接口需认证，且仅本人或 admin 可访问。

### GET /api/users/{user_id}/profile

获取用户个人信息。

**成功响应** `200`

```json
{
  "ok": true,
  "data": {
    "user_id": "qianyi",
    "username": "郭建豪",
    "avatar": null,
    "assistant_style": "default",
    "theme": "default"
  }
}
```

---

### PUT /api/users/{user_id}/profile

更新用户个人信息。

**请求体**

```json
{
  "username": "string (可选)",
  "avatar": "string (可选，base64或URL)",
  "assistant_style": "string (可选)"
}
```

**成功响应** `200`

```json
{ "ok": true, "message": ["个人信息更新成功"] }
```

---

### PUT /api/users/{user_id}/password

修改密码（需验证原密码）。

**请求体**

```json
{
  "old_password": "string (必填)",
  "new_password": "string (必填)"
}
```

**成功响应** `200`

```json
{ "ok": true, "message": ["密码修改成功"] }
```

**失败** `400`：`{ "ok": false, "message": "原密码错误" }`

---

### GET /api/users/{user_id}/system-prompt

获取用户自定义 System Prompt。

**成功响应** `200`

```json
{ "ok": true, "data": { "content": "你是一个专业的编程助手..." } }
```

---

### PUT /api/users/{user_id}/system-prompt

更新用户自定义 System Prompt（空字符串表示清除）。更新后自动失效该用户所有会话的检索缓存。

**请求体**

```json
{
  "content": "string (必填，最多3000字)"
}
```

**成功响应** `200`

```json
{ "ok": true, "message": ["自定义设定更新成功"] }
```

---

### GET /api/users/{user_id}/theme

获取用户主题配置。

**成功响应** `200`

```json
{ "ok": true, "data": { "theme": "default" } }
```

---

### PUT /api/users/{user_id}/theme

更新用户主题配置。

**请求体**

```json
{
  "theme": "default | dark | ocean | sunset | forest | lavender"
}
```

**成功响应** `200`

```json
{ "ok": true, "message": ["主题更新成功"] }
```

---

### GET /api/users/{user_id}/memory

获取用户长期记忆（LangGraph Store，跨会话持久）。

**成功响应** `200`

```json
{
  "memories": [
    {
      "id": "mem_xxx",
      "content": "用户偏好Java后端开发",
      "created_at": "2026-09-01T00:00:00"
    }
  ]
}
```

---

### GET /api/users/{user_id}/sessions

获取用户的所有会话列表。

**成功响应** `200`

```json
[
  {
    "thread_id": "session_abc123",
    "title": "Python学习",
    "last_message": "好的，我来解释...",
    "updated_at": "2026-09-06T10:00:00",
    "message_count": 15
  }
]
```

---

### GET /api/users/{user_id}/files

列出用户上传的文件。

**查询参数**：`thread_id`（可选，按会话筛选）

**成功响应** `200`

```json
{
  "ok": true,
  "data": [
    {
      "file_id": 1,
      "file_name": "doc.pdf",
      "file_size": 102400,
      "upload_time": "2026-09-06T10:00:00",
      "thread_id": null
    }
  ]
}
```

---

## MCP 配置接口

> MCP 配置存储在 PostgreSQL `user_mcp_servers` 表，按用户隔离。保存后通过 hash 检测自动热重载，无需重启后端。

### GET /api/mcp/config

获取当前用户的 MCP 配置。

**成功响应** `200`

```json
{
  "ok": true,
  "data": {
    "mcp_servers": [
      {
        "name": "filesystem",
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/app/user_files"]
      }
    ],
    "storage": "postgresql",
    "user_id": "qianyi"
  }
}
```

---

### PUT /api/mcp/config

更新当前用户的 MCP 配置。保存后下次对话自动生效（hash 检测重建图）。

**请求体**

```json
{
  "mcp_servers": [
    {
      "name": "filesystem",
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/app/user_files/qianyi"]
    },
    {
      "name": "fetch",
      "type": "sse",
      "url": "https://mcp.example.com/sse"
    }
  ]
}
```

**安全校验**：
- 命令白名单：`npx` / `uvx` / `node` / `python` / `python3` / `pipx`
- Windows 路径自动转换为 Linux 容器内路径
- filesystem 限制在 `/app/user_files/{user_id}/` 下
- 禁止敏感环境变量（PATH/HOME 等）
- sse 类型禁止内网地址

**成功响应** `200`

```json
{
  "ok": true,
  "detail": "配置已保存，下次对话时自动生效",
  "data": { "count": 2, "user_id": "qianyi" }
}
```

---

### POST /api/mcp/reload

主动重载当前用户的 MCP 配置：清除图缓存并关闭旧 MCP 连接，下次对话立即重建。

**成功响应** `200`

```json
{
  "ok": true,
  "detail": "MCP 配置已重载，新工具将在下次对话中生效",
  "cleared": true,
  "user_id": "qianyi"
}
```

> `cleared=false` 表示该用户无缓存（从未构建过用户图），不影响功能。

---

### DELETE /api/mcp/config/{server_name}

删除当前用户的单个 MCP 服务器配置。

**路径参数**：`server_name` - MCP 服务器名称

**成功响应** `200`

```json
{ "ok": true, "detail": "已删除 MCP 服务器: filesystem" }
```

---

## 系统接口

### GET /health

健康检查，返回数据库连接状态。

**成功响应** `200`

```json
{ "status": "ok", "db": true }
```

---

### GET /mcp

内置 MCP 服务器端点（FastMCP），外部 MCP 客户端（如 Claude Desktop）可通过 `http://localhost:8000/mcp` 调用 agent 能力。

---

## SSE 流式事件格式

`POST /api/chat/` 返回 `text/event-stream`，每个事件以 `data: <json>\n\n` 格式发送。

### 事件类型

| 事件 | data 格式 | 说明 |
|------|-----------|------|
| 深度思考 | `{"reasoning": "思考内容..."}` | DeepSeek reasoning_content，流式增量 |
| 文本内容 | `{"content": "回复文本..."}` | AI 回答文本，流式增量 |
| 工具调用开始 | `{"tool_call_start": {"name": "fetch", "args": {"url": "..."}}}` | LLM 决定调用工具 |
| 工具调用结束 | `{"tool_call_end": {"name": "fetch", "content": "工具结果摘要(截断300字)"}}` | 工具执行完成 |
| 错误 | `{"error": "错误信息", "error_type": "ExceptionName"}` | 图执行异常 |
| 结束 | `"[DONE]"` | 流式输出结束 |

### 前端解析示例

```javascript
const reader = response.body.getReader();
const decoder = new TextDecoder();
let buffer = '';

while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });
  const lines = buffer.split('\n\n');
  buffer = lines.pop();
  for (const line of lines) {
    if (!line.startsWith('data: ')) continue;
    const data = JSON.parse(line.slice(6));
    if (data === '[DONE]') break;
    if (data.reasoning) { /* 追加到思考面板 */ }
    if (data.content) { /* 追加到回复内容 */ }
    if (data.tool_call_start) { /* 显示工具调用卡片 */ }
    if (data.tool_call_end) { /* 关闭工具调用卡片 */ }
    if (data.error) { /* 显示错误 */ }
  }
}
```

---

## 错误码

| HTTP 状态码 | 场景 | 响应体 |
|------------|------|--------|
| 200 | 成功 | `{"ok": true, ...}` |
| 400 | 业务失败（参数错误/密码错误等） | `{"ok": false, "message": "..."}` |
| 401 | 未认证 / token 过期 / token 不在 Redis 白名单 | `{"detail": "Not authenticated"}` |
| 403 | 无权限（会话归属/用户越权） | `{"detail": "无权访问该会话"}` |
| 404 | 资源不存在 | `{"detail": "Not Found"}` |
| 422 | 请求体验证失败（Pydantic） | `{"detail": [{"loc": ["body","userId"], "msg": "Field required"}]}` |
| 429 | 请求限流（/api/chat/ 每IP 60秒30次） | `{"detail": "Too many requests"}` |
| 500 | 服务器内部错误 | `{"detail": "Internal Server Error"}` |

### Token 自动续签

当 access token 过期时，后端校验逻辑会自动用 Redis 中的 refresh token 签发新 access token，并通过响应头 `X-New-Token` 返回。前端应在每次请求后检查该头并更新本地存储：

```javascript
function syncTokenFromHeaders(headers) {
  const newToken = headers.get('X-New-Token');
  if (newToken) {
    localStorage.setItem('token', newToken);
  }
}
```
