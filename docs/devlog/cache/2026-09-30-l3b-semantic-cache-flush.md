docs_sync: none（已同步 2026-09-30：07-缓存体系.md 失效策略表/注记/3.8节/Q8；简历 Bullet 失效策略段；Wiki 缓存体系页）
# L3b 语义缓存知识库变更后全量失效（flush_semantic_cache + 蓝绿覆盖）

日期：2026-09-30
分类：cache（缓存失效）

## 背景 / 根因

H-20260919-10 缓存分层后，知识库增量增删改只显式失效 L3a 精确缓存（`flush_exact_cache`），
L3b 语义缓存没有失效入口：

- **增量增删改**：collection 不变，L3b 索引（`idx:retrieve_cache`）里的旧结果仍会被
  KNN 召回 → 命中已删/旧 chunk 文档；
- **蓝绿切换**：L3b 查询走 RediSearch 索引做向量召回，**不按 key 精确匹配**
  （key 是 `retrieve_cache:{thread_id}:{bucket_id}`，查询侧只按 `@thread_id` + KNN 过滤）——
  因此 L3a 那种"collection 进 key 自动隔离"的方案对 L3b 无效，必须删 hash
  （删 hash 后索引条目由 RediSearch hash 前缀索引自动同步移除，无需 DROPINDEX）。

## 改动

1. **`cache_service.py` 新增 `flush_semantic_cache()`**：SCAN 遍历 `retrieve_cache:*`
   （`KEY_PREFIX`，与 L3a 的 `rcache:x:*` 前缀完全隔离，互不误删），批量 delete，
   返回实际删除数；沿用 `flush_exact_cache()` 的降级模式（调用方 try 兜底）。
2. **`cache_service.open()` 启动挂载 flush**：蓝绿切换是「改 collection 配置 + 重启」
   生效，进程启动即清空一次 L3b（try/except 降级，失败不阻塞启动）。
   代价：每次部署后 L3b 语义命中率 15 分钟重建期（TTL 900s ± 抖动），
   L1/L2/L3a 仍命中，与知识库增量变更 flush 同款可接受代价。
3. **`knowledge_service.py` 三处调用点**（增量增删改）追加 `flush_semantic_cache()`：
   - `ingest_file` 清旧 chunk（更新既有文档）后；
   - `delete_source` 删除 source 后；
   - `delete_document` 删除单 chunk 后。

## 验证

`ast.parse` 通过；mock 冒烟：
- `flush_semantic_cache()`：SCAN match=`retrieve_cache:*`、跨游标遍历、批量 delete（解包）、
  返回删除数 ✅
- `open()` 启动调用 flush 一次 ✅
- knowledge_service 恰好 3 处调用点 ✅
- L3a/L3b 前缀隔离（`rcache:x:*` vs `retrieve_cache:*`）✅

## 遗留

- 运行时冒烟（真实 Redis 上知识库变更后 L3b 不再命中）待起服后补；
- 每次部署清空 L3b 属设计取舍；若将来部署频率极高，可改为
  「启动时对比 vector_db.json collection 与上次记录的 collection，仅变化时 flush」。
