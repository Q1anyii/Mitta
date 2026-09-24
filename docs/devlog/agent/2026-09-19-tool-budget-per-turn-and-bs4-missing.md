# 工具调用上限误判为「会话累计」 + fetch_url 缺 bs4 依赖 + E1 评测结果不可复现

docs_sync: none（已同步 2026-09-19：项目详解 03 篇 §3.5(3) 按轮计数重写 + 设计取舍第 10 条 + 新增 Q3.5 + 口述稿补状态作用域段 + 待确认补线上未验收；06 篇新增 §3.3c 缺 bs4 完整复盘（含与 3.3b 的对照表）+ 设计取舍第 7 条 + Q9/Q10 + 口述稿补第二起事故 + 待确认补未上线与建议未实现；11 篇 §3.1.14 用例 6→9 条重写；索引 README 03/06 亮点、冲突节新增 3 条、待补充新增 3 条；项目描述 Bullet 评测表 + B1 编排行 + B3 两起事故 + 更新记录 + 修正建议 14/15；根 README 条件路由补注、E5 行、新增「MCP 工具故障排查」对照表。提交见下）
docs_sync: none（追加同步 2026-09-19 21:0x：文末「追加发现：E1 路由评测结果不可复现」一节已同步至根 README E1 行、AGENT_EVAL_MATRIX E1 行 + 复跑波动小节、项目详解 11 篇 §3.1.1 + 待确认第 9 条 + 备注 3、索引 README 冲突节 + 待补充项、项目描述 Bullet B2 行 + 修正建议 3 + 对外讲法 + Q&A）

日期：2026-09-19
类别：agent / mcp / 工具 / 评测
提交：a4e1bd4

## 现象

用户在会话里发了一条微信公众号文章链接要求阅读。Agent 尝试抓取，连续两次
失败，返回给用户的答复是：

> 我尝试抓取了…但连续两次都失败了，返回的错误是抓取组件缺少依赖模块
> `No module named 'bs4'`，属于工具侧的环境问题
> 本轮的工具调用次数已经用满，我没法再重试

两个问题叠在同一处，互相掩盖，需要分开看。

## 问题一：fetch_url / web_search 缺 bs4

`src/mcp_client/mcp_server/mitta_tools_server.py` 的两个联网工具在**函数体内**
按需 import：

```python
@mcp.tool()
async def fetch_url(url: str) -> str:
    import httpx
    from bs4 import BeautifulSoup   # ← 第 91 行
```

`beautifulsoup4` 从未出现在 `requirements.txt` 里，镜像 `python:3.12-slim`
装完依赖也没有 bs4。因为 import 写在函数体内：

- MCP server **启动不报错**，工具列表照常注册，`tools/list` 能看到这两个工具；
- 只有真正调用时才抛 `ModuleNotFoundError`，且是作为工具执行异常返回给模型。

于是线上表现成「工具在，但一用就坏」，比工具直接缺席更难发现——工具缺席会
走 `unavailable` 分支、模型会如实说"没有这个工具"；而这里是模型"有工具但坏了"。

**修法**：`requirements.txt` 工具依赖段补 `beautifulsoup4>=4.14,<5`，并把
「按需 import 会掩盖缺包」写进注释，防同类复发。

端到端验证（本地 conda 环境装好 bs4 后直接调用工具函数）：

```
fetch_url('https://example.com')  -> 返回 142 字符正文
web_search('LangGraph StateGraph') -> 返回 3 条 {title, link, snippet}
```

### 排查线索：模型答非所问其实是「工具坏了 + 预算用满」双重兜底

用户看到的答复里，模型既说了缺依赖，又说了调用次数用满。这两件事都真实发生，
只是顺序被误读成"重试两次就耗尽"。

## 问题二：工具调用次数上限统计的是整个会话，不是本轮

`src/graphs/nodes/llm_node.py` 第 177 行（修复前）：

```python
tool_rounds = sum(1 for m in history if isinstance(m, ToolMessage))
...
force_stop = tool_rounds >= MAX_TOOL_ROUNDS or _is_repeating()
```

`history` 来自 `_trim_history(list(state.get("messages", [])))`，
而 `state["messages"]` 是 checkpointer 按 `thread_id` 恢复的**整段会话记录**
（跨轮累积，窗口 30 条 ≈ 6 轮）。所以这个计数数的是**会话开聊以来的全部
ToolMessage**，不是本轮的。

后果：会话里只要累计执行过 8 次工具调用，**之后每一轮**都会被判定超限，
本轮一次工具都还没调就被剥掉工具、注入终止提示。

实测（模拟 6 轮、每轮 2 次调用）：

```
旧口径：sum(全会话 ToolMessage) = 12  -> force_stop = True   ← 第 5 轮起就废了
新口径：本轮起点之后计数        = 0   -> force_stop = False
```

用户侧的症状就是"聊着聊着工具就不能用了"，且**越聊越容易触发**——
第 4 轮需要 4 次累计、第 5 轮需要 5-6 次，与个人使用强度正相关，
不像 bug 更像"用得多了就这样"。

### 修法：按轮计数

```python
def _turn_anchor(history: list) -> int:
    """本轮起点：最后一条 HumanMessage 的索引 + 1；无 HumanMessage 返回 0。"""
    for i in range(len(history) - 1, -1, -1):
        if isinstance(history[i], HumanMessage):
            return i + 1
    return 0
```

`turn_start = _turn_anchor(history)` 之后：

```python
turn_tools   = [m for m in history[turn_start:] if isinstance(m, ToolMessage)]
pending_calls = sum(len(m.tool_calls) for m in history[turn_start:]
                    if isinstance(m, AIMessage) and m.tool_calls)
tool_rounds  = max(len(turn_tools), pending_calls)   # 取 max，不相加
```

两个设计点值得记：

1. **为什么取 `max` 而不是相加**：起点之后的 `ToolMessage` 是 tool_node 已执行
   的调用，起点之后的 `AIMessage.tool_calls` 是尚未执行的那批——本节点返回后
   tool_node 才执行，两者不会重叠。相加会把同一批调用算两遍，
   一次误计就足以让第 8 次真实调用被拒。宁可少算不能多算。

2. **`_is_repeating` 同步收窄到本轮**：原实现扫全会话调用序列，跨轮同名同参
   也会被判定死循环。但跨轮重复同一请求恰恰是**用户重试**的正常行为
   （比如"再抓一次"），不该拦。死循环只可能发生在单轮的工具循环内部。

语义澄清：轮次上限的本意是「单轮最多调几次」，防的是单轮内模型空转；
用户每发一条新消息都是一次全新的预算，跨轮累计不构成死循环风险——
因为跨轮一定需要人再发消息，人有耐心就是最有效的熔断器。

## 评测与验证

`src/ragas_test/eval_tool_truncation.py` 补 3 条回归用例：

| 用例 | 断言 |
|------|------|
| 新轮起点计数归零（6 轮各调 2 次后，新轮仍可调工具） | 本轮计数 == 0 |
| 轮内累计达上限（同轮 8 次调用 -> 8） | 本轮计数 == MAX_TOOL_ROUNDS |
| 按轮计数 vs 会话累计（防回退） | 本轮 0、全会话 ≥ 8、anchor 定位到末尾 |

第三条是**防回退锁**：同一份历史同时跑新旧两种口径，断言两者必须分道扬镳。
将来谁把计数改回全会话累计，这条立刻变红。

顺带修掉一条长期挂红的既有用例：E5「检索文档截断常量」期望值仍是
`MAX_RETRIEVAL_DOCS == 5`，而该常量在 H-20260919-07 P1 放宽时已改成 8，
评测集没跟着改 → 一直红。这属于「评测与实现脱钩」的典型，
和本轮修的两个问题同源：**改了一处、依赖它的另一处没跟着动**。

结果：

```
eval_tool_truncation   9/9   (100%，此前 7/9)
tests                  73 passed
eval_tool_safety       11/11 无回归
eval_tool_assembly     6/6   无回归
```

## 待确认 / 未做

- `beautifulsoup4` 版本约束用 `>=4.14,<5` 而非精确 pin：本地环境为 4.14.3，
  官方源当前最新 4.15.0。考虑到该包 API 稳定、且是纯解析用途，
  放宽下限比钉死更不容易在构建时卡住（钉死版本正是上次漏加依赖的另一面）。
  若项目要求全量精确 pin，需确认后统一。
- 修复尚未在线上验证：需要重新构建镜像（requirements 变更会触发 CI 构建）
  后，在真实会话里跑一次「发链接 → 抓取正文」链路。
  在此之前，线上仍表现为 `No module named 'bs4'`。
- 未做的事：本轮**没有**加「同轮内相同工具调用结果缓存」。
  一度考虑过（用户问「优化工具调用次数」容易被理解成这个），
  但缓存对本次事故无效——本轮的重复调用是**失败后的重试**，
  重试的意义就是重新执行；缓存只会把第一次的失败结果再喂回去。
  且实现上需要在 AIMessage 里伪造 tool_calls 才能让 `route_after_llm`
  仍然路由到 tool_node，复杂度不低。如需该能力应单开任务。

## 追加发现：E1 路由评测结果不可复现（2026-09-19 21:0x，未闭环）

**现象**：`routing_eval_report.json` 复跑得 **16/17（准确率 94.12%、检索召回
88.89%、fn=1）**，低于 `6ba37ad` 记录在案的 17/17。唯一失败项是
「为什么 RAG 检索后还需要重排序（Rerank）？」，被 `CLASSIFIER_PROMPT`
判为无需检索。

**关键判据**：`CLASSIFIER_PROMPT` 在两次结果之间**未改动**（`git log` 可查），
所以不能用「改了 prompt 导致变差」解释。候选变量有三个，本次无法区分：

1. 链路已变——H-07 重切 chunk（300/50→800/100）、`RERANK_FILTER_THRESHOLD`
   0.25→0.15，检索侧行为与首次评测时不同；
2. **采样波动**——已核对 `src/init.py:15`，`model` 通过 `init_chat_model`
   构造时**未传 `temperature`**，即默认采样；意图分类是单次 LLM 调用，
   17 条样本集下一条翻转就是 5.9 个百分点；
3. 该条 query 本身语义边界（"为什么还需要 Rerank"既可当概念题，
   也可当不需要外部知识的自问自答）。

另外，检索召回率（88.89%）与分类准确率（94.12%）**同值**，说明是同一条
用例同时挂掉两项指标，不是两处独立退化。

**结论**：**不能判定为"路由能力回退"**，也不能把 100% 当作稳定值引用。
当前 `routing_eval_report.json` 只是单次快照。

**已做的文档处置**（本轮同步，避免对外引用被推翻的绝对值）：

- 根 `README.md` E1 行改标"单次采样值，勿引用绝对值"，并列两次结果；
- `docs/AGENT_EVAL_MATRIX.md` E1 行补"字段值不可作稳定能力值"，
  新增「E1 复跑波动」小节，实测记录表 E1 行改为 94.12% + 归因说明；
- 项目详解 11 篇 §3.1.1 实测结果拆成两次跑并写清"别讲成回退"，
  待确认项新增第 9 条；3 条补模板类隐患提示（LLM 单次采样指标不应作能力值）；
- 索引 README 冲突节 + 待补充项各新增一条；
- 项目描述 Bullet：B2 行从 ✅ 改 ⚠️、「可新增写入项目描述」第 3 条作废、
  对外讲法段与 Q&A 段的口径全部改为"16/17 + 单次采样"。

**未做（遗留）**：没有真正重跑修 temperature 的版本。正确做法是把
`eval_routing.py` 改成固定 `temperature=0` 并对每条用例重复 3 次取众数
（或统计 N 轮翻转率），再落盘时附 `runs/flip_rate`。**在完成前，
任何引用 E1 数值的地方都只能报"17 条用例、最近一次 16/17"。**

**顺带暴露的口径问题**：这类"单次 LLM 调用 + 落盘 JSON"的脚本
（E1/E15 等）都不带重复采样与方差信息，落盘值天然不可复现。
E1 只是第一个把问题暴露出来的——E15 的 16/16 同样是单次结果，
只是本轮未复跑、未观察到波动。建议统一加 `--repeat N`。
