---
docs_sync: none（已同步 2026-09-22：README BM25 节补 19da33f 修复说明、04 篇 §3.3 补入库脚本漏建索引根因与线上验证）
---

# 修复：BM25 稀疏路恒空（入库未建 RediSearch 索引）

## 背景

项目宣传「稠密向量 + BM25 稀疏双路互补」，但线上 BM25 检索始终返回空，
混合检索实际退化成稠密单路。定位发现：
esources/knowledge-base/ingest_knowledge.py
入库时只 HSET 写了文档内容，从未调用建索引逻辑，RediSearch 上根本没有
kb_bm25 索引，FT.SEARCH 自然恒空。

## 根因

- cache_service.create_sparse_index()（FT.CREATE ... ON HASH PREFIX 1 kb:doc:
  SCHEMA content TEXT, source TAG）早已存在且已推送；
- 但入库脚本 ingest_file() 的 Step4 只 pipe.hset(...)，漏了调用
  create_sparse_index()；
- 建索引与写 Hash 又整体包在 	ry/except 里静默降级，失败只 warning，
  线上无任何报错，问题长期被掩盖。

## 修复


esources/knowledge-base/ingest_knowledge.py Step4 在 hset 前补一行：

    cache_service.create_sparse_index()

该方法幂等（FT.INFO 存在即返回），重复入库安全。提交：19da33f。

## 验证（线上服务器）

- FT._LIST 含 kb_bm25；
- FT.SEARCH kb_bm25 "上下文 | 管理" NOCONTENT WITHSCORES 命中
  kb:doc:14_002_f1783822（14-agent-memory-state.md，相关 chunk），分数 2.25；
- 稀疏路由从恒空恢复为正常召回，双路互补真实成立。

## 备注

CI（cr-cicd.yml）检测到 
esources/knowledge-base/ 变更即 ingest_required=true
走蓝绿入库；本次补建索引随该流程在线上完成，未额外手动操作。