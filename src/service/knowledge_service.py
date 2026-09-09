# ============================================================
# 知识库增量更新服务
# 作用：为知识库提供 API/Web 界面的增量更新能力（上传入库、列表、删除）
# 复用现有链路：EmbeddingProcessor 解析切分 → Chroma 向量入库 + RedisSearch BM25 入库
# 说明：与 ingest_knowledge.py 一致的双通道写入（向量 + BM25），doc_id 对齐
# ============================================================

import hashlib
import tempfile
from pathlib import Path
from typing import Optional

from loguru import logger

from config import load_vector_db_config
from service.cache_service import cache_service
from constant.cache_constant import DOC_PREFIX
from vector.embedding import Meta, EmbeddingProcessor, meta_to_dict
from vector.vector_store import create_vector_store


class KnowledgeService:
    """知识库增量更新服务。

    单例在 chat_service 初始化后使用（复用同一 vector_store 与 Redis 连接）。
    每个方法幂等：同内容重复入库基于内容哈希 doc_id 覆盖，不会产生重复文档。
    """

    def __init__(self):
        self._vector_store = None
        self._processor: Optional[EmbeddingProcessor] = None

    @property
    def vector_store(self):
        """惰性创建向量库实例（与 RAG 检索共用同一 Chroma collection）。"""
        if self._vector_store is None:
            self._vector_store = create_vector_store(load_vector_db_config())
        return self._vector_store

    @property
    def processor(self) -> EmbeddingProcessor:
        if self._processor is None:
            self._processor = EmbeddingProcessor()
        return self._processor

    # ---------- 入库 ----------

    def _make_doc_id(self, base_id: str, chunk_index: int, text: str) -> str:
        """生成 chunk 级唯一 doc_id（与 ingest_knowledge.py 的算法完全一致）。"""
        text_hash = hashlib.md5(text.encode("utf-8")).hexdigest()[:8]
        return f"{base_id}_{chunk_index:03d}_{text_hash}"

    def _write_redis(self, ids: list[str], documents: list[str], source: str):
        """批量写入 RedisSearch（BM25 索引自动覆盖 kb:doc:* 前缀的哈希）。"""
        pipe = cache_service.redis.pipeline()
        for doc_id, content in zip(ids, documents):
            pipe.hset(f"{DOC_PREFIX}{doc_id}", mapping={"content": content, "source": source})
        pipe.execute()

    def ingest_bytes(self, file_name: str, content: bytes, category: str = "knowledge_base") -> dict:
        """上传文件内容直接入库（无需落盘）。

        Args:
            file_name: 文件名（用于分类与 base_id）
            content: 文件字节内容（支持 .md/.txt/.pdf 等 EmbeddingProcessor 可解析格式）
            category: 文档分类（默认 knowledge_base）

        Returns:
            {"ok": True, "written": N, "file_name": ..., "category": ...}
        """
        suffix = Path(file_name).suffix.lower()
        if suffix not in (".md", ".txt", ".pdf"):
            raise ValueError(f"暂不支持该文件格式: {suffix}，支持 .md/.txt/.pdf")

        # 写入临时文件供 EmbeddingProcessor 解析（它按文件路径加载）
        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=suffix, prefix="kb_upload_", delete=False
        ) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)
        try:
            return self.ingest_file(tmp_path, category=category, source=file_name)
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def ingest_file(self, file_path: Path, category: str = "knowledge_base",
                    source: Optional[str] = None) -> dict:
        """解析本地文件并双通道入库（向量 + BM25）。

        Args:
            file_path: 本地文件路径
            category: 文档分类
            source: 来源标记（默认用文件名）

        Returns:
            {"ok": True, "written": N, "source": source, "category": category}
        """
        source = source or file_path.name
        chunks = self.processor.split_docs(str(file_path))
        if not chunks:
            return {"ok": False, "written": 0, "source": source, "reason": "文档解析为空或不支持的格式"}

        meta = Meta(source=source, category=category)
        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict] = []

        for idx, chunk in enumerate(chunks):
            content = chunk.page_content.strip()
            if not content:
                continue
            doc_id = self._make_doc_id(Path(source).stem, idx, content)
            ids.append(doc_id)
            documents.append(content)
            metadatas.append(meta_to_dict(meta))

        if not ids:
            return {"ok": False, "written": 0, "source": source, "reason": "切分后无有效内容"}

        # 双通道写入：Chroma 向量 + RedisSearch BM25
        self.vector_store.upsert(ids, documents, metadatas)
        self._write_redis(ids, documents, source)

        logger.success(f"知识库增量入库成功 source={source}, written={len(ids)}")
        return {"ok": True, "written": len(ids), "source": source, "category": category}

    # ---------- 查询 ----------

    def count(self) -> int:
        """当前知识库向量集合总文档数。"""
        try:
            return self.vector_store.count()
        except Exception as e:
            logger.error(f"获取知识库总数失败: {e}")
            return 0

    def list_documents(self) -> list[dict]:
        """按 source 聚合列出知识库文档（从 Chroma collection 读取元数据）。

        Returns:
            [{"source": 文件名, "category": 分类, "chunks": 块数}, ...]
        """
        try:
            collection = self.vector_store.collection
            data = collection.get(include=["metadatas"])
            agg: dict[str, dict] = {}
            for meta in (data.get("metadatas") or []):
                meta = dict(meta or {})
                src = meta.get("source", "unknown")
                item = agg.setdefault(src, {"source": src, "category": meta.get("category", "knowledge_base"), "chunks": 0})
                item["chunks"] += 1
            return sorted(agg.values(), key=lambda x: x["source"])
        except Exception as e:
            logger.error(f"列出知识库文档失败: {e}")
            return []

    # ---------- 删除 ----------

    def delete_source(self, source: str) -> int:
        """删除指定 source 的全部文档（Chroma + RedisSearch 双通道）。

        Args:
            source: 文档来源（文件名）

        Returns:
            删除的 chunk 数量
        """
        collection = self.vector_store.collection
        # 从 Chroma 按 source 元数据过滤获取全部 id
        data = collection.get(where={"source": source}, include=["metadatas"])
        ids = data.get("ids") or []
        if ids:
            collection.delete(ids=ids)
        # 同步删除 RedisSearch 哈希（BM25 索引自动失效）
        if ids:
            pipe = cache_service.redis.pipeline()
            for doc_id in ids:
                pipe.delete(f"{DOC_PREFIX}{doc_id}")
            pipe.execute()
        logger.success(f"知识库删除 source={source}, chunks={len(ids)}")
        return len(ids)

    def delete_document(self, doc_id: str) -> bool:
        """删除单个文档 chunk（按 doc_id）。

        Args:
            doc_id: 文档 chunk 的 doc_id

        Returns:
            是否删除成功
        """
        collection = self.vector_store.collection
        # Chroma delete 不存在 id 时静默成功，先确认存在
        got = collection.get(ids=[doc_id], include=[])
        exists = bool(got.get("ids"))
        if exists:
            collection.delete(ids=[doc_id])
        cache_service.redis.delete(f"{DOC_PREFIX}{doc_id}")
        return exists


# 模块级单例（lazy 初始化，首次调用时创建）
knowledge_service = KnowledgeService()
