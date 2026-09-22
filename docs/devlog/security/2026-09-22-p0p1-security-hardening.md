---
docs_sync: required
---

# 2026-09-22 P0/P1 安全加固批（A1–A17）

## 背景

外部审计（mitta_audit_report.md）一次性列出 7 个 P0 + 13 个 P1，核心结论：
「MCP 子系统完全没有信任边界，未认证用户可在服务器上执行任意代码、读任意文件、接管任意账号」。
当日按 A1–A17 编号批量修复，覆盖路径校验、参数注入、认证限流、会话越权、JWT 生命周期、SSRF、XSS、容器非 root。

## 修复清单

### P0 高危

| 编号 | 问题 | 修复 | commit |
|---|---|---|---|
| P0-1 | MCP stdio `-c/-e` 绕过包名白名单 | 显式拒绝危险 flag，默认拒绝 | 4d308dd |
| P0-2 | SSRF 字符串匹配绕过 | 改 `ipaddress` + `getaddrinfo` 解析后判定，解析失败 fail-closed | acec2e3 |
| P0-3 | 文件路径 `startswith` 前缀绕过 | 改 `resolve()` + `relative_to()` + try/except | 5e4ecb7 |
| P0-4 | 找回密码仅凭 user_id 接管账号 | 改一次性验证码（Redis TTL + GETDEL 用后即焚）+ login/recover 独立限流（IP+userId 双维度、失败指数退避） | fa59a75 / cc908e6 / 9971260 |
| P0-5 | `/mcp` 端点零认证 | 挂 `McpAuthMiddleware`，user_id 服务端从 JWT 取，不接受客户端自填 | c947c16 / 9df8ec9 / 4ce44da |
| P0-6 | 会话 owner 为 None 即放行 | 改 fail-closed，抽 `verify_thread_access` 统一 6 处 | 1a15d84 |
| P0-7 | Refresh token 永不过期 | payload 加 `jti`+`iat`，续签作废旧 jti，继承绝对过期不滑动 | c409ecc |

### P1 真实风险

| 编号 | 问题 | 修复 | commit |
|---|---|---|---|
| P1-1 | `v-html` 渲染 LLM 输出未消毒，XSS 偷 localStorage JWT | 前端引入 DOMPurify，渲染前过滤 | aefaa86 |
| P1-2 | 限流键用 `verify_signature: False` 解析 JWT | 改验签后取 sub，验签失败退回 IP 键 | 86d3feb |
| P1-4 | ingest 脚本硬编码 Redis 密码 | 改读 `REDIS_DB_URL` 环境变量 | 952c7f2 |
| P1-8 | 密码强度无校验 | 8 位起 + 必须含字母数字 | 6f06303 |
| P1-9 | 文件上传只校验扩展名 | 去 html/svg，加 magic bytes 校验 | 6f06303 |
| P1-10 | compose 弱口令默认值 + 端口全绑 0.0.0.0 | 去掉弱口令默认值，中间件端口绑 127.0.0.1 | 6f06303 |
| P1-11 | 容器以 root 运行 | Dockerfile 加 `appuser` + chown 工作目录与 uv 工具目录 | 86d3feb |
| P1-13 | SPA 兜底路径穿越 | `resolve()` + `relative_to()` 规范化 | 6f06303 |
| — | SMTP 授权码硬编码 | 改读 env，默认留空 | 576b3b5 |
| — | minio/milvus 服务器不起导致 compose 失败 | MINIO 变量改回带默认值非强制 | ebc63e1 |

## 沉淀

- 新增 `docs/SECURITY_INPUT_CHECKLIST.md`：A1–A11 沉淀为「接受用户输入的函数编码前逐条过」的 checklist（de0d4ee）。
- 前端诚实标注为 Vue 3 CDN 运行时、无构建工具（85501ad）。

## 未修

- P1-5 `reranked_docs` 并发写：经核实无需 reducer（见 fixList.md B1 修正说明）。
- P1-7 用户自定义 system_prompt 无护栏（仍直接拼接），列入下一批。
