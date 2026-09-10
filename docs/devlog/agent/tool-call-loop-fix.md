# 工具调用死循环修复（随便调用一个工具 → 无限循环）

## 遇到的问题

用户发送"随便调用一个可用的工具"后，Agent 陷入工具调用死循环：模型反复调用
`create_directory` / `list_directory` / `open_nodes` / `search_files` 等工具，
每轮都重新生成新的 tool_calls，永远不收敛，直到前端中断 / 请求超时。

线上复现（服务器 121.199.38.43，镜像为修复前版本）：SSE 流 90 秒内持续输出
工具调用，`list_directory`×9、`search_files`×11、`open_nodes`×7、
`create_directory`×9、`create_entities`×5……流尾仍在调用 `open_nodes`，无
`[DONE]` 结束标记，被 curl 90s 超时强制断开。

## 排查过程

1. **读图结构**（`src/graphs/main_graph.py`）：`llm_node → route_after_llm →
   tool_node → llm_node` 循环。终止条件是 llm_node 不再生成 tool_calls，
   该循环没有显式轮次上限（LangGraph 默认 recursion limit 25 只是兜底报错）。

2. **读 llm_node**（`src/graphs/nodes/llm_node.py`）发现根因：
   ```python
   return {"messages": [HumanMessage(content=input_str), ai_reply], ...}
   ```
   **每一轮工具循环都重新向 state.messages 追加一条 `HumanMessage(input_str)`**。
   第 2 轮起，模型看到的序列变成：
   `Human(问题) → AI(tool_calls) → Tool(结果) → Human(问题) → AI(tool_calls)`
   ——用户问题被重复强调，且工具结果夹在中间、末尾总是"用户让我调用工具"，
   模型每轮都被重新触发调用工具，形成正反馈死循环。

3. **对照正常 ReAct 语义**：用户消息应只出现一次，之后是
   `AI(tool_calls) → Tool → AI(tool_calls) → Tool → AI(最终回答)` 的收敛序列。

## 解决方案

改动集中在 `src/graphs/nodes/llm_node.py`，两处防护：

1. **工具循环轮次去重用户消息**
   计算 `in_tool_loop = history 末尾是 ToolMessage`：处于工具循环中时，
   本轮只返回 `[ai_reply]`，不再重复插入 `HumanMessage(input_str)`。
   首轮（末尾是 HumanMessage）仍正常追加用户消息。

2. **工具轮次上限硬性兜底**
   统计历史中 `ToolMessage` 数量即已执行工具轮次：
   - `tool_rounds >= MAX_TOOL_ROUNDS`（默认 4）时不再 `bind_tools`，
     改用裸模型并注入 SystemMessage"工具调用次数已达上限，请直接回答"，
     强制生成无 tool_calls 的最终回答 → 路由进入 memory_node → END。
   - 即使模型行为再异常，单轮请求最多执行 4 次工具调用后必然收敛。

## 验证

- `py_compile` 语法通过。
- 逻辑推演三种场景：
  - 开放指令"随便调用一个可用的工具"：最多 4 轮工具调用后强制收尾 ✓
  - 普通问答（无需工具）：首轮直接回答，不受影响 ✓
  - 正常多工具任务（1-3 轮调用后自行停止）：低于上限，行为不变 ✓
  - 刷新恢复流（messages 末尾为 ToolMessage）：`in_tool_loop=True` 不追加
    Human，模型继续生成，与"断连不中断生成"逻辑兼容 ✓
- 线上旧镜像已复现该 bug，修复提交推送后需 CI 重建镜像再次验证
  （见 devlog/deploy 下 CI 部署链相关记录）。

## 附带收益

- 工具循环轮不再累积重复用户消息，messages 增长减半，token 消耗下降；
- 解决了此前"内部 ReAct 每轮全量传会话消息、token 浪费"的隐患（配合
  `_trim_history` 滑动窗口）。
