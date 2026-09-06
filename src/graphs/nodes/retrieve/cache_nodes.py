"""缓存节点：检查检索缓存 + 写入检索缓存。

拆分自原 retrieve_graph.py 的 check_cache 和 store_cache 闭包函数。
依赖：cache_service（Redis 检索缓存全局单例），通过参数注入。
"""

from langchain_core.runnables.config import RunnableConfig
from loguru import logger

from graphs.state import RAGState


def check_cache(state: RAGState, config: RunnableConfig, cache_service) -> dict:
    """检查检索缓存是否命中。

    缓存键 = thread_id + LSH 语义桶（query 向量的随机投影哈希），
    命中后还需用在线重排模型验证候选问题与当前问题语义等价（防止同义问题误命中）。

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
    # 无 thread_id（如评估脚本/离线调用）时跳过缓存，避免 Redis 写入 None 报错
    if not thread_id:
        return {"cache_hit": False}
    query_in_cache = cache_service.query_cache(thread_id, question, 3)
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
