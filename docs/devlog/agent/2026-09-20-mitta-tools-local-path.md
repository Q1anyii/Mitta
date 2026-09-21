---
docs_sync: none
---

# 修复：本地 Windows 下 mitta-tools MCP 脚本路径解析失败

## 现象

本地开发启动后端，日志报：
`MCP 服务器 [mitta-tools] 连接失败，已跳过：MCP 服务器脚本不存在：E:\app\src\mcp_client\mcp_server\mitta_tools_server.py`

## 根因

`resources/config/mcp_servers.json` 里 mitta-tools：
- `args = ["src/mcp_client/mcp_server/mitta_tools_server.py"]`（相对路径）
- `cwd = "/app"`（容器内工作目录，服务器上真实存在）

`client.py` 预检：`script = Path(params.cwd) / params.args[0]`。
本地 Windows 下 `cwd="/app"` 是绝对路径，被解析成当前盘根 `E:\app`，
而项目实际在 `E:\工作文件\AgentProject`，故 `E:\app\src\...` 不存在。
该配置同时服务线上容器（`/app` 正确），不能直接改 cwd。

## 修复

`src/mcp_client/client.py` 预检段：脚本按 `cwd` 拼接不存在时，
**fallback 到进程 cwd / 项目根**（`Path(__file__).resolve().parents[2]`）定位脚本，
找到则把 `cwd` 换成 fallback 目录重建 `StdioServerParameters`；仍找不到才抛错。
容器环境下 `/app` 本身存在、脚本直接命中，不触发 fallback，线上行为不变。

## 验证

- `ast.parse client.py` 通过；
- 实测 `parents[2]` = `E:\工作文件\AgentProject`，fallback 后脚本存在。

## 遗留

- filesystem/sqlite/memory 等项的 `cwd`/args 仍含 `/app/user_files/...` 容器路径；
  本地开发若这些也报目录问题，同样需要类似 fallback（当前未触发，暂不处理）。
