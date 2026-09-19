import os
import hashlib
import re as _re
from typing import List

from dotenv import load_dotenv
from langchain_core.documents import Document
from redis.commands.search import Search
from redis.commands.search.index_definition import IndexDefinition, IndexType
from redis.commands.search.field import TextField, NumericField, TagField, VectorField
import numpy as np
import redis
# embed_model / online_rerank 由构造函数注入（依赖注入），不再全局 import
import json
import time
import uuid
from loguru import logger
from constant.cache_constant import REDIS_INIT_SUCCESS, REDIS_CONNECT_FAILED, REDIS_CONNECT_CLOSED, \
    HEALTH_CHECK_INTERVAL, \
    TAG_FIELD, VECTOR_FIELD_NAME, VECTOR_FIELD_ALGORITHM, VECTOR_ATTRIBUTE, INDEX_NAME, KEY_PREFIX, CACHE_DEFAULT_TTL, \
    CACHE_RERANK_HIT_SCORE, SPARSE_INDEX_NAME, DOC_PREFIX, \
    CACHE_LAYER_ENABLED, \
    REWRITE_CACHE_PREFIX, REWRITE_PROMPT_VERSION, REWRITE_CACHE_TTL, \
    EMBED_CACHE_PREFIX, EMBED_CACHE_TTL, \
    KB_VERSION, RETRIEVE_EXACT_PREFIX, CACHE_KNN_FAST_K, CACHE_RERANK_STRONG_HIT
from redis.commands.search.query import Query

from utils.doc_util import documents_to_dicts, dict_to_documents
from utils.lsh_util import RandomProjectionLSH

load_dotenv(override=True)
REDIS_DB_URL = os.getenv("REDIS_DB_URL")

_WS_RE = _re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """文本归一化，供所有「按文本 hash 命中」的缓存层共用。

    做三件事：全角空格→半角、首尾去空白、连续空白折叠成一个、ASCII 转小写
    （中文不受 lower 影响）。目的是让「  Mitta 是什么 ？ 」和「Mitta 是什么?」
    落到同一个 hash —— 这是 L1/L2/L3a 三层能共享的前提。
    """
    t = (text or "").replace("\u3000", " ").strip()
    t = _WS_RE.sub(" ", t)
    return t.lower()


def text_hash(text: str) -> str:
    """归一化文本的 sha256 前 32 位十六进制（缓存 key 用）。"""
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()[:32]


class CacheService:
    db_url: str

    @staticmethod
    # 支持格式: redis://host:port[/db] 或 redis://:password@host:port[/db]
    def parse_url(redis_db_url=REDIS_DB_URL):
        prefix, suffix = redis_db_url.split("//")
        # 处理 password@host 格式
        if "@" in suffix:
            auth, host_port = suffix.split("@", 1)
            password = auth.split(":")[-1] if ":" in auth else auth
        else:
            host_port = suffix
            password = prefix.split(":")[-1] if ":" in prefix else ""
        # 去掉路径部分（数据库编号，如 /0），避免 int("6379/0") 报错
        host_port = host_port.split("/")[0]
        host, port = host_port.split(":")
        return host, int(port), password

    def __init__(self, redis_db_url: str = REDIS_DB_URL, index_name:str = INDEX_NAME, cache_ttl = CACHE_DEFAULT_TTL, embed_model=None, online_rerank=None):
        self.db_url = redis_db_url or os.getenv("REDIS_DB_URL")
        self.host, self.port, self.password = self.parse_url(self.db_url)
        self.redis = redis.Redis(
            host=self.host,
            port=self.port,
            password=self.password,
            # 超时保护：Redis 半开/卡顿时快速失败，避免 mget 等操作挂起数十秒。
            # 实测无超时下 TCP 半开会挂起约 34 秒（LangGraph 节点缓存 RedisCache.get
            # 每次节点到达都会 mget），导致 SSE 流长时间无事件、前端超时中断丢消息。
            socket_timeout=3,
            socket_connect_timeout=3,
        )
        self.index_name = index_name
        self._lsh = None      # LSH 模型复用：planes 必须固定，否则同一 query 每次映射不同 bucket，缓存 key 无限膨胀
        self._lsh_dim = 0
        self.cache_ttl =cache_ttl
        # 依赖注入：embed_model / online_rerank 由调用方传入；未传入时延迟导入（兼容旧调用）
        if embed_model is None or online_rerank is None:
            from init import embed_model as _embed, online_rerank as _rerank
            self.embed_model = embed_model or _embed
            self.online_rerank = online_rerank or _rerank
        else:
            self.embed_model = embed_model
            self.online_rerank = online_rerank


    def open(self, embed_model=None, online_rerank=None):
        """初始化 Redis 连接和索引。

        Args:
            embed_model: Embedding 模型（依赖注入），None 时用构造函数注入的
            online_rerank: 在线重排函数（依赖注入），None 时用构造函数注入的
        """
        # 允许在 open 时覆盖注入的依赖（main.py lifespan 统一注入）
        if embed_model is not None:
            self.embed_model = embed_model
        if online_rerank is not None:
            self.online_rerank = online_rerank
        self.create_index()
        self.create_sparse_index()
        try:
            if self.redis.ping():
                logger.success(REDIS_INIT_SUCCESS)
        except redis.ConnectionError as err:
            logger.error(f"{REDIS_CONNECT_FAILED}:{err}")
            raise

    def close(self):
        if self.redis:
            self.redis.close()
            logger.info(REDIS_CONNECT_CLOSED)

    def __enter__(self):
        self.open()
        return self          # 返回 self，以便在 with 块中使用

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False         # 不抑制异常，异常会继续抛出

    def create_sparse_index(self, sparse_index_name=SPARSE_INDEX_NAME, doc_prefix=DOC_PREFIX):
        """创建 BM25 全文索引（RedisSearch）。失败不阻塞主链路，BM25 是优化而非硬依赖。"""
        index_name = sparse_index_name

        # 检查索引是否存在，不存在则创建
        try:
            self.redis.execute_command("FT.INFO", index_name)
            logger.info(f"BM25 索引已存在: {index_name}")
            return
        except redis.exceptions.ResponseError:
            # 索引不存在，正常创建
            pass
        except Exception as e:
            # Redis 未加载 RediSearch 模块 / 连接异常等，降级跳过 BM25
            logger.warning(f"BM25 索引检查失败，跳过（不影响主链路）: {e}")
            return

        try:
            self.redis.execute_command(
                "FT.CREATE", index_name,   # ★ 用 index_name，不是全局 INDEX_NAME
                "ON", "HASH",
                "PREFIX", "1", doc_prefix,
                "SCHEMA",
                "content", "TEXT", "WEIGHT", "1.0",
                "source", "TAG",
                "category", "TAG",
            )
            logger.success(f"BM25 索引创建成功: {index_name}")
        except Exception as e:
            # 索引已被其他进程创建 / 模块异常，降级跳过
            logger.warning(f"BM25 索引创建失败，跳过（不影响主链路）: {e}")

    def create_index(self, tag_field=TAG_FIELD,vector_field_name=VECTOR_FIELD_NAME,
        vector_field_algorithm = VECTOR_FIELD_ALGORITHM,
        vector_attribute=VECTOR_ATTRIBUTE):
        # 定义索引字段
        schema = (
            TagField(tag_field),                # 用于过滤
            VectorField(
                vector_field_name,
                vector_field_algorithm,          # 向量索引算法（flat / hnsw）
                vector_attribute
            ),
            TextField("query_text"),              # 可选，用于调试
            NumericField("created_at")            # 可选
        )

        # 创建索引（如果不存在)
        try:
            self.redis.ft(self.index_name).create_index(schema, definition=IndexDefinition(prefix=[KEY_PREFIX]))
            logger.success("success")
        except Exception as e:
            logger.info("Index may already exist:", e)

    def query_to_vector(self, query: str) -> list[float]:
        """将查询文本转为向量（用注入的 embed_model）。"""
        return self.embed_model.embed_query(query)

    def _get_lsh(self, dim: int) -> RandomProjectionLSH:
        """惰性创建并复用 LSH 模型（planes 固定，保证同一 query 稳定映射同一 bucket）"""
        if self._lsh is None or self._lsh_dim != dim:
            self._lsh = RandomProjectionLSH(dim=dim, num_bits=64)
            self._lsh_dim = dim
        return self._lsh

    def set_key(self, thread_id: str, query_vector: list[float]):
        dim = len(query_vector)
        lsh_model = self._get_lsh(dim)
        # get_bucket_id 内部会再做一次 hash，这里必须传原始向量（1024 维）；
        # 传 hash 后的 64 位结果会 np.dot((64,1024),(64,)) 维度不匹配报错
        bucket_id = lsh_model.get_bucket_id(query_vector)
        key = f"retrieve_cache:{thread_id}:{bucket_id}"
        return key

    def store_cache(self, thread_id: str, query_text: str,  result: List[Document]):
        """写检索结果缓存：同时写 L3a（精确 hash）和 L3b（语义向量桶）。

        2026-09-19 H-20260919-10：向量化走 L2 缓存，写入侧同样不再重复付费。
        """
        # L3a 精确层先写：命中率不如语义层，但命中时 0 embedding / 0 rerank，性价比最高
        self.store_exact_cache(query_text, result)

        query_vector = self.embed_query_cached(query_text)
        key = self.set_key(thread_id, query_vector)
        serializable_result = documents_to_dicts(result)
        try:
            # 缓存是优化而非正确性依赖：Redis 不可用/超时（socket_timeout=3）时
            # 跳过写入，不让缓存失败阻塞检索主链路
            self.redis.hset(key, mapping={
                "thread_id": thread_id,  # 索引的 Tag 字段，query_cache 按它过滤（缺失会导致 KNN 永远查不到）
                "query_embedding": np.array(query_vector, dtype=np.float32).tobytes(),  # 必须转换为二进制
                "query_text": query_text,
                "result": json.dumps(serializable_result, ensure_ascii=False),
                "created_at": time.time()
            })
            self.redis.expire(key, self.cache_ttl)
        except Exception as e:
            logger.warning(f"缓存写入失败，跳过（不影响主链路）：{e}")

    # ============================================================
    # L1 查询改写缓存（2026-09-19 H-20260919-10）
    # ============================================================
    # 命中条件：归一化(问题) + 归一化(历史) 的 hash 完全一致。
    # 不做语义匹配 —— 语义匹配留给 L3b，L1 只承担「同一句话又问了一遍」这种高频场景。
    # 跨用户共享：✅ 改写只依赖 question + history，与 user_id / thread_id 无关。
    #            两个不同用户问同一句话，拿到的改写完全一样，共享是安全的。
    #            （若将来做「按用户画像改写」，需把 user_id 加进 key。）

    def rewrite_key(self, question: str, history: str = "") -> str:
        """L1 缓存键：rw:{prompt版本}:{hash(问题‖历史)}"""
        h = hashlib.sha256(
            (normalize_text(question) + "\x1f" + normalize_text(history or "")).encode("utf-8")
        ).hexdigest()[:32]
        return f"{REWRITE_CACHE_PREFIX}:{REWRITE_PROMPT_VERSION}:{h}"

    def get_rewrite_cache(self, question: str, history: str = "") -> dict | None:
        """读 L1 改写缓存，未命中返回 None（Redis 异常一律按未命中降级）。"""
        if not CACHE_LAYER_ENABLED:
            return None
        try:
            raw = self.redis.get(self.rewrite_key(question, history))
        except Exception as e:
            logger.warning(f"[cache:L1] 读取失败，按未命中处理: {e}")
            return None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    def set_rewrite_cache(self, question: str, history: str, payload: dict) -> None:
        """写 L1 改写缓存。payload 需可 JSON 序列化（main_query / sub_queries / keywords）。"""
        if not CACHE_LAYER_ENABLED:
            return
        try:
            self.redis.set(
                self.rewrite_key(question, history),
                json.dumps(payload, ensure_ascii=False),
                ex=REWRITE_CACHE_TTL,
            )
        except Exception as e:
            logger.warning(f"[cache:L1] 写入失败，跳过（不影响主链路）: {e}")

    # ============================================================
    # L2 embedding 缓存（2026-09-19 H-20260919-10）
    # ============================================================
    # 命中条件：归一化文本 hash 完全一致（embedding 是纯函数，这是唯一正确的条件）。
    # 跨用户共享：✅✅ 最强的一层。「北京天气」无论谁问、在哪个会话问，向量都一样。
    #            高并发下第一个请求付费，后续全部命中 —— 这是削 embedding 成本的主力。
    # 适用：query 向量化（4 条/次）、语义缓存查找（1 条/次）、MMR 候选向量化（~50 条/次）。
    # 不适用：入库侧的文档向量化（一次性，缓存无意义，且会撑爆内存）。

    def _embed_model_tag(self) -> str:
        """embedding 模型标识，进 key 做版本隔离（换模型自动失效）。"""
        return str(getattr(self.embed_model, "model", "unknown")).replace(" ", "_")

    def embed_texts_cached(self, texts: list[str]) -> list[list[float]]:
        """批量向量化，逐条查 L2 缓存，只把未命中的发给 API。

        Returns:
            与 texts 一一对应的向量列表（顺序不变）
        """
        if not CACHE_LAYER_ENABLED or not texts:
            return self.embed_model.embed_documents(texts)

        tag = self._embed_model_tag()
        keys = [f"{EMBED_CACHE_PREFIX}:{tag}:{text_hash(t)}" for t in texts]
        try:
            cached = self.redis.mget(keys)
        except Exception as e:
            logger.warning(f"[cache:L2] mget 失败，全部走 API: {e}")
            return self.embed_model.embed_documents(texts)

        vectors: list[list[float] | None] = [None] * len(texts)
        miss_idx: list[int] = []
        for i, blob in enumerate(cached):
            if blob:
                try:
                    vectors[i] = np.frombuffer(blob, dtype=np.float32).astype(float).tolist()
                    continue
                except Exception:
                    pass
            miss_idx.append(i)
        logger.debug(f"[cache:L2] 命中 {len(texts) - len(miss_idx)}/{len(texts)}")

        if miss_idx:
            raw = self.embed_model.embed_documents([texts[i] for i in miss_idx])
            pipe = self.redis.pipeline(transaction=False)
            for slot, vec in zip(miss_idx, raw):
                vectors[slot] = vec
                pipe.set(
                    keys[slot],
                    np.array(vec, dtype=np.float32).tobytes(),
                    ex=EMBED_CACHE_TTL,
                )
            try:
                pipe.execute()
            except Exception as e:
                logger.warning(f"[cache:L2] 回写失败（不影响结果）: {e}")

        return [v if v is not None else [] for v in vectors]

    def embed_query_cached(self, text: str) -> list[float]:
        """单条向量化（走 L2 缓存），query_cache / 语义缓存查找用。"""
        if not CACHE_LAYER_ENABLED:
            return self.embed_model.embed_query(text)
        return self.embed_texts_cached([text])[0]

    # ============================================================
    # L3a 精确结果缓存（2026-09-19 H-20260919-10）
    # ============================================================
    # 命中条件：归一化问题文本 hash 完全一致。
    # 跨用户共享：✅ 检索结果只取决于 (问题, 知识库)，与 user/thread 无关
    #            （生产向量库是单集合 COLLECTION_NAME，无按用户隔离的文档权限）。
    # 收益：**命中时 0 次 embedding、0 次 rerank、0 次 LLM 改写**，只剩一次 Redis GET。
    #        这正是原实现做不到的 —— 原来命中也要先 embed 才能查 KNN。
    # 代价：每条缓存 4KB 向量 + 结果 JSON；命中即续期，冷问题自然淘汰。

    def retrieve_exact_key(self, question: str) -> str:
        """L3a 缓存键：rcache:x:{知识库版本}:{hash(问题)}"""
        return f"{RETRIEVE_EXACT_PREFIX}:{KB_VERSION}:{text_hash(question)}"

    def query_exact_cache(self, question: str) -> List[Document] | None:
        """L3a 精确命中查询；未命中 / Redis 异常返回 None。"""
        if not CACHE_LAYER_ENABLED:
            return None
        try:
            raw = self.redis.get(self.retrieve_exact_key(question))
        except Exception as e:
            logger.warning(f"[cache:L3a] 读取失败，按未命中处理: {e}")
            return None
        if not raw:
            return None
        try:
            docs = dict_to_documents(json.loads(raw))
        except Exception as e:
            logger.warning(f"[cache:L3a] 结果反序列化失败，按未命中处理: {e}")
            return None
        try:
            self.redis.expire(self.retrieve_exact_key(question), self.cache_ttl)
        except Exception:
            pass
        return docs

    def store_exact_cache(self, question: str, result: List[Document]) -> None:
        """写入 L3a 精确缓存（可与 L3b 语义缓存共存，互不影响）。"""
        if not CACHE_LAYER_ENABLED:
            return
        try:
            self.redis.set(
                self.retrieve_exact_key(question),
                json.dumps(documents_to_dicts(result), ensure_ascii=False),
                ex=self.cache_ttl,
            )
        except Exception as e:
            logger.warning(f"[cache:L3a] 写入失败，跳过（不影响主链路）: {e}")

    def query_cache(self, thread_id: str, query: str, top_k: int = 12) -> List[Document] | None:
        """L3b 语义检索缓存：LSH 分桶 + KNN 召回 + rerank 两阶段验证。

        2026-09-19 H-20260919-10 两处改动：
          1. 向量化改走 L2 缓存（embed_query_cached），同义问题第二次查询不再付 embedding；
          2. rerank 验证改两阶段：先只对 KNN top3 打分，最高分 ≥ 0.7 直接命中
             （不必把 top_k=12 全打一遍）—— 用来抵消 top_k 3→12 带来的 4 倍 rerank 成本。
             实测同义改写 rerank 分 0.89+，0.7 阈值有充足间隔；低分才扩容到 top_k 全量验证。

        Args:
            thread_id: 会话 ID（key 与 Tag 过滤用；知识库跨会话共享，非隔离依据）
            query: 用户原始问题
            top_k: KNN 召回候选条数（默认 12；候选越多同义命中率越高，但 rerank 越贵）

        Returns:
            命中返回缓存的 Document 列表；未命中返回 None
        """
        query_vector = self.embed_query_cached(query)
        # 将向量转为二进制
        query_bytes = np.array(query_vector, dtype=np.float32).tobytes()

        # 构建查询：过滤 thread_id，并按向量相似度排序
        q = (
            Query(f"@thread_id:{{{thread_id}}} => [KNN {top_k} @query_embedding $vec AS vector_score]")
            .sort_by("vector_score")           # 按距离升序排序（COSINE/L2 越小越相似）
            .return_fields("vector_score", "result", "query_text", "created_at")
            .dialect(2)                        # 必须使用 dialect 2 以支持 VECTOR
        )

        # 执行查询，传入向量参数
        params = {"vec": query_bytes}
        try:
            res = self.redis.ft(self.index_name).search(q, query_params=params)
        except Exception as e:
            # 缓存是优化而非正确性依赖：Redis 不可用/超时按未命中降级，不阻塞检索主链路
            logger.warning(f"缓存查询失败，按未命中处理：{e}")
            return None

        if not res.docs:
            return None

        # 向量初筛只做候选召回，不做命中判定：实测 bge-m3 原始 query 向量对短问题的
        # 语义区分度很差（同义改写距离 0.6+，比无关问题还远），绝对距离阈值不可靠，
        # 必须再用重排模型验证候选问题与当前问题是否语义等价，才允许命中缓存
        candidate_texts = [doc.query_text for doc in res.docs]

        # ── 两阶段验证：先 top3 快判，高分直接命中，低分再扩容 ──
        fast_k = min(CACHE_KNN_FAST_K, len(candidate_texts))
        try:
            fast = self.online_rerank(query, candidate_texts[:fast_k], top_n=fast_k)
        except Exception as e:
            logger.warning(f"缓存验证重排调用失败，按未命中处理：{e}")
            return None
        if not fast:
            logger.warning("缓存验证重排返回空结果，按未命中处理")
            return None

        best = fast[0]
        if best["relevance_score"] >= CACHE_RERANK_STRONG_HIT:
            # 快判强命中：省掉剩余 top_k-fast_k 条的 rerank 打分
            return self._take_cache_hit(res.docs, best["index"], best["relevance_score"])

        # 快判不够强：对剩余候选继续验证，取全局最高分
        if len(candidate_texts) > fast_k:
            try:
                rest = self.online_rerank(query, candidate_texts, top_n=len(candidate_texts))
            except Exception as e:
                logger.warning(f"缓存验证重排（扩容）失败，按未命中处理：{e}")
                return None
            if rest:
                best = rest[0]

        if best["relevance_score"] >= CACHE_RERANK_HIT_SCORE:
            return self._take_cache_hit(res.docs, best["index"], best["relevance_score"])

        logger.error(f"未命中，重排最高分 {best['relevance_score']} 低于阈值 {CACHE_RERANK_HIT_SCORE}")
        return None

    def _take_cache_hit(self, docs, index: int, score: float) -> List[Document]:
        """命中后的公共收尾：滑动续期 + 反序列化。"""
        hit = docs[index]
        # 滑动过期：给真实命中的条目续期（hit.id 才是该条目的 Redis key，
        # 按查询向量 set_key 算出的桶 key 未必存在）
        try:
            self.redis.expire(hit.id, self.cache_ttl)
        except Exception:
            pass
        logger.success(f"[cache:L3b] 语义命中 score={score:.3f}")
        return dict_to_documents(json.loads(hit.result))

    def clear_thread_cache(self, thread_id: str) -> int:
        """清除指定 thread_id 的所有检索缓存。

        用于用户更新 system_prompt 等场景：prompt 变更后，旧缓存的检索结果
        可能与新 prompt 不匹配，需失效该用户所有会话的缓存。

        Args:
            thread_id: 会话 ID

        Returns:
            实际删除的 key 数量
        """
        if not thread_id:
            return 0
        pattern = f"retrieve_cache:{thread_id}:*"
        deleted = 0
        # SCAN 遍历匹配 key（避免 KEYS 阻塞 Redis），批量删除
        cursor = 0
        while True:
            cursor, keys = self.redis.scan(cursor=cursor, match=pattern, count=100)
            if keys:
                deleted += self.redis.delete(*keys)
            if cursor == 0:
                break
        if deleted > 0:
            logger.info(f"已清除 thread_id={thread_id} 的检索缓存 {deleted} 条")
        return deleted

    def clear_user_thread_caches(self, user_id: str, thread_ids: list[str]) -> int:
        """批量清除用户多个会话的检索缓存。

        Args:
            user_id: 用户 ID（仅用于日志）
            thread_ids: 会话 ID 列表

        Returns:
            实际删除的 key 总数
        """
        total = 0
        for tid in thread_ids:
            total += self.clear_thread_cache(tid)
        if total > 0:
            logger.info(f"用户 user_id={user_id} 共清除 {len(thread_ids)} 个会话的检索缓存，合计 {total} 条")
        return total


class CachedEmbeddings:
    """L2 embedding 缓存的通用包装器（2026-09-19 H-20260919-10）。

    包在任意 langchain Embeddings / chroma EmbeddingFunction 外面，让所有向量化动作
    （dense_query 的 4 条/次、query_cache 的 1 条/次、MMR 候选的 ~50 条/次）
    统一走 Redis 缓存，未命中的才打到 API。

    之所以做成包装器而不是改 MilvusVectorStore：
      - 一次性覆盖所有调用方（检索 / 工具筛选 / 语义缓存 / MMR），不用逐处改；
      - 底层 embed_model 仍是原来的对象，EmbeddingTextCounter 之类的探针照常生效，
        统计到的就是"真正打到 API 的条数"，缓存收益可直接量化。

    用法（chat_service.open 注入向量库时）：
        vector_store = create_vector_store(cfg, embedding_function=CachedEmbeddings(cache_service, embed_fn))
    """

    def __init__(self, cache: "CacheService", base):
        self.cache = cache
        self.base = base

    # ── langchain Embeddings 接口 ──
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return self.cache.embed_texts_cached(list(texts))

    def embed_query(self, text: str) -> List[float]:
        return self.cache.embed_texts_cached([text])[0]

    # ── chroma EmbeddingFunction 接口（__call__ 接受 str 或 list）──
    def __call__(self, input):
        if isinstance(input, str):
            return self.cache.embed_texts_cached([input])
        return self.cache.embed_texts_cached(list(input))

    # 透传常用属性，避免下游 getattr 拿不到时降级
    @property
    def model(self):
        return getattr(self.base, "model", "unknown")

    def name(self) -> str:
        return getattr(self.base, "name", lambda: "cached")() if hasattr(self.base, "name") else "cached"


# 全局单例：连接生命周期由 main.py 的 lifespan 统一 open()/close()，
# 业务模块（retrieve_graph 等）直接 import 本实例，不自行创建/关闭
cache_service = CacheService()