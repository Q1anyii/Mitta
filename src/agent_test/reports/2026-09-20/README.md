# 2026-09-20 记忆模块评测（线上生产环境）

## 环境标注

| 报告文件 | 环境 | 链路 |
| --- | --- | --- |
| `memory_eval_report.production.json` | **线上生产环境**（www.mittaai.xyz，服务器 121.199.38.43） | `docker exec mitta-api` 容器内 → `postgres:5432`（容器网络，生产真实链路） |
| `memory_eval_report.public_net.json` | 公网直连（本地 Windows → 服务器公网 IP） | 本地 → `121.199.38.43:5432`（含公网 RTT，仅作对照） |

## 测试方法（agent_test.eval_memory）

- 样本量：写入 80 次/轮、读取 300 次/轮；warmup 前 10 次不计入统计
- 重复 3 轮，各项指标取中位数；完整分布 avg / p50 / p95 / max / min
- 写入：模拟 memory_node 同一 `user_profile` key 覆盖写、画像文本逐轮增长
- 读取：热点 key（user_profile 占 2/3）+ 60 个 fact key 混合读
- 测试 namespace `("agent_test", <uid>, prefix)`，跑完自动清理

## 关键结果（3 轮中位数）

| 指标 | 生产容器内 | 公网直连（对照） |
| --- | --- | --- |
| 写入 avg / p95 | 1.77 / 3.01 ms | 23.35 / 28.00 ms |
| 读取 avg / p95 | 1.34 / 2.09 ms | 44.58 / 51.87 ms |
| 读取 max | 7.58 ms | 70.06 ms |
| 重复写入减少率 | 50%（第二轮全重复全部跳过） | 50% |
| 对话画像 10 轮 | 10 条记忆，总写入 22 ms | 212 ms |

## 结论

生产容器内链路读写 P95 ≤ 5ms，存储无瓶颈；公网直连差距几乎全部来自公网 RTT，不能代表生产链路性能。
