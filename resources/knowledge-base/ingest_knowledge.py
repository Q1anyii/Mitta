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
import sys
from pathlib import Path

from config import load_vector_db_config
from service.cache_service import cache_service
from constant.cache_constant import DOC_PREFIX
from vector.embedding import meta_to_dict
from vector.vector_store import create_vector_store

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

# ragas_test-qa 子目录下的文件分类
QA_CATEGORY_MAP = {
    "01": "test_qa_basic",
    "02": "test_qa_debugging",
    "03": "test_qa_architecture",
    "04": "test_qa_badcase",
}


def get_category(file_path: Path) -> tuple[str, str]:
    """根据文件路径确定 (category, base_id)。"""
    # ragas_test-qa 子目录
    if "ragas_test-qa" in file_path.parts:
        prefix = file_path.stem.split("-")[0]
        return QA_CATEGORY_MAP.get(prefix, "test_qa"), prefix

    # 根目录文件
    prefix = file_path.stem.split("-")[0]
    return CATEGORY_MAP.get(prefix, "knowledge_base"), prefix

def _make_doc_id(base_id: str, chunk_index: int, text: str) -> str:
    """生成 chunk 级唯一 doc_id，与 Milvus 主键一致。"""
    text_hash = hashlib.md5(text.encode("utf-8")).hexdigest()[:8]
    return f"{base_id}_{chunk_index:03d}_{text_hash}"

def index_document(doc_id: str, content: str, source: str = ""):

    """写入一条文档到 RedisSearch，doc_id 与 Milvus 中的主键一致"""
    key = f"{DOC_PREFIX}{doc_id}"
    cache_service.redis.hset(key, mapping={
        "content": content,   # 文档正文（BM25 索引字段）
        "source": source,     # 来源文件名（TAG 过滤字段）
    })


def ingest_file(processor, file_path: Path, vector_store) -> int:
    """
    入库单个文件，返回写入的文档条数。

    同时写入：
    - Milvus：向量检索（稠密）
    - RedisSearch：BM25 全文检索（稀疏）
    两者用相同的 doc_id 对齐，RRF 融合时靠 id 匹配。
    """
    from vector.embedding import Meta

    category, base_id = get_category(file_path)
    source = "knowledge_base"
    meta = Meta(source=source, category=category)

    # ---- Step 1：切分文件 ----
    chunks = processor.split_docs(str(file_path))  # 返回 List[Document]，每个有 .page_content

    if not chunks:
        return 0

    # ---- Step 2：遍历 chunks，构建三个列表（与 Milvus upsert 接口对齐）----
    ids: list[str] = []
    documents: list[str] = []   # ★ 这就是 content
    metadatas: list[dict] = []

    for idx, chunk in enumerate(chunks):
        # ★ content = chunk.page_content
        content = chunk.page_content.strip()
        if not content:
            continue

        # 生成与 Milvus 一致的 doc_id
        doc_id = _make_doc_id(base_id, idx, content)

        ids.append(doc_id)
        documents.append(content)
        metadatas.append(meta_to_dict(meta))

    if not ids:
        return 0

    # ---- Step 3：批量写入 Milvus（向量检索）----
    vector_store.upsert(ids, documents, metadatas)

    # ---- Step 4：批量写入 RedisSearch（BM25 全文索引）----
    # cache_service 本身没有 pipeline，底层 redis 客户端才有
    pipe = cache_service.redis.pipeline()
    for doc_id, content in zip(ids, documents):
        pipe.hset(f"{DOC_PREFIX}{doc_id}", mapping={"content": content, "source": source})
    pipe.execute()

    return len(ids)


def main():
    """主函数：遍历所有 .md 文件并入库。"""
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
    vector_store = create_vector_store(load_vector_db_config())

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
