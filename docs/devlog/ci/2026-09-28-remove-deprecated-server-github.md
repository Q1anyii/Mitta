docs_sync: none
# 移除废弃且未启用的 server-github npm 包

日期：2026-09-28
分类：ci（镜像构建）

## 背景

CI 构建 npm 阶段持续出现废弃警告：

```
npm warn deprecated @modelcontextprotocol/server-github@2025.4.8: Package no longer supported.
```

## 查证结论

1. `@modelcontextprotocol/server-github` 官方已停止维护（2025-04 起标记 deprecated）；
2. 项目默认配置（`resources/config/mcp_servers.json`）未启用 github MCP；
3. `mcp_config_service.py` 的 `SAFE_MCP_PACKAGES` 白名单保留该包名（仅作"允许用户配置"的候选，不影响安全校验）；
4. 服务器线上 `.env` 亦未启用 → 纯属构建/镜像浪费 + 警告噪音。

## 改动

`Dockerfile` npm install 列表移除 `@modelcontextprotocol/server-github`，同步更新依赖注释。

## 验证方式

1. 下次 CI 构建 npm 阶段无 server-github 废弃警告；
2. `npm ls -g` 列表不再含该包；
3. 镜像内 MCP 工具连接正常（filesystem/sequential-thinking/memory 不受影响）。

## 遗留

- `SAFE_MCP_PACKAGES` 白名单保留 `server-github`：若未来用户显式配置启用，npx 会临时拉取安装（构建期不再预装）；该包已废弃，建议届时迁移 GitHub 官方 `github-mcp-server`。
