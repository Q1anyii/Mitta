---
docs_sync: required（文档侧多处提到"检索缓存 TTL 15 秒/雪崩风险低"，现 TTL 已 900s 且加了抖动，需文档撰写 Agent 核对同步）
---

# 2026-09-28 缓存雪崩防护：四层缓存 TTL 加随机抖动

## 背景

用户指出项目存在潜在缓存雪崩风险，要求用最简方案解决：TTL 加抖动。

**结论先行**：雪崩主源不只是"同批写入同 TTL"，而是**滑动续期把热门 key 的过期时刻收敛对齐**——命中时把 TTL 重置为固定值，反复命中的 key 会聚到同一时间窗集体过期、同时回源（embedding / rerank / LLM 改写，代价最高）。抖动必须同时覆盖写入与续期两侧。

## 现状盘点（cache_service.py 内 4 类业务缓存）

| 层 | 用途 | 基础 TTL | 雪崩代价 |
|---|---|---|---|
| L1 `rw:` | 查询改写 | 24h | LLM 改写调用 |
| L2 `emb:` | embedding 向量 | 7 天 | embedding API |
| L3a `rcache:x:` | 精确结果 | 900s | embedding+rerank+改写 |
| L3b `retrieve_cache:` | 语义结果 | 900s | embedding+rerank |

不动的 TTL：限流窗口（语义必须精确）、验证码/失败锁（安全语义）、幂等 key（并发控制）、事件流 7 天清理（防堆积非雪崩源）、LangGraph 节点级缓存（框架内部，短 TTL 影响极小）。

## 改动

`src/service/cache_service.py`：
1. 新增 `_ttl_with_jitter(base_ttl)`：TTL = base + [0, base×10%] 均匀随机，**只增不减**（保证缓存寿命不低于设计值，避免抖动缩短 TTL 反而提前失效；900s ± 90s 对命中率影响可忽略）。
2. 6 处 TTL 使用点全部改走抖动：
   - L1 写：`set_rewrite_cache` 的 `ex`
   - L2 写：`embed_texts_cached` 回写 `ex`
   - L3a 写：`store_exact_cache` 的 `ex`
   - L3a 续期：`query_exact_cache` 的 `expire`
   - L3b 写：`store_cache` 的 `expire`
   - L3b 续期：`_take_cache_hit` 的 `expire`

## 验证

- `ast.parse`：通过。
- `_ttl_with_jitter` 行为（`__new__` 绕过模型加载，5000 次采样）：base=900 → [900, 990] 全部 ≥900；base=5 → [5,6]；base≤0 原样返回。
- `eval_cache_ttl.py` 动态 TTL 测试影响评估：临时改 cache_ttl=5 走 store_cache 后 TTL 变为 5~6s，但测试每 2.5s 轮询一次（2.5 < 5），续期窗口不变，断言不受影响，无需改测试。
- 遗漏检查：grep 全文件 `expire(`/`ex=` 与缓存相关的 6 处已全部带 `_ttl_with_jitter`，无遗漏。

## 遗留项

无。若将来做 L3b 全量 flush（BACKLOG 讨论中），抖动同样适用，无需额外处理。
