---
docs_sync: required
---

# CI 门禁与工程清理（workflow_call / 死代码 / LSH 种子）

## 背景

审计指出：评估脚本不进 CI 等于没有回归保护；项目里还有死代码、LSH 未设种子、注释与实现不符等 C 类体力活。

## 改动

### 1. agent-regression 改为部署门禁（bb7068b）

原来的 `agent-regression.yml` 是旁路跑（跑完不影响主流程）。改成：
- 主 workflow 通过 `workflow_call` 调用回归；
- `needs` 串联，回归失败即阻断部署；
- 后面补了两次 ref 修正（64a0b14 / c07f39e）：workflow_call 引用必须带 `@main` 版本 ref，本地触发不带 ref 会失败。

### 2. C 类体力活批量清理（37ae877）

- 删死代码：`classify_node()` 主体、`persona_router_node.py`、`rand_id_util.py`（已脱离主链路）；
- 清 MySQL 注释残留（已迁 PG）；
- `lsh_util.py` 加 `np.random.default_rng(42)`，语义缓存桶划分不再随进程重启漂移；
- 检索 magic number（`[:3]`、`n_results=20`）提常量；
- 删 `deploy/nginx/default.conf`（与实跑 `resources/frontend/nginx.conf` 不同源的死文件）。

### 3. 误提交工作文件清理（0e8e330）

- 审计/待办工作文件（fixList.md、mitta_audit_report.md）从仓库移除并加入 .gitignore；
- 它们是本地工作文档，不该进公共仓库。

## 效果

- CI 现在真正具备回归门禁能力，不是「跑过就算」；
- 死代码清空后 grep 噪音显著降低；
- LSH seed 加上后，语义缓存命中率指标可以稳定复现（不再每次重启漂移）。
