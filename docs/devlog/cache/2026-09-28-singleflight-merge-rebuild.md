---
docs_sync: none（已同步 2026-09-28：07 篇新增 §3.5b/架构图/Q12/设计取舍第 8 条/待确认区、04 篇流程图与职责表、DESIGN_NOTES 缓存节、根 README 功能特性、简历 Bullet B6、Wiki 缓存页防雪崩两环）
---

# 2026-09-28 singleflight 落地：并发回源合并（缓存雪崩防护第二环）

## 背景

缓存雪崩防护分两环：TTL 抖动（已完成，`2026-09-28-cache-avalanche-ttl-jitter.md`）分散**过期时刻**；singleflight 把**过期后第一波并发回源**合并成一次——同一时刻同一 key 只放一个请求进完整检索（embedding + rerank + LLM 改写），其余请求 await 同一结果。

## 架构约束（决定落点）

Mitta 的检索是 LangGraph 图：`check_cache → (未命中) 检索链路 → rerank → store_cache`。
- 回源跨越多个图节点，singleflight 不能包"整条链"，只能拆成两半：
  - **check_cache 未命中分支**：`wait_or_lead(key)`——有 leader 在回源则等待复用；否则登记自己为 leader，放行回源。
  - **store_cache 完成点**：`complete_rebuild(key, result)`——先广播（等待者立即拿到同一份结果），再写缓存（兜底广播之后才到的请求直接命中）。
- 合并 key 用 **L3a 缓存 key**（`rcache:x:{collection}:{hash(问题)}`）：同问题同知识库 = 同结果，粒度正确且跨 thread 共享（L3b 是 thread 隔离的，不能做合并粒度）。
- 单实例部署 → 进程内 `asyncio.Future` 实现，零轮询、立即广播；多 worker 需换 Redis 锁（已记遗留）。

## 关键设计（每个都是反例推出来的）

| 设计 | 反例 |
|---|---|
| follower 等待带 timeout（20s）+ 超时后自己回源 | leader 回源异常/图中断时 Future 永不完成，无超时 = 永久挂起（singleflight 最常见 bug） |
| 超时/异常时 pop 登记 | 不清理则后续请求对死 Future 空等 |
| complete_rebuild 幂等（pop 后再 set） | leader 迟到/重复广播无害 |
| 先广播再写缓存 | 广播丢失时等待者仍能通过缓存命中兜底 |
| 只包未命中路径（命中直接返回） | 命中路径过闸门 = 并发命中被无谓串行化 |
| 无 thread_id（离线评估）跳过合并 | 评估脚本不依赖合并语义，直接放行 |

## 改动文件

- `src/service/cache_service.py`：`import asyncio`；`__init__` 加 `_inflight: dict[str, Future]`；新增 `wait_or_lead(key, timeout=20)` / `complete_rebuild(key, result)`。
- `src/graphs/nodes/retrieve/cache_nodes.py`：`check_cache` 改 `async def`（LangGraph 支持 async 节点，主链路 ainvoke），两级缓存未命中后接 `wait_or_lead`，等待到结果直接复用返回；`store_cache` 写缓存前调 `complete_rebuild` 广播。

## 验证

- `ast.parse` 两个改动文件：通过。
- asyncio 逻辑测试 5 场景（`__new__` 绕过模型加载）：
  1. 1 leader + 4 follower 同 key：follower 全部复用 leader 的同一结果，登记清理正确；
  2. 不同 key 互不干扰；
  3. leader 中断（不 complete）：follower 超时兜底自己回源，登记清理；
  4. leader 先返回、complete 广播给等待者，重复 complete 幂等无害；
  5. follower 超时后 leader 迟到 complete 无害，新请求重新竞争当 leader。
- 图构建兼容性审查：check_cache 经 partial 绑定注册，条件边只读 `cache_hit` 字段，async 节点在 ainvoke 链路正常。

## 遗留项

- **多 worker 部署**：进程内 Future 只合并同进程请求；若将来 uvicorn 多 worker，需换 Redis `SET NX` 锁 + 轮询/订阅，跨实例合并。
- **L3b 检查重复**：两个并发请求在进 singleflight 前都会各自做一次 L3b 缓存检查（embed + rerank 验证）。这是"缓存验证"不是"完整回源"，成本低于合并目标，暂不合并。
