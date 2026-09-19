# RAG 入库 CI 蓝绿切换（双 collection + 保留一版回滚）（H-20260919-08）

docs_sync: none

日期：2026-09-19
类别：ci / rag-ingest

## 背景

改 chunk_size/切分器后需手动 ssh 服务器跑 ingest。目标：push main 时若改动 RAG 入库相关文件，CI 自动入库到新 collection → 切换指向 → 重启 → 健康检查通过再切 → 保留最新两个 collection 支持回滚；**绝不先删旧库**。

## 改动

1. **`resources/knowledge-base/ingest_knowledge.py`**：新增 `--collection <name>`（覆盖 vector_db.json 的 collection 名，蓝绿入库到新 collection）与 `--redis-url <url>`（覆盖 Redis 连接；服务器容器内连 `redis://redis:6379/0`，本地默认 `redis://:sorts_dev@localhost:6379` 不变）。
2. **`resources/knowledge-base/cleanup_collections.py`**（新增）：清理旧 collection。显式 `--keep` 列表（当前活跃 + 上一个可回滚）保留 2 个，删除其余 `FAQ_KNOWLEDGE_BASE_*`；其他 collection（MCP_TOOLS 等）不碰。比"按创建时间排序"更稳——chroma Collection 无可靠创建时间元数据，short_sha 名称不保证时间序。支持 `--path` 覆盖 persist_path（测试/手动清理用）。
3. **`.github/workflows/acr-cicd.yml`**：
   - 新增"Check if RAG ingest is required"步骤：`git diff HEAD~1 HEAD` 命中 `src/constant/embedding_constants.py` / `resources/knowledge-base/` / `resources/config/vector_db.json` → `ingest_required=true`，否则跳过全部入库步骤（不花 embedding API 费用）；
   - `Get commit hash` 改为无条件执行（ingest 需要 SHORT_SHA，即使 build_required=false）；
   - rsync config 加 `--exclude vector_db.json`（服务器上该文件由 CI 动态维护 collection 名）；
   - 新增 knowledge-base rsync（仅 ingest_required=true）——配合 docker-compose 挂载，.md/脚本变更无需重建镜像；
   - Deploy 内新增蓝绿段：入库到 `FAQ_KNOWLEDGE_BASE_<short_sha>`（失败即 CI 红，旧库不动）→ sed 切换 vector_db.json → 重启 api → 健康检查失败**回滚切回旧 collection 再重启** → 健康检查通过后 cleanup 保留 NEW+OLD。
4. **`docker-compose.yml`**：api 容器挂载 `./resources/knowledge-base:/app/resources/knowledge-base`（容器读最新 .md 与入库/清理脚本，与镜像解耦）。

## 验证

- chromadb 1.5.9 `list_collections()` / `delete_collection(name)` API 实测通过；
- `cleanup_collections.py` 在临时 chroma 目录实测：建 4 个 collection（含 MCP_TOOLS），`--keep` 2 个 → 只删旧的 FAQ_KNOWLEDGE_BASE，MCP_TOOLS 保留，断言通过；
- `ingest_knowledge.py --help` 输出正常（argparse 注册正确，未执行入库避免 embedding 费用）；
- `acr-cicd.yml` / `docker-compose.yml` YAML 解析通过；
- sed 替换与 OLD_COLL 读取在 vector_db.json 副本上模拟通过；
- **验证边界**：无法在本地跑服务器端 CI（无服务器 SSH/容器环境）；首次真实 push 需观察一次完整入库-切换-清理流程。

## 风险与遗留

- **换服务器/重置初始化**：仓库内 vector_db.json 是初始值（`FAQ_KNOWLEDGE_BASE`），服务器上被 CI 改过名；换服务器时需手动恢复初始 collection 或重新入库到初始名（手动操作，不自动化，见 handoff §4）；
- **BM25 旧 key 堆积**：RedisSearch hset 按 doc_id 前缀保留，不主动清（回滚需要）；数据量小不影响性能，后续可按 doc_id 前缀定期清；
- **首个 commit 无 HEAD~1**：`git diff HEAD~1 HEAD` 失败（`|| true`）→ 不触发入库，可接受（首次部署人工初始化即可）；
- **collection 命名**：`FAQ_KNOWLEDGE_BASE_<short_sha>`（下划线 + 十六进制）符合 chroma 命名规则（字母数字下划线）；
- CI 部署后 `docs_sync: required`：README 部署说明（CI 流程 / collection 管理 / 手动初始化步骤）需文档 Agent 同步。
