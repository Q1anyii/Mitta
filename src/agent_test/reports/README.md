# 评测产物归档目录

`src/agent_test/` 根目录只放**脚本源码**；所有**生成的报告**（`*.json` / `*.md` / `*.csv`）
统一落到 `reports/<日期>/` 下，避免"十几份报告混在源码目录里、分不清哪份是当天的"。

## 目录约定

```
src/agent_test/
├── *.py                        # 评测脚本源码（唯一允许放在根目录的产物）
├── report_path.py              # 报告落盘路径助手
└── reports/
    ├── README.md               # 本文件
    ├── 2026-09-19/             # 当天新跑的评测数据（脚本默认写入目录）
    ├── 2026-09-18/             # 前一天
    └── legacy/                 # 2026-09-18 之前的历史产物，不再原地更新
```

- 归属按**文件 mtime** 判定，不做内容考古；跑多次覆盖当天同名文件。
- 需要保留多版本时给脚本传 `--tag run2`（脚本自行拼文件名）。
- 2026-09-19 起新增/改造的脚本（`eval_routing.py`、`eval_sse.py`、`eval_cache_hitrate.py`）
  已接入 `report_path.resolve_report_path()`，默认写 `reports/<今天>/`；
  其余脚本仍写死根目录文件名，跑完需手工归档。

## 2026-09-19 当天产物

| 文件 | 来源脚本 | 说明 |
|---|---|---|
| `routing_eval_report.json` / `_run2.json` | `eval_routing.py` | 统一路由评测（37 条，意图 + 人格双维度），两次独立运行 |
| `routing_eval_report.LEGACY_classify_node.json` | — | 旧 94.12% 那版（打的是已废弃 `classify_node`），留档对比 |
| `persona_router_eval_report.json` / `.LEGACY.json` | `persona_router_eval.py` | H-11 合并前的老产物，已标记 DEPRECATED |
| `sse_eval_report.json` | `eval_sse.py` | 分场景首 token（shortcut / no_retrieval / retrieval） |
| `sse_eval_report.LEGACY_20260823.json` | — | 旧版单场景首 token，留档 |
| `memory_eval_report.json` | `eval_memory.py` | 长期记忆读写延迟（4.56 ms / 2.49 ms 覆盖旧的 46 ms） |
| `cache_hitrate_eval_report.json` | `eval_cache_hitrate.py`（新） | 同义改写命中率 + 有无缓存 embedding 调用率 |
| `ragas_judge_report.json` | `eval_ragas_judge.py` | 自写 LLM-judge 五指标（**不是** RAGAS 官方库） |
| `h07_p0p1_report.json` | `eval_retrieval.py` | H-07 P0/P1 检索口径（avg_recall 0.8095 出处） |
| `project_retrieval_eval_*.json` / `retrieval_eval_report.json` | `eval_retrieval.py` | 检索召回率多口径对比 |
| `tool_*_eval_report.json` | `eval_tool_*.py` | 工具装配 / 安全 / 截断 |
| `diagnose_report.md` / `project_diagnose_report.md` | 诊断脚本 | 大体积诊断输出，仅留档，不作为数字出处 |

## 引用注意

对外（文档 / 演示）引用数字时，**必须带生成日期和脚本口径**：
同一份报告名在不同日期内容不同，只写文件名会读到旧数。
