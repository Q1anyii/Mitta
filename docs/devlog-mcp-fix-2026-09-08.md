# Mitta MCP Server 连接失败排查与修复开发日志

> 日期：2026-09-06 ~ 2026-09-08
> 涉及模块：MCP 客户端（`src/mcp_client/client.py`）、配置服务（`src/service/mcp_config_service.py`）、聊天服务（`src/service/chat_service.py`）、`Dockerfile`、`resources/config/mcp_servers.json`
> 关联提交：`7f23d28`、`020eb0c`、`9a991b2`

---

## 一、问题现象

服务器（阿里云 ECS，1核1.6G+2GB swap）部署的 Mitta 后端，AI 回答"没有可用的网页抓取工具"，MCP 工具始终加载为 0：

- 前端提问"抓取网页内容"，AI 回复无可用工具
- 后端日志稳定出现：

```
工具筛选：两路均未命中，本轮无可用工具 query='抓取网页内容' 规则命中=0 语义命中=0 最终=0 工具=[]
MCP 服务器 [filesystem] 连接失败，已跳过：MCP 服务器 [filesystem] 连接失败：
MCP 服务器 [git] 连接失败，已跳过：MCP 服务器 [git] 连接失败：
...
用户 [qianyi] MCP 连接成功但无工具（服务器数=0）
```

- 但**本地开发环境完全正常**，同一份配置 6 个 MCP server 全部可用。

---

## 二、排查过程

### 第 1 步：确认配置确实被读到

日志显示 `从数据库加载用户 [qianyi] 的 MCP 配置：6 个` —— 配置读取正常，问题在连接环节。

### 第 2 步：逐层验证 5 层链路

| 层级 | 验证方式 | 结论 |
|------|---------|------|
| ① 配置加载 | 日志 `从数据库加载...6 个` | ✅ 正常 |
| ② 子进程启动 | 手动向 server 发 JSON-RPC initialize | ⚠️ 见下 |
| ③ 工具获取 | `docker logs` 观察连接结果 | ❌ 全失败 |
| ④ 语义筛选 | 日志 `工具筛选` | ❌ 空工具导致 0 命中 |
| ⑤ 绑定 LLM | 工具列表为空则无工具可用 | ❌ 连带失败 |

### 第 3 步：手动握手测试（关键定位）

在容器内直接向 MCP server 发送 initialize 请求：

```bash
# uvx 类（fetch）—— 单独跑完全正常
echo '{"jsonrpc":"2.0","id":1,"method":"initialize",...}' | timeout 15 uvx mcp-server-fetch
# 返回：{"result":{"protocolVersion":"2024-11-05",...,"serverInfo":{"name":"mcp-fetch","version":"1.30.0"}}}

# npx 类（sequential-thinking）—— 启动即崩
echo '...' | timeout 25 npx -y @modelcontextprotocol/server-sequential-thinking
# 报错：Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'zod'
```

### 第 4 步：确认 npm 全局包缺失（实锤根因一）

```bash
npm ls -g --depth=0 | grep -i mcp   # 输出为空！
```

**结论**：Dockerfile 里 `npm install -g @modelcontextprotocol/server-*` 构建时**全部静默失败**，行尾 `|| echo "WARN..."` 把失败吞掉了。运行期 npx 每次临时下载包，20s 超时内下载不完整且缺 zod 依赖 → 启动即崩。

### 第 5 步：追查 Dockerfile 为何失败（实锤根因二）

```bash
npm install -g @modelcontextprotocol/server-fetch
# npm error 404: '@modelcontextprotocol/server-fetch@*' is not in this registry
```

**结论**：`@modelcontextprotocol/server-fetch` 和 `mcp-server-sqlite` 在 **npm registry 上根本不存在**（它们都是 Python 生态包，走 uvx 而非 npx）。npm install 遇到一个 404 会**整体失败** → 所有 MCP 包都没装上 → `|| echo WARN` 掩盖。

### 第 6 步：发现全局默认配置永远加载不到（根因三）

日志：

```
MCP 全局配置文件不存在（路径: /app/E:\工作文件\AgentProject\resources\config\mcp_servers.json）
```

**结论**：`.mcp_config_path` 文件被提交进了 git，里面存的是**本地 Windows 绝对路径**。容器里 `get_mcp_config_path()` 读到它后解析成 `/app/E:\工作文件\...` 这个不存在的路径 → 全局默认 MCP 配置永远为空，只能依赖数据库用户配置。

### 第 7 步：uvx 并发锁竞争（次要因素）

fetch 单独跑秒回，但 6 个 server 并发启动时 4 个是包管理启动（uvx/npx），存在 **uv 全局安装锁竞争**，20s 超时不够 → fetch/sqlite 超时失败。

---

## 三、解决方案

### 1. 服务器现场修复（立即生效，不等镜像重建）

```bash
# ① 清掉损坏的 npx 临时缓存
docker exec mitta-api bash -c 'rm -rf /root/.npm/_npx/*'

# ② 全局安装 npm 上真实存在的 MCP 包（走 npmmirror，不再临时下载）
docker exec mitta-api bash -c 'npm install -g @modelcontextprotocol/server-filesystem @modelcontextprotocol/server-sequential-thinking @modelcontextprotocol/server-memory @modelcontextprotocol/server-github'

# ③ 预热 uvx 的 Python 生态包（fetch/sqlite）
docker exec mitta-api bash -c 'timeout 180 uvx mcp-server-fetch --help; timeout 180 uvx mcp-server-sqlite --help'

# ④ 重启生效
docker restart mitta-api
```

### 2. 代码根治（`7f23d28` + `020eb0c`）

**Dockerfile**：
- npm 全局包列表移除不存在的 `@modelcontextprotocol/server-fetch`、`mcp-server-sqlite`，新增 `@modelcontextprotocol/server-time`
- 去掉 `|| echo "WARN..."` 掩盖，改为失败即中止 + `npm ls -g` 验证 + `echo NPM_MCP_PACKAGES_INSTALLED_OK`
- uvx 预热列表补全 `mcp-server-sqlite`（构建期下载进 uv 缓存，运行期零联网）
- 配置 `UV_DEFAULT_INDEX`/`UV_INDEX_URL` 为阿里云源（运行期 uvx 冷启动加速）

**chat_service.py**（零工具不缓存退避）：
- 配置了 MCP server 却拿到 0 工具时**绝不写缓存**——否则空工具图被永久命中，配置 hash 不变就永不重连（"配了 MCP 却读不到"的核心）
- 记录失败时间戳 `_mcp_build_fail_at`，60s 冷却期内不重复触发后台连接
- 成功拿到工具后清除失败标记
- 后台构建日志带工具数量

**mcp_config_service.py**：
- `SAFE_MCP_PACKAGES` 白名单新增 `@modelcontextprotocol/server-time`、`mcp-server-time`

**resources/config/mcp_servers.json**（服务器默认配置）：
- 删除 git server（容器内无 git 仓库且包崩溃，RAG 场景无用）
- sqlite 从 `npx` 改为 `uvx`（Python 包），db 路径改为容器内路径
- 新增 time 工具
- 全部路径改为容器内 Linux 路径

**清理路径残留**：
- `git rm --cached resources/config/.mcp_config_path resources/config/.vector_config_path`
- 加入 `.gitignore`（本地路径残留文件禁止提交，否则容器内解析成 `/app/E:\...`）

---

## 四、验证结果

修复后服务器日志：

```
工具向量索引构建完成：18 个工具 -> collection=MCP_TOOLS
用户 [qianyi] 后台加载 18 个 MCP 工具（3 个服务器）
用户 [qianyi] 专属图后台构建完成
工具筛选：query='抓取网页内容' 规则命中=1 最终=12 工具=['fetch', 'read_file', ...]
```

- ✅ filesystem / sequential-thinking / memory / fetch 全部可用（19 个工具 / 4 个服务器）
- ✅ "抓取网页内容" → fetch 工具被正确筛出并调用
- ✅ 前端不再提示"没有可用的网页抓取工具"

---

## 五、遗留与建议

| 项 | 状态 | 建议 |
|----|------|------|
| git MCP server | 移除 | RAG 问答用不到；若需要，cwd 必须指向真实 git 仓库 |
| sqlite db 路径 | 待确认 | 确保 `/app/user_files/user_01/AgentProject/resources/` 目录存在 |
| 数据库用户配置 | 待同步 | `user_mcp_servers` 表里旧配置（含 git/npx sqlite）需前端删除或 SQL 更新，用户配置优先级高于全局默认 |
| fetch 并发超时 | 已缓解 | uv 缓存预热后基本解决；若再出现可调大 `init_mcp_holders` 超时 |

---

## 六、经验沉淀

1. **`|| echo WARN` 是最危险的写法**——它会掩盖构建失败，让问题延迟到运行期才以更难懂的形式爆发
2. **npx vs uvx 生态要分清**：`@modelcontextprotocol/server-fetch` 是 npm 包名但 fetch 实际是 Python 包（`mcp-server-fetch`，uvx 跑）；装包前先验证包名存在
3. **本地路径残留禁止进 git**：`.mcp_config_path`/`.vector_config_path` 这类存绝对路径的状态文件，容器里读必然解析错误，必须 gitignore
4. **空结果缓存是隐性 bug**：任何"构建结果为空"的场景都不要无脑写缓存，要给失败退避 + 重试机制
5. **服务器排查要逐层验证**：配置读取 → 子进程启动 → 握手 → 工具获取 → 语义筛选，一层层实测定位，不要靠猜
