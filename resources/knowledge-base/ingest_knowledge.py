#!/usr/bin/env python3
"""
编程知识库向量库入库脚本

功能：遍历 knowledge-base 目录下的所有 .md 文件，调用项目的 EmbeddingProcessor 入库。
使用：cd src && python ../resources/knowledge-base/ingest_knowledge.py

注意：
- 需要先配置好 .env 中的 SILICONFLOW_API_KEY 和 SILICONFLOW_BASE_URL
- 入库会调用 embedding API，产生一定费用
- 重复入库会更新已有文档（基于内容哈希去重）
"""
import hashlib
import os
import sys
from pathlib import Path

from config import load_vector_db_config
from service.cache_service import cache_service
from constant.cache_constant import DOC_PREFIX
from vector.embedding import meta_to_dict
from vector.vector_store import create_vector_store
import redis as _redis

# Redis 连接：从环境变量读 REDIS_DB_URL，不硬编码；--redis-url 可覆盖
cache_service.db_url = os.getenv("REDIS_DB_URL")
cache_service.host, cache_service.port, cache_service.password = cache_service.parse_url(cache_service.db_url)
cache_service.redis = _redis.Redis(
    host=cache_service.host, port=cache_service.port,
    password=cache_service.password,
    socket_timeout=5, socket_connect_timeout=5,
)

# 确保 src 目录在 Python 路径中
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from loguru import logger

# 知识库目录
KB_DIR = SCRIPT_DIR

# 文件分类映射：文件名前缀 -> category
CATEGORY_MAP = {
    "01": "python",
    "02": "fastapi",
    "03": "langgraph",
    "04": "rag",
    "05": "database",
    "06": "architecture",
    "07": "exception",
    "08": "security",
    "09": "frontend",
    "10": "engineering",
    "README": "knowledge_base_index",
}

# agent_test-qa 子目录下的文件分类
QA_CATEGORY_MAP = {
    "01": "test_qa_basic",
    "02": "test_qa_debugging",
    "03": "test_qa_architecture",
    "04": "test_qa_badcase",
}


def get_category(file_path: Path) -> tuple[str, str]:
    """根据文件路径确定 (category, base_id)。"""
    # agent_test-qa 子目录
    if "agent_test-qa" in file_path.parts:
        prefix = file_path.stem.split("-")[0]
        return QA_CATEGORY_MAP.get(prefix, "test_qa"), prefix

    # 根目录文件
    prefix = file_path.stem.split("-")[0]
    return CATEGORY_MAP.get(prefix, "knowledge_base"), prefix

def _make_doc_id(base_id: str, chunk_index: int, text: str = "") -> str:
    """生成 chunk 级唯一 doc_id（不含内容 hash，改文件后同 idx upsert 覆盖）。"""
    return f"{base_id}_{chunk_index:03d}"


def _file_hash(path: Path) -> str:
    """文件内容 hash，用于判断文件是否改过。"""
    return hashlib.md5(path.read_bytes()).hexdigest()[:12]

def index_document(doc_id: str, content: str, source: str = ""):

    """写入一条文档到 RedisSearch，doc_id 与 Milvus 中的主键一致"""
    key = f"{DOC_PREFIX}{doc_id}"
    cache_service.redis.hset(key, mapping={
        "content": content,   # 文档正文（BM25 索引字段）
        "source": source,     # 来源文件名（TAG 过滤字段）
    })


def _clear_old_chunks(vector_store, base_id: str) -> None:
    """删除指定 base_id 前缀的所有旧 chunk（Chroma + Redis）。"""
    try:
        data = vector_store.collection.get(include=["metadatas"])
        old_ids = [_id for _id in data.get("ids", []) if _id.startswith(f"{base_id}_")]
        if old_ids:
            vector_store.collection.delete(ids=old_ids)
            pipe = cache_service.redis.pipeline()
            for doc_id in old_ids:
                pipe.delete(f"{DOC_PREFIX}{doc_id}")
            pipe.execute()
            logger.info(f"  清旧 chunk: {base_id} 删除 {len(old_ids)} 条")
    except Exception as e:
        logger.warning(f"清旧 chunk 失败（base_id={base_id}）: {e}")


def ingest_file(processor, file_path: Path, vector_store) -> int:
    """
    入库单个文件，返回写入的文档条数。

    增量逻辑：
    - 文件 hash 存 Redis（kb:filehash:{base_id}），hash 未变则跳过（省 embedding 调用）
    - 改了的文件先 _clear_old_chunks 清旧 chunk，再 upsert 新 chunk
    - doc_id = {base_id}_{idx:03d}（不含内容 hash），同 idx 直接覆盖
    """
    from vector.embedding import Meta

    category, base_id = get_category(file_path)
    source = "knowledge_base"
    meta = Meta(source=source, category=category)

    # ---- Step 0：文件 hash 判断，未改则跳过 ----
    fhash = _file_hash(file_path)
    hash_key = f"kb:filehash:{base_id}"
    try:
        old_hash = cache_service.redis.get(hash_key)
        if old_hash and old_hash.decode() == fhash:
            logger.info(f"  跳过（未改动）: {file_path.name}")
            return 0
    except Exception:
        pass  # Redis 不可用时全量重建

    # ---- Step 1：切分文件 ----
    chunks = processor.split_docs(str(file_path))

    if not chunks:
        return 0

    # ---- Step 2：清旧 chunk（改了的文件）----
    _clear_old_chunks(vector_store, base_id)

    # ---- Step 3：构建 ids/documents/metadatas ----
    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []

    for idx, chunk in enumerate(chunks):
        content = chunk.page_content.strip()
        if not content:
            continue
        doc_id = _make_doc_id(base_id, idx)
        ids.append(doc_id)
        documents.append(content)
        metadatas.append(meta_to_dict(meta))

    if not ids:
        return 0

    # ---- Step 4：upsert 向量 ----
    vector_store.upsert(ids, documents, metadatas)

    # ---- Step 5：写 Redis BM25 ----
    try:
        cache_service.create_sparse_index()
        pipe = cache_service.redis.pipeline()
        for doc_id, content in zip(ids, documents):
            pipe.hset(f"{DOC_PREFIX}{doc_id}", mapping={"content": content, "source": source})
        pipe.execute()
    except Exception as e:
        logger.warning(f"RedisSearch 写入失败: {e}")

    # ---- Step 6：记录文件 hash ----
    try:
        cache_service.redis.set(hash_key, fhash)
    except Exception:
        pass

    return len(ids)


def main():
    """主函数：遍历所有 .md 文件并入库。"""
    import argparse

    parser = argparse.ArgumentParser(description="知识库向量入库（支持蓝绿 collection 切换）")
    parser.add_argument("--collection", default=None,
                        help="覆盖 vector_db.json 的 collection 名（如 FAQ_KNOWLEDGE_BASE_<short_sha>）")
    parser.add_argument("--redis-url", default=None,
                        help="覆盖 Redis(RedisSearch) 连接 URL，如 redis://redis:6379/0；"
                             "不传时用顶部默认值（本地 WSL 配置）")
    args = parser.parse_args()

    if args.redis_url:
        # 服务器容器内跑入库：Redis 服务名/密码与本地不同，用参数覆盖
        cache_service.db_url = args.redis_url
        cache_service.host, cache_service.port, cache_service.password = cache_service.parse_url(cache_service.db_url)
        cache_service.redis = _redis.Redis(
            host=cache_service.host, port=cache_service.port,
            password=cache_service.password,
            socket_timeout=5, socket_connect_timeout=5,
        )

    logger.info("=" * 60)
    logger.info("编程知识库向量库入库开始")
    logger.info(f"知识库目录: {KB_DIR}")
    logger.info("=" * 60)

    # 初始化 EmbeddingProcessor
    try:
        from vector.embedding import EmbeddingProcessor
        processor = EmbeddingProcessor()
        logger.info(f"向量库初始化成功")
    except Exception as e:
        logger.error(f"向量库初始化失败: {e}")
        logger.error("请检查 vector_db.json 配置和网络连接")
        sys.exit(1)

    # 收集所有 .md 文件
    md_files = sorted(KB_DIR.rglob("*.md"))
    # 排除本脚本和 README（可选，README 也可以入库）
    md_files = [f for f in md_files if f.name != "ingest_knowledge.py"]

    logger.info(f"找到 {len(md_files)} 个 Markdown 文件")
    logger.info("-" * 60)

    # 逐个入库
    total_docs = 0
    success_count = 0
    fail_count = 0

    from vector import vector_store
    cfg = load_vector_db_config()
    if args.collection:
        cfg["collection"] = args.collection
    vector_store = create_vector_store(cfg)

    for i, file_path in enumerate(md_files, 1):
        logger.info(f"[{i}/{len(md_files)}] 处理: {file_path.name}")

        count = ingest_file(processor, file_path , vector_store=vector_store)
        if count > 0:
            total_docs += count
            success_count += 1
        else:
            fail_count += 1

    # 汇总
    logger.info("=" * 60)
    logger.info("入库完成")
    logger.info(f"  成功文件: {success_count}/{len(md_files)}")
    logger.info(f"  失败文件: {fail_count}")
    logger.info(f"  总文档数: {total_docs}")
    logger.info(f"  集合总数: {processor.count()}")
    logger.info("=" * 60)

    if fail_count > 0:
        logger.warning(f"有 {fail_count} 个文件入库失败，请检查日志")
        sys.exit(1)


if __name__ == "__main__":
    main()
