# ============================================================
# Embedding / 向量库相关常量
# 作用：ChromaDB 集合名、文档切分参数等
# 原位置：embedding.py / init.py（两处重复定义）
# ============================================================

# ChromaDB 集合名：FAQ 知识库向量存储的集合名称
# 注意：embedding.py 和 init.py 原各定义一份，统一在此处管理，避免两处值漂移
COLLECTION_NAME = "FAQ_KNOWLEDGE_BASE"

# 文档切分大小（字符数）：RecursiveCharacterTextSplitter 的 chunk_size
# 2026-09-19 P0 重切（H-20260919-07）：300→800。
# 此前 300 把 MarkdownHeader 切好的小节二次切碎，知识点被割裂。
# Header 切分已按 ##/### 保留标题边界，超长小节再按 800 二次切。
CHUNK_SIZE = 800

# 文档切分重叠（字符数）：相邻 chunk 之间的重叠，保证上下文连贯
CHUNK_OVERLAP = 100

# Embedding 模型名（SiliconFlow bge-m3，1024 维）
EMBEDDING_MODEL_NAME = "BAAI/bge-m3"

# 重排模型名（SiliconFlow bge-reranker-v2-m3）
RERANK_MODEL_NAME = "BAAI/bge-reranker-v2-m3"

# 向量距离度量：ChromaDB 集合使用 cosine 相似度
VECTOR_DISTANCE_METRIC = "cosine"
