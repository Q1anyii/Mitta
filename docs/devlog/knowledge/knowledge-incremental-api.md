# Mitta 知识库增量更新 API 开发日志

> 涉及模块：知识库服务（`src/service/knowledge_service.py`）、路由（`src/routers/knowledge_router.py`）、主入口（`src/main.py`）、文件上传服务（`src/service/file_upload_service.py` 复用校验）
> 关联提交：`806eba2`（feat: 用户存储 MySQL 迁移 PostgreSQL + 知识库增量更新 API + README 更新）

---

## 一、问题背景与动机

外部评审指出项目短板：**知识库构建依赖手动运行 `ingest_knowledge.py` 脚本**，没有自动化的知识库更新机制。对于需要频繁更新知识的场景（如在线学习平台 FAQ），每次更新都要登录服务器跑脚本，运维成本高、时效性差。

**决策**：提供 **API / Web 界面的知识库增量更新能力**——通过 HTTP 接口即可增量添加文档、查看文档列表、删除文档，无需登录服务器。

---

## 二、排查过程

### 第 1 步：摸清现有入库链路（复用而非重写）

现有 `resources/knowledge-base/ingest_knowledge.py` 的链路：

```
文件 → EmbeddingProcessor.split_docs() 切分
     → Chroma 向量入库（vector_store.upsert）
     → RedisSearch BM25 入库（hset kb:doc:{doc_id}）
```

doc_id 算法：`{base_id}_{idx:03d}_{md5(text)[:8]}`（base_id 取文件名 stem）。

**关键发现**：BM25 索引是 RedisSearch 对 `kb:doc:*` 前缀 HASH 的**自动覆盖**（无需重建索引）——意味着只要新增写入 `kb:doc:` 哈希，BM25 检索自动生效。增量更新的成本比预想低：只需要"解析切分 → 双通道写"这一段，完全复用现有 EmbeddingProcessor 即可。

### 第 2 步：确认可复用组件

| 组件 | 来源 | 用途 |
|------|------|------|
| `EmbeddingProcessor` | `src/vector/embedding.py` | 文档解析 + 切分（支持 md/txt/pdf） |
| `create_vector_store` | `src/vector/vector_store.py` | Chroma 向量库实例（与 RAG 检索共用同一 collection） |
| `DOC_PREFIX` | `src/constant/cache_constant.py` | `kb:doc:` 前缀 |
| `FileUploadService.validate_file` | 文件上传服务 | 格式/大小/非空校验复用 |

### 第 3 步：踩坑——类方法 vs 模块函数的 import

最初误写成 `from service.file_upload_service import validate_file`，实际它是 `FileUploadService` 的 **staticmethod**，应写：

```python
from service.file_upload_service import FileUploadService
valid, error = FileUploadService.validate_file(file_name, len(content))
```

（`ImportError` 暴露，修正后通过。）

---

## 三、解决方案

### 1. 新增 `src/service/knowledge_service.py`

单例服务（lazy 初始化），四个核心方法：

| 方法 | 作用 | 实现要点 |
|------|------|---------|
| `ingest_bytes(file_name, content)` | 字节内容直接入库 | 写临时文件 → `ingest_file` → 清理临时文件 |
| `ingest_file(file_path, source)` | 本地文件双通道入库 | `split_docs` → 生成 doc_id → `vector_store.upsert` + `_write_redis` |
| `list_documents()` | 按 source 聚合列出文档 | 从 Chroma collection `get(include=["metadatas"])` 聚合 chunks 数 |
| `delete_source(source)` | 删除某来源全部 chunk | Chroma `where={"source": source}` 取 ids → delete + 同步删 Redis |
| `delete_document(doc_id)` | 删除单个 chunk | Chroma delete + Redis delete（先确认存在再删） |

**幂等设计**：doc_id 基于内容哈希，同内容重复上传自动覆盖，不产生重复文档。

### 2. 新增 `src/routers/knowledge_router.py`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/knowledge/upload` | 上传文档入库（.md/.txt/.pdf，≤10MB，复用 `validate_file` 校验） |
| GET | `/api/knowledge/documents` | 文档列表（按来源聚合，含总 chunks 数 + collection 总数） |
| DELETE | `/api/knowledge/source/{source}` | 按来源删除全部 chunk |
| DELETE | `/api/knowledge/documents/{doc_id}` | 按 doc_id 删除单个 chunk |

全部 JWT 认证（`Depends(get_current_user)`），与聊天接口一致。

### 3. 注册路由（`src/main.py`）

```python
from routers.knowledge_router import router as knowledge_router
app.include_router(knowledge_router)   # system_router 之前注册
```

### 4. README 同步

- 功能特性新增"知识库增量更新 API"
- 知识库入库章节拆成「方式一：脚本全量入库」+「方式二：HTTP 接口增量入库」
- API 接口一览新增「知识库」区块 + curl 示例

---

## 四、验证结果（真机集成）

用真实服务路径验证（含 Chroma 向量库 + Redis 双通道）：

```
=== knowledge_service ===
VECTOR 服务器配置加载完成，chromaFAQ_KNOWLEDGE_BASE
ingest -> {'ok': True, 'written': 2, 'source': 'test-kb-api.md', 'category': 'knowledge_base'}
redis keys -> 2 条
  kb:doc:test-kb-api_000_ce027c0a
  kb:doc:test-kb-api_001_a371e62d
delete_source -> 2 条
删除后 redis keys -> 0 条
```

路由注册验证：

```
POST /api/knowledge/upload
GET /api/knowledge/documents
DELETE /api/knowledge/source/{source}
DELETE /api/knowledge/documents/{doc_id}
knowledge_router OK
```

**验证点覆盖**：上传入库（2 chunks 双通道落库）、文档列表、按来源删除（Chroma + Redis 同步清理）、路由注册正确。

---

## 五、遗留与建议

| 项 | 状态 | 建议 |
|----|------|------|
| Web 前端管理界面 | 未做 | 当前是纯 API；后续可在设置页加"知识库管理"tab（上传/列表/删除） |
| 删除接口权限 | 所有登录用户可删 | 单用户部署无碍；多用户场景可加 admin 角色校验 |
| ingest 并发 | 未加锁 | 并发上传同一 source 时以 doc_id 幂等兜底，暂无需锁 |

---

## 六、经验沉淀

1. **能复用就不重写**：新功能先读现有链路（ingest_knowledge.py），增量 API 只是把脚本逻辑"服务化"，复用 EmbeddingProcessor + vector_store + DOC_PREFIX，改动面最小
2. **BM25 自动覆盖是增量更新的天然优势**：`kb:doc:` 前缀哈希新增即生效，无需重建索引——这决定了增量 API 的实现成本很低
3. **staticmethod 的 import 写法**：`ClassName.method` 而不是 `from module import method`，ImportError 是最快的提醒
4. **幂等靠内容哈希**：doc_id 含 md5(text)[:8]，重复上传天然覆盖，接口对"重复提交"免疫
5. **双通道删除必须同步**：Chroma 删了 Redis 没删，BM25 还会召回已删文档——delete_source/delete_document 都是两处一起清
