docs_sync: none（已同步 2026-09-28：DEPLOYMENT.md 工作流条目新增部署前磁盘清理+镜像自动回滚；11 篇 §3.4 表格⑦b/⑦c/⑨ 与演进要点；Wiki 部署页）
# CI 部署前清理服务器镜像腾空间，修复磁盘满导致部署失败

日期：2026-09-28
分类：ci（部署流程）

## 根因

CI 部署阶段（SSH 到阿里云 ECS）健康检查 24 连败，容器日志：

```
chromadb: AttributeError: 'RustBindingsAPI' object has no attribute 'bindings'
loguru: OSError: [Errno 28] No space left on device
```

**根因是服务器磁盘满**，chromadb 的 AttributeError 是写盘失败导致的连锁症状：
磁盘满 → 容器启动时 chromadb 初始化写盘（SQLite/WAL/向量数据）失败 → Rust 绑定未初始化 → stop 时 `del self.bindings` 抛 AttributeError → 应用启动失败。

为什么磁盘会满：每次部署 `docker compose pull api` 拉新 `latest` → 旧 latest 变悬空镜像；而 CI 末尾清理用的是 `docker image prune -f --filter "until=24h"`——频繁部署时所有镜像都在 24h 内，**过滤器一条都清不掉**，镜像持续堆积直到占满磁盘。

## 改动（.github/workflows/acr-cicd.yml）

1. 部署脚本 `[1.5/4]`：`docker compose pull` **之前**先清理腾空间（磁盘满时 pull 本身也会失败）：
   - `docker image prune -af`——全量清未被任何容器引用的镜像（运行中容器不受影响）；
   - `docker builder prune -af`——服务器不本地 build，构建缓存纯占空间一并清；
   - 均 `|| true` 防 set -e 中断。
2. 部署末尾兜底清理：`docker image prune -f` 去掉 `until=24h` 过滤器（pull 后旧 latest 变悬空，及时清掉）。
3. **镜像自动回滚**（[1.6/4] 备份 + [4/4] 失败分支）：
   - pull 前把当前 latest 打临时 `rollback_<时间戳>` tag（挂在运行中容器引用的镜像上，prune 不删）作为回滚源；
   - 健康检查 24 连败时：`docker tag <rollback> latest` → `docker compose up -d --no-build api` → 复查健康；
   - 无回滚源（首次部署/备份失败）时跳过；RAG collection 蓝绿回滚逻辑保留；
   - ACR 侧每个 SHORT_SHA tag 是回滚的远程源泉（勿开生命周期自动清理，否则失效）。

## 验证方式

下次部署观察：`[1.5/4]` 清理日志、`df -h` 磁盘剩余、健康检查 200 通过。

## 遗留

- 本次修复只对**下次部署之后**生效；服务器当前堆积的镜像需手动清理一次
  （`docker system prune -af && docker builder prune -af`）后才部署；
- 服务器为 1.5GB 内存轻量机，镜像+数据持续增长，长期建议磁盘扩容或定期清理；
- 应用层可优化项（非本次范围）：chromadb 初始化失败时报磁盘/初始化原因而非 AttributeError 连锁错误。
