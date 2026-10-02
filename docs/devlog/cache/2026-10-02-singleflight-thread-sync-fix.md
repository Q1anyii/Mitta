docs_sync: none（已同步 2026-10-02：07 篇 §3.5b + 04 篇 L52 流程图 + 简历 Bullet L460 + Wiki 缓存页防雪崩两环）

# 修复：线上 P0 —— check_cache async-only 导致同步图启动即崩

## 现象

线上（mittaai.xyz）用户发任何需要检索的消息，AI 气泡吐人格化开场白后随即
"回复中断，请重新生成"，点重新生成红条报错：

```
TypeError: No synchronous function provided to "check_cache".
Either initialize with a synchronous function or invoke via the async API
(ainvoke, astream, etc.)
```

问答完全不可用。报错来自 `langgraph/_internal/_runnable.py:334`
`RunnableCallable.invoke()`：节点只定义了 `afunc`（async def）而没有 `func`（sync def）
时，同步执行路径直接抛错。

## 根因

主图执行架构（读代码确认）：

- SSE 流式入口 `chat_service.stream()` 是**同步生成器**，内部在独立 worker 线程里跑
  `graph.stream(stream_mode=["messages","custom"])` —— **同步 API，线程内无事件循环**；
- 非流式 `a_invoke` 经 `asyncio.to_thread(graph.invoke())` 也是同步路径；
- 主图全部节点（router/retrieve/llm/memory）均为 sync def；
- `retrieve_node`（sync）内部同步 `retrieve_graph.invoke(...)` 调子图。

2026-09-28 引入 singleflight（防缓存雪崩回源合并）时，把 `check_cache` 改成了
`async def`，内部 `await cache_service.wait_or_lead(...)`，而 `wait_or_lead` 用
`asyncio.get_running_loop().create_future()` + `asyncio.wait_for(asyncio.shield(fut))`
实现合并。

这与"worker 线程同步图"架构天然不兼容：worker 线程里没有 running loop，
且每个请求是独立线程，asyncio.Future 跨线程/跨 loop 语义本就不成立。
任何走到 `check_cache` 的请求（两级缓存未命中、需回源）必炸。

## 修复

singleflight 从 asyncio.Future 改为 threading 原语，`check_cache` 回到 sync def：

- `cache_service.py`：
  - `self._inflight: dict[str, asyncio.Future]` →
    `self._sf_events: dict[str, threading.Event]` + `self._sf_results: dict`
    + `self._sf_lock = threading.Lock()`；
  - `wait_or_lead(key, timeout=20.0)` 改同步：lock 内查/建 Event，首访返回
    `(False, None)` 当 leader；follower `ev.wait(timeout)`，醒来从 `_sf_results`
    读结果返回 `(True, docs)`；超时仅在 Event 仍是当代时清理登记后兜底返回
    `(False, None)`；
  - `complete_rebuild(key, result)`：锁内 pop Event、写结果，锁外 `ev.set()` 广播
    （保证 follower 被唤醒时结果已落盘）；幂等。
- `cache_nodes.py`：`async def check_cache` → `def check_cache`，去 `await`；
  docstring 注明节点必须 sync（同步图架构）+ singleflight 用 threading.Event 的原因。

singleflight 语义不变：同 key 并发回源只放一个进检索，其余等结果；leader 异常时
follower 超时兜底自回源。

## 验证

临时脚本（已删）mock 冒烟全过：
1. ast.parse 两文件；AST 级确认无 `import asyncio`、无 `await` 节点；
2. `inspect.iscoroutinefunction(check_cache)` 为 False（确为 sync）；
3. 线程行为四步：首请求登记 leader → follower 线程等待被 complete_rebuild 广播唤醒
   并拿到同一结果 → 广播后登记清除、新请求重新当 leader → 无 complete_rebuild 时
   follower 超时兜底返回 leader。

## 遗留 / 待办

- 起服后补一次真实 Redis 冒烟：发送一个新问题（必走 check_cache 回源）确认线上
  不再报错；再发重复问题确认 L3a 命中。
- ~~文档若描述过 singleflight 为 "asyncio.Future / asyncio.shield" 实现，需同步为
  threading.Event 版本~~ **已同步 2026-10-02**（07 篇 §3.5b + 04 篇 L52 流程图 +
  简历 Bullet L460 + Wiki 缓存页防雪崩两环）。
