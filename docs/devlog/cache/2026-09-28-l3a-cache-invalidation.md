---
docs_sync: none（纯缓存失效机制，无对外可展示改动；BACKLOG #9b 已标记完成）
---

# 2026-09-28 L3a 检索缓存失效边界修复（BACKLOG #9b）

## 背景

BACKLOG #9b 的原始描述：`clear_thread_cache` 按 thread 清理会误删 L3a 跨会话共享条目。深入排查后发现更深的问题——**L3a 缓存没有与知识库内容绑定**：

- L3a key 原来 = `rcache:x:{KB_VERSION}:{hash(问题)}`，`KB_VERSION` 是静态环境变量（默认 `v1`）。
- 知识库增量增删改（删除文档 / 覆盖入库）后，collection 不变、KB_VERSION 不变，**旧缓存继续命中已删/已改的 chunk**。
- 蓝绿入库切换 collection 时，旧缓存同样不会被清——因为 key 里根本没有 collection。

**结论先行**：不能靠监听、不能靠手动 bump 环境变量。蓝绿切换和增量增删改本身就是事件信号，让它们各自驱动失效，TTL 做最后兜底。

## 修复方案（三层闭环，不引入监听）

1. **collection 进 key（版本隔离，管蓝绿）**：`retrieve_exact_key()` 的 key 改为
   `rcache:x:{collection}:{hash(问题)}`，collection 取自 `load_vector_db_config()["collection"]`
   （蓝绿入库每次换 `FAQ_KNOWLEDGE_BASE_<sha>`，key 随之变 → 旧缓存自动读不到）。
   collection 缺失时回退 `KB_VERSION`（测试/配置缺失场景），解析结果惰性缓存一次，不重复读盘。
2. **操作点 flush（事件失效，管增量）**：新增 `CacheService.flush_exact_cache()`，
   SCAN `rcache:x:*` 批量删除；在 `KnowledgeService` 三个"知识库内容实际变化"的操作点调用：
   - `ingest_file` 清旧 chunk 成功（覆盖入库 = 更新了既有文档）
   - `delete_source` 删除成功后
   - `delete_document` 删除成功后
3. **TTL 兜底**：原有 `ex=self.cache_ttl`（900s，命中续期）不变，兜住漏网场景。

不引入监听的理由：知识库变化只发生在上述三个操作点 + CI 蓝绿重建，监听属于"被动轮询"，操作点失效是"事件驱动"，后者零延迟且无额外复杂度。

## 改动文件

- `src/service/cache_service.py`：`__init__` 加 `_kb_namespace_cache` 字段；新增 `_resolve_kb_namespace()` / `flush_exact_cache()`；`retrieve_exact_key()` 改带 collection。
- `src/service/knowledge_service.py`：`ingest_file` / `delete_source` / `delete_document` 三个操作点插入 `cache_service.flush_exact_cache()`。

## 验证

- `ast.parse` 两个改动文件：通过。
- 轻量逻辑验证（`CacheService.__new__` 绕过模型加载）：本机无 `vector_db.json` 时 `_resolve_kb_namespace()` 正确回退 `v1`，key 格式 `rcache:x:v1:{hash}` 正确；有 collection 时走 `collection` 分支（代码路径 `coll or KB_VERSION`）。
- 调用链确认：`knowledge_router.py` 的 ingest/delete 接口全部走 `knowledge_service` 的三个方法，无遗漏入口；CI 全量入库（蓝绿）不走此 service，靠 collection 进 key 自动隔离。

## 遗留项

- **L3b 同类问题**：L3b key = `retrieve_cache:{thread_id}:{bucket_id}`，同样不含 collection。知识库增删改后，L3b 语义缓存（KNN + rerank 验证）也可能命中已删文档。本次按用户确认的范围只处理 L3a；L3b 若要做，可在 `flush_exact_cache` 同级加全量 flush 或给 set_key 补 namespace（会稀释按 thread 的命中面，需单独评估）。
- 旧格式 `rcache:x:v1:{hash}` 的历史 key 不再被读写，由 TTL 自然回收，无需迁移。
