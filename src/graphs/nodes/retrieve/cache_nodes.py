"""缓存节点：检查检索缓存 + 写入检索缓存。

拆分自原 retrieve_graph.py 的 check_cache 和 store_cache 闭包函数。
依赖：cache_service（Redis 检索缓存全局单例），通过参数注入。
"""

from langchain_core.runnables.config import RunnableConfig
from loguru import logger

from graphs.state import RAGState


def check_cache(state: RAGState, config: RunnableConfig, cache_service) -> dict:
    """检查检索缓存是否命中。

    两级查找（2026-09-19 H-20260919-10）：
      L3a 精确层 —— key = hash(归一化问题)，一次 Redis GET，命中时 0 次 embedding / 0 次 rerank。
                    跨用户、跨会话共享（检索结果只取决于问题 + 知识库，与 thread 无关）。
      L3b 语义层 —— key = thread_id + LSH 桶，需先 embed 再 KNN 召回，最后用 rerank 验证
                    候选问题与当前问题语义等价（防止同义问题误命中）。

    L3a 存在的原因：原实现只有 L3b，而 L3b 必须先算向量才能查 KNN，
    所以"命中"也要付 1 次 embedding + 12 条 rerank，收益只是延迟减半而非数量级下降。
    L3a 把"字面重复提问"（用户重发、刷新重试、复制粘贴）这条高频路径彻底打平。

    Args:
        state: 当前图状态，含 question
        config: LangGraph 配置，含 configurable.thread_id
        cache_service: Redis 检索缓存服务（依赖注入）

    Returns:
        命中时返回 {"reranked_docs": [...], "cache_hit": True}；
        未命中返回 {"cache_hit": False}
    """
    question = state["question"]
    thread_id = config["configurable"].get("thread_id", None)

    # ── L3a 精确层：不需要 thread_id，也不需要 embedding ──
    exact = cache_service.query_exact_cache(question)
    if exact:
        logger.success("[cache:L3a] 精确命中，跳过整个检索链路（0 embedding / 0 rerank）")
        return {"reranked_docs": exact, "cache_hit": True}

    # ── L3b 语义层：需要 thread_id（写缓存要挂 thread Tag）──
    # 无 thread_id（如评估脚本/离线调用）时跳过 L3b，避免 Redis 写入 None 报错
    if not thread_id:
        return {"cache_hit": False}
    query_in_cache = cache_service.query_cache(thread_id, question, 12)
    if query_in_cache:
        logger.success("缓存命中，直接返回")
        return {"reranked_docs": query_in_cache, "cache_hit": True}
    return {"cache_hit": False}


def store_cache(state: RAGState, config: RunnableConfig, cache_service) -> dict:
    """将本次检索结果写入缓存（仅缓存未命中时执行）。

    缓存 TTL 走 CacheService.store_cache 默认值（15 秒），
    适合短时间内重复提问同一问题的场景（如用户连续发送相同消息）。

    Args:
        state: 当前图状态，含 question 和 reranked_docs
        config: 含 thread_id
        cache_service: Redis 检索缓存服务（依赖注入）

    Returns:
        空 dict（不修改状态）
    """
    thread_id = config["configurable"].get("thread_id", None)
    if not thread_id:
        return {}
    if not state.get("cache_hit"):
        # ttl 走 CacheService.store_cache 默认值（15s）；不要对 user_id 调 redis TTL——
        # TTL 只能查已存在 key 的剩余时间，user_id 不是 key，返回 -2 会导致 expire 异常
        cache_service.store_cache(thread_id, state["question"], state["reranked_docs"])
    return {}
