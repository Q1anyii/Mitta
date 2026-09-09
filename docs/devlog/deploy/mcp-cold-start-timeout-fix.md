# Mitta 本地 MCP 冷启动连接超时全跳线修复开发日志

> 涉及模块：MCP 客户端（`src/mcp_client/client.py`）、聊天服务（`src/service/chat_service.py`）、启动入口（`src/main.py`）、默认配置（`resources/config/mcp_servers.json`）、`.gitignore`
> 关联提交：`5a2b2fd`

---

## 一、问题现象

本地 Windows 开发环境（conda env `langchain1.2`）重启后端后，**全部 8 台 MCP 服务器连接失败**：

```
20:01:14.070 | SUCCESS | config:validate_config:117 - 环境变量校验通过，所有必填项已配置
...
20:01:29.073 | WARNING | mcp_client.client:_connect_one:289 - MCP 服务器 [filesystem] 连接失败，已跳过
20:01:29.104 | WARNING | mcp_client.client:_connect_one:289 - MCP 服务器 [sequential-thinking] 连接失败，已跳过
20:01:29.120 | WARNING | mcp_client.client:_connect_one:289 - MCP 服务器 [memory] 连接失败，已跳过
20:01:29.136 | WARNING | mcp_client.client:_connect_one:289 - MCP 服务器 [markitdown] 连接失败，已跳过
20:01:29.136 | WARNING | mcp_client.client:_connect_one:289 - MCP 服务器 [context7] 连接失败，已跳过
20:01:29.183 | WARNING | mcp_client.client:_connect_one:289 - MCP 服务器 [dbhub] 连接失败，已跳过
20:01:29.198 | WARNING | mcp_client.client:_connect_one:289 - MCP 服务器 [chroma] 连接失败，已跳过
20:01:29.199 | WARNING | mcp_client.client:_connect_one:289 - MCP 服务器 [basic-memory] 连接失败，已跳过
```

辅助现象：
- `chroma` 额外报 `Failed to parse JSONRPC message from server ... input_value='Successfully initialized Chroma client\r'`
- `markitdown` 报 Windows 管道 `stdout.flush()` `OSError: [Errno 22] Invalid argument`
- `basic-memory` 启动时打印大段 ASCII 艺术字 banner
- 手动在 shell 里跑 `uvx mcp-server-fetch --help` 等命令**全部正常**

---

## 二、排查过程

### 第 1 步：从时间线锁定嫌疑——15 秒

日志里启动时刻 `20:01:14` 到全部失败 `20:01:29` **恰好 15 秒**，与 `init_mcp_holders` 的默认 `timeout: int = 15` 完全吻合。这是首要嫌疑。

### 第 2 步：逐台手动握手，排除"服务器本身坏"

用裸 asyncio 管道直接向每个 server 发 JSON-RPC `initialize`：

| 服务器 | 首次结果 | 说明 |
|--------|---------|------|
| npx 系（filesystem/sequential-thinking/memory/context7） | 成功 | npm 全局包已装，秒回 |
| markitdown（uvx） | 首次超时 | 日志显示 `Installed 71 packages` —— uvx 首次运行下载依赖 |
| basic-memory（uvx） | 首次超时 | `Downloading pyright/litellm/sqlalchemy...` 数十 MB 下载 |
| chroma（uvx） | 首次失败/二次成功 | 首次需下载 grpcio/onnxruntime/chromadb（22.5MiB 等） |
| dbhub（npx） | 启动即退出 | `ERROR: Database connection configuration is required` |

**结论**：服务器本体全部健康，问题在**启动耗时**——uvx/npx 首次运行要下载依赖，远超 15 秒连接超时。

### 第 3 步：区分两类"误报"噪音，避免被带偏

- **chroma 横幅污染**：`chroma_mcp/server.py:660` 无条件 `print("Successfully initialized Chroma client")` 到 stdout，首次初始化时污染 JSON-RPC 通道。实测 mcp 库 `stdout_reader` 对非 JSON 行会 send exc 后继续（`continue`），**横幅不致命**——chroma 连续 3 次连接稳定成功（13 tools）。
- **basic-memory banner**：分离流测试确认 banner 走 **stderr**（stdout 是合法 JSON），无害。
- **markitdown Errno 22**：Windows 管道 flush 报错出现在子进程侧，但第二次连接成功（1 tool），属 uvx 冷启动下载中断的连带噪音。

### 第 4 步：dbhub 配置缺陷（独立真 bug）

`@bytebase/dbhub` 无参数启动即退出：`Database connection configuration is required`。默认配置里它没有 DSN，属于**配置缺失导致启动即失败**，与超时无关。

### 第 5 步：确认所有调用点超时值

不止默认参数，还查到两处显式/外层限制：
- `chat_service.py:277` `init_mcp_holders(user_servers, timeout=20)` —— 显式 20s
- `main.py:47` `.result(timeout=35)` —— 外层等待 35s

只改默认参数会被这两处截断，必须一起调。

---

## 三、解决方案

| 文件 | 修改 |
|------|------|
| `src/mcp_client/client.py` | `init_mcp_holders` 默认 `timeout: int = 15` → `120`，docstring 同步说明冷启动场景 |
| `src/service/chat_service.py` | 用户图构建 `init_mcp_holders(user_servers, timeout=20)` → `120` |
| `src/main.py` | 初始连接 `.result(timeout=35)` → `130`（给 120s 连接 + 缓冲） |
| `resources/config/mcp_servers.json` | dbhub args 追加 `--demo`（demo 模式用内存 SQLite 示例库，无需 DSN） |
| `.gitignore` | 追加 `resources/chroma_data/`、`resources/local_data.db` 等本地运行时数据（测试产物，不入库） |

---

## 四、验证结果

修复后按后端真实连接路径（mcp 库 `stdio_client` + `ClientSession.initialize` + `list_tools`）逐台实测**全部 12 台默认配置**：

```
[OK] filesystem: 14 tools
[OK] fetch: 1 tools
[OK] sqlite: 6 tools
[OK] sequential-thinking: 1 tools
[OK] memory: 9 tools
[OK] time: 2 tools
[OK] markitdown: 1 tools
[OK] context7: 2 tools
[OK] playwright: 24 tools
[OK] dbhub: 2 tools
[OK] chroma: 13 tools
[OK] basic-memory: 21 tools
```

合计 **96 个工具**全部加载成功，冷启动（依赖已缓存）单台秒连，无一台被跳过。

---

## 五、经验沉淀

1. **MCP 连接失败先看时间戳差值**：日志里启动时刻与"已跳过"时刻的差值如果等于代码里的 timeout 值，那就是超时实锤，不用怀疑服务器本体
2. **uvx/npx 冷启动是隐藏杀手**：首次运行下载依赖可达数十 MB（chromadb 22.5MiB、markitdown 71 包），15~20s 超时必然全灭；调大超时 + 预热依赖缓存双管齐下
3. **横幅/日志不一定致命**：服务器往 stdout 打印横幅（chroma）或 banner（basic-memory）时，先分离 stdout/stderr 实测确认，mcp 库对非 JSON 行有容错（send exc + continue），不要看到 parse error 就以为是根因
4. **改默认参数要全链路核对**：函数默认值、显式传参、外层 `.result(timeout=...)` 三处都要查，任何一处截断都会让修复失效
5. **demo 参数是免配置 MCP 的救命稻草**：dbhub 这类需要外部配置（DSN）的服务器，默认配置必须带可启动参数（如 `--demo`），否则对用户就是"配了但永远连不上"
