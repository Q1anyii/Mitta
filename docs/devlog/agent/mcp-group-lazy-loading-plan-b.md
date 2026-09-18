# Devlog: 方案B落地——MCP 分组 + 分级启动（第一方常驻 / 第三方懒加载）

> 日期：2026-09-18
> 类别：架构 / MCP
> 动机：服务器仅 1.6G 内存，context7/dbhub 等第三方 MCP 常驻浪费进程内存

## 目标

- 配置分组：第一方核心（filesystem/sqlite/sequential-thinking/memory/time/mitta-tools）常驻；
  第三方扩展（context7/dbhub）懒加载——进程不常驻，命中工具时才按需连接。
- 常驻进程从 8 台降到 6 台（第三方进程占用可释放，预估节省 150-300MB）。

## 改动清单

| 文件 | 改动 |
|------|------|
| `resources/config/mcp_servers.json` | 每台加 `group`（first_party/third_party）+ `lazy`（bool）；第三方 2 台 lazy=true |
| `src/config.py` | `load_mcp_server_configs(user_id, groups=None)` 支持分组过滤；无 group 的历史配置默认 first_party；新增 `_filter_by_groups` |
| `src/mcp_client/client.py` | `init_mcp_holders` 加 `groups` 过滤；新增 `McpLazyLoader`（闪连预热 schema → 命中触发真实连接 → 安全过滤 → 注入工具池） |
| `src/graphs/tool_filter.py` | `select_tools` 收集 `last_pending_hits`：语义命中但未加载的第三方工具名（懒加载触发信号） |
| `src/graphs/nodes/llm_node.py` | 签名加 `lazy_loader`；select_tools 后检测 pending → `trigger()` 拉起连接 → 重试筛选（本轮即可用） |
| `src/graphs/main_graph.py` | 新增 `DynamicToolNode`（每次执行前按 provider 刷新工具表，路由懒加载新增工具）；`tools` 改为引用调用方列表（不再 list() 拷贝，保证池可变）；`build_main_graph` 加 `lazy_loader` 参数 |
| `src/service/chat_service.py` | `open()` 加 `lazy_loader` 参数并透传全局图 |
| `src/main.py` | 启动只连 first_party；`McpLazyLoader.warmup()` 闪连第三方拿 schema；工具索引 = 第一方真实工具 + 第三方 schema 占位（`_make_pending_tools`）；关闭时 `lazy_loader.aclose()` |

## 设计要点

1. **闪连预热**：启动期临时连接第三方 server 拿工具 schema（name/description/tags），
   随即关闭（不保持进程）；schema 构造「不可执行占位工具」喂给工具向量索引，
   使 ToolFilter 语义层能召回第三方工具名。
2. **命中触发**：`query_available_tools` 命中但不在执行池的工具 → 写入
   `tool_filter.last_pending_hits` → `llm_node` 调 `lazy_loader.trigger(server_name)`
   （同步等待，tool_loop 上连接）→ 安全过滤后 extend 进可变工具池 →
   本轮重试 select_tools 即可用。
3. **动态路由**：静态 `ToolNode` 构造时拷贝工具表，无法执行懒加载追加的工具；
   `DynamicToolNode` 每次执行前按 provider 刷新 `tools_by_name`。
4. **失败语义**：预热/连接失败的 server 标记 `_failed`，不再重试、不阻塞主链路，
   `is_pending_tool` 返回 False（不会无限触发）；LLM 侧对应工具降级为不可用并如实告知。

## 过程中修复的 Bug

- **config.py 首次加载不过滤**：缓存未命中路径 `return validated` 未经过 groups 过滤，
  导致首次 `load_mcp_server_configs(groups=['third_party'])` 返回全量 8 台。
  已修复为 `return _filter_by_groups(validated, groups)`。

## 验证

- [x] 分组过滤：first_party=6 台（filesystem/sqlite/sequential-thinking/memory/time/mitta-tools）、
      third_party=2 台（context7/dbhub）、全量=8 台
- [x] `McpLazyLoader.warmup()` 容错：第三方连接失败 → 标记 `_failed`，不抛异常、不阻塞启动
- [x] `trigger()` 对 failed server 返回 False；`is_pending_tool` 不再触发
- [x] 语法/import 全链路：main.py、main_graph（DynamicToolNode、lazy_loader 参数）、
      llm_node（lazy_loader 参数）、chat_service 均通过
- [ ] 线上实测：完整启动只连 6 台；context7/dbhub query 触发懒加载（需部署到服务器验证）

## 遗留 / 说明

- 本地上 context7/dbhub 均无法连接（npx 环境/网络），懒加载触发链路
  （warmup 成功 → 语义命中 → trigger）需在线上容器（已有 uv/npx 缓存）实测。
- 用户自定义 MCP 配置路径（DB 加载）保持全连现状（无 lazy 字段默认 first_party，
  但用户图构建仍全量连接——本期只对全局配置生效懒加载）。
