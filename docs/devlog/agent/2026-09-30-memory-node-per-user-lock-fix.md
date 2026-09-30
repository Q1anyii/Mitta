docs_sync: none（已同步 2026-09-30：05-记忆体系.md 三轮说明 4 处；简历 Bullet 记忆节点 L113/L287；Wiki 双层记忆）
# memory_node 长期记忆并发竞态修复（per-user 锁提升模块级 + 双写互斥）

日期：2026-09-30
分类：agent（长期记忆）

## 问题

1. **per-user 锁失效（真实 bug）**：H-20260920-01 二轮方案加入的用户级锁 `_user_locks` 定义在 `memory_node` **函数体内**，每轮调用都重建空字典——闭包捕获的是当轮新建的锁，同 user_id 的多轮 fire-and-forget 后台任务各拿各的锁，互斥语义完全失效。用户快速连发多条消息时，多个任务基于同一份旧档案快照并发「LLM 合并 → store.put」，last-write-wins 静默丢失上一轮提取的长期信息。
2. **双写竞态（窗口极小但存在）**：`user_profile._ensure_username_profile`（llm_node 回答前同步写用户名）与 memory_node 后台任务写同一 `("rag_chat", user_id)/user_profile`，但用的是不同锁/无锁——用户名变更轮与后台合并并发时仍可能互相覆盖。
3. 排障性：`logger.error` 无堆栈；异常/重放路径 `state["messages"]` 为空时 `[-1]` 会 IndexError 污染主图。

## 改动

1. **新增 `src/graphs/utils/per_user_lock.py`**：模块级 `dict[str, threading.Lock]` + `get_per_user_lock(user_id)`，作为唯一权威的 per-user 锁源。
2. **`memory_node.py`**：
   - 删除函数内失效的 `_user_locks`/`_lock_for`，改用公共 `get_per_user_lock(uid)`；
   - 新增 `state["messages"]` 空防御（警告跳过，避免 IndexError）；
   - `logger.error(..., exc_info=True)` 保留堆栈；
   - 保留既有的空串兜底 `not new_profile or ...` 与线程池 `max_workers=8`。
3. **`user_profile.py`**：`_ensure_username_profile` 的「读档案 → 合并 → put」整体包进同一把 `get_per_user_lock(user_id)`，与 memory_node 后台任务互斥。

## 验证

`ast.parse` 三文件通过；完整 `import graphs.nodes.memory_node` / `graphs.utils.user_profile` 无错误；运行时断言：
- 同 user_id 两次 `get_per_user_lock` 返回同一锁对象、不同 user_id 返回不同锁；
- memory_node 源码无 `_user_locks` 残留、含 `get_per_user_lock(uid)`、`exc_info=True`、空串兜底、messages 防御；
- user_profile 含 `get_per_user_lock(user_id)`。

## 遗留

- per_user_lock 字典随用户数增长（个人规模可忽略，超大用户量需 LRU 淘汰）；
- 运行时并发冒烟（快速连发消息验证长期记忆不丢）待用户起服后补。
