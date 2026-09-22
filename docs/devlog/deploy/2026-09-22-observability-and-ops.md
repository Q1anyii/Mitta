---
docs_sync: none（已同步 2026-09-22）
---

# 可观测性与运维补强（request_id / 日志轮转 / 回滚 / 备份）

## 背景

审计 P1-12：可观测性为零——`trace_id`/`request_id` 全仓 0 命中，无 Prometheus/OTel，loguru 无轮转配置，`./logs` 卷长跑必撑爆磁盘。
同时线上出过事故后没有一键回滚手段，PG 也没有每日备份。

## 改动

### 1. request_id 请求链路追踪（babc2e5）

- 新增 `src/middleware/request_context.py`：用 contextvar 注入 request_id，中间件每个请求生成 UUID；
- 日志 formatter 自动带上 request_id，响应头 `X-Request-Id` 回传给前端；
- 跨请求串联终于可行：出问题拿日志里的 request_id 就能拉出整条链路。

### 2. loguru 日志轮转（babc2e5）

- 10 MB 单文件轮转，保留 7 天，压缩为 zip；
- 解决 `./logs` 卷无界增长问题。

### 3. 一键回滚脚本（ce73283）

- `deploy/rollback.sh`：传 short_sha 即切到对应镜像重启，不用手敲 docker compose；
- 配合 CI 蓝绿部署，出问题 1 分钟内回滚。

### 4. PG 每日备份（ce73283）

- `deploy/backup_pg.sh`：每日 `pg_dump`，保留 7 天；
- 加磁盘水位提示（备份目录占用超阈值时输出警告）。

## 遗留

- 仍无 Prometheus/OTel 指标上报（P2 级，后续再接）；
- 业务 Metrics 埋点（检索延迟分布、缓存命中率）还没进监控面板。
