"""per-user 串行锁：长期记忆「读档案 → LLM 合并 → store.put」必须按用户原子化。

竞态来源：
1. memory_node 的 fire-and-forget 后台任务（用户快速连发多条时，多轮任务并发写同一档案，
   last-write-wins 会静默丢失上一轮提取的信息）；
2. user_profile（llm_node 回答前同步写用户名）与 memory_node 后台任务并存时双写同一 key。

两个写入方必须共享同一把锁才能互斥，故抽为公共模块。
锁对象按需创建，个人规模（千级用户）内存开销可忽略；超大用户量需改 LRU 淘汰。
"""

import threading

_locks: dict[str, threading.Lock] = {}


def get_per_user_lock(user_id: str) -> threading.Lock:
    """按 user_id 获取（或创建）该用户的串行锁。

    dict.setdefault + 创建后立即返回：同一 user_id 的并发调用在 GIL 保护下
    只会创建/返回同一个锁对象，保证互斥语义。

    Args:
        user_id: 用户 ID

    Returns:
        该用户的 threading.Lock（不同用户互不阻塞）
    """
    return _locks.setdefault(user_id, threading.Lock())
