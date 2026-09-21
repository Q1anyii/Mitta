"""查询节点：稠密向量检索 + BM25 稀疏检索 + Query 改写。

拆分自原 retrieve_graph.py 的 dense_query、bm25_search、rewrite_query、extract_json。
依赖：vector_store（稠密检索）、cache_service（BM25 的 RedisSearch）、model（Query 改写）。
"""

import json
import re

from langchain_core.runnables.config import RunnableConfig
from loguru import logger

from constant.cache_constant import DOC_PREFIX, SPARSE_INDEX_NAME
from constant.retrieval_constants import REWRITE_PROMPT
from graphs.state import QueryRewriteResult, RAGState
from vector.retrieve_doc import RetrievedDoc
from vector.vector_store import VectorStore


def extract_json(text: str) -> dict:
    """从 LLM 输出中提取 JSON 对象，兼容三种格式：
    1. 纯 JSON 字符串
    2. ```json ... ``` 代码块包裹
    3. 文本中夹杂 JSON（取第一个 { 到最后一个 }）

    Args:
        text: LLM 原始输出文本

    Returns:
        解析后的 dict

    Raises:
        json.JSONDecodeError: 三种格式都无法解析时抛出
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            return json.loads(match.group(1))
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            return json.loads(text[start:end + 1])
        raise


def dense_query(state: RAGState, vector_store: VectorStore) -> dict:
    """稠密向量检索：用原始问题 + 改写后的所有子查询分别检索 ChromaDB。

    每个查询召回 top 20，结果保持二维结构（list[list[RetrievedDoc]]），
    后续 rrf_fusion 按列表遍历做排名融合。

    Args:
        state: 含 question 和 rewritten_queries
        vector_store: 向量存储实例（依赖注入）

    Returns:
        {"rank_list": [[doc1, doc2, ...], [...], ...]} — 每个查询的结果列表
    """
    origin_query = state["question"]
    rewritten_queries = state["rewritten_queries"]
    # 原始问题 + 所有改写查询分别检索，增加召回率
    all_queries = [origin_query] + list(rewritten_queries)
    rank_list = vector_store.query(all_queries, n_results=20)
    return {"rank_list": rank_list}


def run_bm25(query: str, cache_service, top_k: int = 20) -> list[RetrievedDoc]:
    """BM25 稀疏检索的纯函数实现（H-20260919-11 从图节点里抽出，供并行编排复用）。

    与稠密向量检索互补：向量检索擅长语义匹配（"如何修电脑" ≈ "电脑维修方法"），
    BM25 擅长精确关键词匹配（专有名词、错误码、型号等）。

    只依赖 question，**不依赖查询改写结果**，因此可以跟改写并行发起。

    Args:
        query: 检索问题（原始问题即可，BM25 不受指代影响）
        cache_service: Redis 缓存服务（依赖注入，提供 redis 连接执行 FT.SEARCH）
        top_k: BM25 返回的最大文档数

    Returns:
        BM25 结果列表；RedisSearch 不可用/查询异常时返回空列表（降级，不抛异常）
    """
    # RedisSearch 查询语法中 : ( ) - @ 等是特殊字符，中文问句直接传会 Syntax error。
    # 用 jieba 分词后以 OR（|）连接：默认英文分词器对中文按整句分词，
    # 空格 AND 会因中文词无结果而整体返回 0，OR 保证英文/专有名词能命中。
    # 2026-09-18 修复：分词 token 含 . - _ 等字符（代码块/路径类问题如 `..`、`self._`、
    # `UploadFile`、`...`）会触发 Syntax error，对 RedisSearch 特殊字符统一加 \ 转义。
    import jieba
    import re as _re
    _redis_special = _re.compile(r'([,.<>{}\[\]"\'=~!@#$%^&*();:|\-+\\])')
    tokens = [t.strip() for t in jieba.lcut(query) if t.strip() and len(t.strip()) > 1]
    escaped = [_redis_special.sub(r"\\\1", t) for t in tokens]
    safe_query = " | ".join(escaped) if escaped else _redis_special.sub(r"\\\1", query)

    try:
        result = cache_service.redis.execute_command(
            "FT.SEARCH", SPARSE_INDEX_NAME,
            safe_query,
            "NOCONTENT",      # 不返回文档内容，只返回 doc_id 和分数
            "WITHSCORES",     # 返回 BM25 分数
            "LIMIT", "0", str(top_k),
        )
    except Exception:
        # 索引不存在或查询异常，降级：返回空结果（不抛异常，由调用方决定是否还有别的路）
        return []

    # 兼容两种返回格式：新版 redis-py 返回 dict（键为 bytes 或 str）；旧版返回扁平 list
    def _get(d, name):
        return d.get(name) if name in d else d.get(name.encode("utf-8"))
    if isinstance(result, dict):
        docs = []
        for it in (_get(result, "results") or []):
            try:
                raw_id = _get(it, "id") or b""
                if isinstance(raw_id, bytes):
                    raw_id = raw_id.decode("utf-8")
                doc_id = raw_id.replace(DOC_PREFIX, "")  # 去掉文档前缀，得到纯 id
                attrs = _get(it, "extra_attributes") or {}
                content = _get(attrs, "content") or b""
                if not content:
                    # NOCONTENT 模式下 extra_attributes 不含正文，需从 Hash 读取
                    content = cache_service.redis.hget(raw_id, "content") or b""
                src = cache_service.redis.hget(raw_id, "source")
                src = src.decode("utf-8") if isinstance(src, bytes) else (src or "未知文档")
                text = content.decode("utf-8") if isinstance(content, bytes) else (content or "")
                score = _get(it, "score") or 0.0
                docs.append(RetrievedDoc(
                    id=doc_id,
                    text=text,
                    distance=0.0,  # BM25 没有向量距离，用 0 占位，实际分数存 metadata
                    metadata={"source": src, "bm25_score": float(score)},
                ))
            except Exception:
                continue
        return docs

    if not isinstance(result, (list, tuple)) or len(result) < 2:
        return []

    docs = []
    # FT.SEARCH 返回格式：[总数, doc_id1, score1, doc_id2, score2, ...]
    # 从索引 1 开始，步长 2 遍历 (doc_id, score) 对
    for i in range(1, len(result) - 1, 2):
        try:
            raw_id = result[i]
            if isinstance(raw_id, bytes):
                raw_id = raw_id.decode("utf-8")
            doc_id = raw_id.replace(DOC_PREFIX, "")  # 去掉文档前缀，得到纯 id
            score = float(result[i + 1])
            # 从 Redis Hash 中读取文档正文（BM25 索引只存索引，内容存在 HASH 中）
            content = cache_service.redis.hget(raw_id, "content")
            src = cache_service.redis.hget(raw_id, "source")
            src = src.decode("utf-8") if isinstance(src, bytes) else (src or "未知文档")
            text = content.decode("utf-8") if isinstance(content, bytes) else (content or "")
            docs.append(RetrievedDoc(
                id=doc_id,
                text=text,
                distance=0.0,  # BM25 没有向量距离，用 0 占位，实际分数存 metadata
                metadata={"source": src, "bm25_score": score},
            ))
        except Exception:
            continue
    return docs


def bm25_search(state: RAGState, cache_service, top_k: int = 20) -> dict:
    """BM25 稀疏检索图节点：包装 run_bm25，把结果追加到 rank_list（保持二维结构）。

    注意：必须用 append/拼接保持二维（list[list[RetrievedDoc]]），
    若写成 [rank_list, docs] 会把稠密结果整体包一层成三维，rrf_fusion 遍历时
    doc 变成 list 直接 AttributeError。
    """
    docs = run_bm25(state["question"], cache_service, top_k=top_k)
    return {"rank_list": state["rank_list"] + [docs]}


def rewrite_query(state: RAGState, model, cache_service=None) -> dict:
    """Query 改写：用 LLM 将原始问题 + 多轮历史改写为结构化查询。

    L1 改写缓存（2026-09-19 H-20260919-10）：改写是纯函数 f(问题, 历史) -> 查询集合，
    与 user_id / thread_id 无关，因此缓存键只含归一化后的 (问题, 历史)，**可跨用户共享**。
    命中时直接跳过 LLM 调用 —— 实测改写占混合检索链路 2226~2694 ms（约 40%~48%），
    是单节点里最贵的一环。


    输出格式（中文 JSON，由 QueryRewriteResult 映射）：
    {
        "主查询": "改写后的核心问题",
        "子查询": ["扩展查询1", "扩展查询2"],
        "关键词": ["关键词1", "关键词2"]
    }

    解决的问题：
    - 指代消解："它怎么样" → "M1 MacBook Pro 性能怎么样"
    - 查询扩展："怎么学 Python" → ["Python 入门教程", "Python 学习路线", "Python 实战项目"]

    Args:
        state: 含 question 和 history
        model: LLM 实例（依赖注入）

    Returns:
        {"rewritten_queries": [主查询, 子查询1, 子查询2, ...]}
    """
    history_text = "\n".join(
        f"{m['role']}: {m['content']}" for m in state.get("history", [])
    )
    queries = run_rewrite(state["question"], history_text, model, cache_service)
    logger.info(f"重写后问题:{queries}")
    return {"rewritten_queries": queries}


def run_rewrite(question: str, history_text: str, model, cache_service=None) -> list[str]:
    """改写的纯函数实现（H-20260919-11 从图节点抽出，供并行编排复用）。

    Args:
        question: 用户原始问题
        history_text: 已拼好的多轮历史文本（可为空串）
        model: LLM
        cache_service: 传则启用 L1 改写缓存；None 时不缓存（离线评测/降级）

    Returns:
        [主查询, 子查询...]；任何异常都降级为 [question]，不抛异常
    """
    # ── L1 改写缓存：命中即返回，跳过 LLM ──
    if cache_service is not None:
        cached = cache_service.get_rewrite_cache(question, history_text)
        if cached and cached.get("main_query"):
            queries = [cached["main_query"]] + list(cached.get("sub_queries") or [])
            logger.info(f"[cache:L1] 改写缓存命中，跳过 LLM: {queries}")
            return queries

    prompt = REWRITE_PROMPT.format(
        question=question,
        history=history_text or "无",
    )
    logger.info("正在进行Query 改写 + 重排序")

    # response_format={"type": "json_object"} 要求模型返回合法 JSON（DeepSeek 支持）
    resp = model.invoke(prompt, response_format={"type": "json_object"})
    raw_json = extract_json(resp.content)
    # 容错：LLM 偶尔返回 {"queries": [...]} 等非预期格式，Pydantic 校验失败时降级处理
    try:
        result = QueryRewriteResult(**raw_json)
        queries = [result.main_query] + result.sub_queries
        # 写 L1 缓存：只缓存校验通过的结构化结果，避免把降级兜底值写进去
        if cache_service is not None:
            cache_service.set_rewrite_cache(question, history_text, raw_json)
    except Exception:
        logger.warning(f"Query 改写返回格式异常，尝试兼容解析: {list(raw_json.keys())}")
        if isinstance(raw_json.get("queries"), list) and raw_json["queries"]:
            queries = raw_json["queries"]
        else:
            # 最终兜底：用原始问题，不阻塞检索
            queries = [question]

    return queries
