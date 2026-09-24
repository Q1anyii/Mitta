# mitta-tools 线上未装配：cwd 指向不存在的用户目录，12 工具全缺

docs_sync: none（已同步 2026-09-19：项目详解 06 篇新增 §3.3b 完整事故复盘 + §3.7 服务器表 cwd=/app 修正 + §六 口述补案例；11 篇 CI ④ 后补"配置类改动部署路径"；索引 README 冲突节；项目描述 Bullet B3；根 README MCP 表 cwd + 配置排查提示。提交 fd16c48）

日期：2026-09-19
类别：deploy / mcp

## 现象（线上 www.mittaai.xyz）

用户发 GitHub 仓库链接要求「抓取这个仓库页面看看有什么」，帽子米塔回复
「翻遍工具箱——没有网页抓取工具，目前只有文件读写、SQLite、知识图谱记忆」。
但自研本地 MCP Server `mitta-tools` 本就提供 `web_search`、`fetch_url`
（提交 3ff7f79，共 12 工具），属于「工具已实现但线上未装配」。

## 根因

`resources/config/mcp_servers.json` 中 mitta-tools 配置：

```json
"command": "python",
"args": ["src/mcp_client/mcp_server/mitta_tools_server.py"],
"cwd": "/app/user_files/user_01/AgentProject"
```

- 生产镜像 `WORKDIR /app`、`COPY . .`，代码实际在 `/app/src/...`；
  `/app/user_files/user_01/AgentProject` 既不在构建上下文（本地根目录无
  user_files），也不在 CI rsync / compose 挂载范围，容器内是
  `client.py::open()` 启动前 `os.makedirs(exist_ok=True)` 建出的**空目录**。
- `mcp_client/client.py:197-201` 有启动预检：command ∈ {python,python3,py}
  且 args[0] 是脚本路径时，检查 `Path(cwd)/args[0]` 是否存在，不存在直接
  `raise ConnectionError("MCP 服务器脚本不存在：…")`，连接被上层捕获并
  warning 跳过。
- 于是 `/app/user_files/.../src/mcp_client/mcp_server/mitta_tools_server.py`
  不存在 → 预检失败 → 整个 mitta-tools server 跳过，12 个工具全部缺失。
- filesystem / sqlite / memory 等是 `npx`/`uvx` 外部包，args[0] 是包名不走
  脚本预检，且不依赖 cwd 下的代码，故在空 cwd 下仍能握手成功——这解释了
  截图里只有这三类工具、独缺 mitta-tools 的现象。截图中
  department/employee/salary 表来自 dbhub `--demo` 示例员工库（懒加载）。

## 修复

mcp_servers.json 中 mitta-tools 的 `cwd` 改为镜像代码根 `/app`，
args 相对路径不变（预检路径变为 `/app/src/mcp_client/mcp_server/
mitta_tools_server.py`，镜像内存在）。

不影响工具行为：server 内 `SRC_DIR`/`PROJECT_ROOT` 均按 `__file__` 推导
（容器内分别为 /app/src、/app），`_run_git` 显式传 `cwd=PROJECT_ROOT`，
`read_local_file` 的 ALLOWED_ROOT 也是 PROJECT_ROOT，与启动 cwd 无关。

本地评测兼容：`ragas_test/evaluate_tool_filter.py::_localize_mcp_configs`
按 `cwd.startswith("/app")`（Windows 加载后形态为 `E:\app`，脚本同时覆盖
`E:\\app` 前缀）把 cwd 改写为本地项目根，cwd="/app" 天然命中，评测脚本无需改动。

## 验证

- 临时脚本本地化配置后 `init_mcp_holders([mitta-tools])` 实连：
  **12 个工具全部加载**（web_search / fetch_url / git_status / git_log /
  git_add / git_commit / git_branch / git_checkout / git_diff /
  search_project_files / read_local_file / get_project_info），关键工具无缺失。
- 规则层召回：`client.py::TOOL_TAGS` 中 fetch_url 含
  「抓取/网页/url/链接/内容/页面/fetch/抓」，「抓取这个仓库页面」类 query
  规则层强命中，不依赖语义向量索引。
- 语义层补齐：`main.py:85-87` 启动时 `tools_embedding(index_tools)`
  自动把已连接第一方工具写入 Milvus MCP_TOOLS 集合，api 容器重启后
  mitta-tools 12 工具自动入索引，无需手动操作。
- `pytest tests/ -q`：**73 passed**。

## 部署与遗留

- 本次只改 `resources/config/mcp_servers.json`，由 CI rsync 同步到
  /opt/mitta/resources/config（compose 挂载进容器），**无需重建镜像**，
  push 触发 CI 同步并重启 api 后生效。
- 用户级 MCP 配置优先于文件（`config.py::load_mcp_server_configs`
  先查 PostgreSQL user_mcp_servers，无记录才降级文件）；截图中默认
  server 均在，证明 qianyi 账户无自定义记录，改文件即生效。
- 运行时网络限制（非本次缺陷）：服务器在国内，fetch_url 抓 github.com
  可能间歇超时，工具会返回「抓取失败: …」而非「工具不存在」；
  web_search 走 Bing，可作为替代信息源。
- 容器内 /app 不含 .git（.dockerignore 排除），git_* 工具在容器内会返回
  not a git repository，属预期（git 工具主要面向本地开发场景）。
