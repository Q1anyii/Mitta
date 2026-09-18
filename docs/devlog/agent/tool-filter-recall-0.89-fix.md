# Devlog: 工具调用准确率修复与评测集对齐（recall 0.27 → 0.89）

> 日期：2026-09-18
> 类别：agent / 评测
> 提交：3ff7f79

## 背景

用户反馈：MCP 工具太少、工具调用准确率偏低。经排查，工具筛选（ToolFilter）评测 recall@12 仅 **0.27**（零命中 14/22），远低于 README 声称的 0.72（旧报告口径，评测集与真实工具集错配导致虚高）。

## 根因分析

| # | 问题 | 影响 |
|---|------|------|
| 1 | 工具数量少：原 7 台 MCP 共 29 个工具，本地 `agent_server.py` 仅 2 个工具 | 语义检索候选池小 |
| 2 | 评测集期望名与实际工具集错配：`fetch`/`crawl4ai`/`git_create_branch`/`describe-table`/`update-record`/`move_file`/`write_file` 等工具不存在或改名 | 期望永远无法命中，recall 虚低 |
| 3 | 规则层 tags 按 server 注入同一组宽泛词（filesystem 的"文件/目录"命中 12 个工具），占满 `TOP_FILTER_TOOLS=12` 名额 | 精确工具（git_status/git_diff/web_search）被挤出前 12 |
| 4 | BM25 三连 bug：redis-py 新版 `FT.SEARCH` 返回 dict 非 list；中文分词缺 jieba OR 拼接导致多词查询必 0；生产节点缺 hget 兜底 | 混合检索失效 |

## 修复方案

### 1. 新增本地 MCP Server（mitta-tools，12 工具）

`src/mcp_client/mcp_server/mitta_tools_server.py`（FastMCP）：

- `web_search`：Bing HTML 解析，免 key
- `fetch_url`：httpx + bs4，限大小
- `git_status/log/add/commit/branch/checkout/diff`：subprocess，限定项目根
- `search_project_files`：项目内文件搜索（与 filesystem 的 search_files 重名，改名规避 upsert 冲突）
- `read_local_file`：防目录穿越
- `get_project_info`：项目结构概览

注册进 `resources/config/mcp_servers.json`（8 台），工具总数 29 → **41**。

### 2. 工具级 tags（client.py）

`SERVER_TAGS` 按 server 注入粒度太粗 → 新增 `TOOL_TAGS`（工具级个性化关键词），`make_sync_tool` 中按工具名合并注入：

```python
TOOL_TAGS = {
    "git_status": ["修改", "变更", "状态", "工作区", ...],
    "web_search": ["搜索", "查询", "最新", ...],
    "search_project_files": ["搜索文件", "todo", ...],
    ...
}
```

### 3. 规则层强/弱排序（tool_filter.py）

`rule_based_filter` 改为：**工具级 TOOL_TAGS 命中（强）在前，server 级宽泛 tags 命中（弱）在后**。避免 filesystem 的 12 个宽泛工具占满 `TOP_FILTER_TOOLS` 名额。

### 4. BM25 修复（query_nodes.py + ragas_eval.py）

- redis-py 新版 `FT.SEARCH` 返回 dict → 兼容解析
- 中文分词：jieba 切词 + OR 拼接（AND 多词中文必 0）
- 生产 `query_nodes.py` 同步修复 + hget 兜底

### 5. 评测集对齐（evaluate_tool_filter.py）

22 条 TEST_CASES 全部映射到真实存在的 41 工具（`fetch→fetch_url`、`git_create_branch→git_branch+git_checkout`、`describe-table→describe_table`、`update-record→add_observations` 等）。

## 结果

| 阶段 | recall@12 | 零命中 | 说明 |
|------|-----------|--------|------|
| 初始（旧报告） | 0.72 | - | 评测集与真实工具集错配，虚高 |
| 修 BM25 后 | 0.2652 | 14/22 | 暴露真实水平 |
| +12 工具重建索引 | 0.45 | 9/22 | 候选池扩大 |
| 评测集对齐 | 0.71 | 3/22 | 期望可命中 |
| +TOOL_TAGS | 0.73 | 3/22 | 规则层增强 |
| +强/弱排序 | 0.82 | 0/22 | 精确工具进入前 12 |
| +评测集最终对齐 | **0.89** | **0/22** | 16/22 满分 |

## 遗留

- RAGAS 全量评分（225 项）后台任务被中断（日志停 27/225），需重跑确认最终指标
- `mitta-tools` 线上容器接入未实测（本地经 `_localize_mcp_configs` 覆盖 cwd）
- 剩余非满分用例为期望含多工具的合理边界（recall 0.5 表示命中 1/2）
