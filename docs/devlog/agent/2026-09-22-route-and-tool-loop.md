---
docs_sync: required
---

# Agent 路由与工具循环重构（Send 取消 / llm_node 拆分 / 硬熔断）

## 背景

外部审计指出三个问题：
1. `routes.py` 用 `list[Send]` 做路由，但 `Send` 的 payload 不继承父 state，曾踩过 persona 丢失事故（H-20260919-09）；
2. `llm_node.py` 单文件过长，工具循环防护逻辑和编排逻辑混在一起；
3. 工具循环的 `force_stop` 只是往 prompt 里注入「别再调工具」，没剥离 `tool_calls`——模型不听话就会空转烧 token 甚至 `GraphRecursionError`。

## 改动

### 1. 取消 Send，改 if/else 条件分支（352e8c6）

`routes.py` 的 `route()` 从返回 `list[Send]` 改为普通字符串分支：
- 需要检索 → `"retrieve_node"`
- 否则 → `"llm_node"`

`main_graph.py` 对应改 `add_conditional_edges` 注册两个目标节点。
原因：当前是单路径二选一（不是 map-reduce 扇出），Send 的 payload 不继承父 state 反而带来隐患，if/else 更直白。
注释保留：「后续升级 Supervisor 多 Agent 时可再用 Send」。

### 2. llm_node 拆分（f536302 → 8491cbf）

- 第一轮：把工具循环防护纯函数从 `llm_node.py` 抽到 `nodes/llm_circuit.py`；
- 第二轮（目录整理）：`llm_circuit.py` 移到 `graphs/utils/`（它是纯函数不是节点），`routes.py` 上移到 `graphs/` 顶层。

拆分后主节点只留编排：调用 LLM → 解析 tool_calls → 循环/熔断判断 → 返回 state。

### 3. 工具循环硬熔断（b187cf3）

`force_stop` 从「软提示」升级为「代码层硬约束」：
- 达到次数上限或检测到重复调用时，**直接剥空 `AIMessage.tool_calls`**，不靠提示词让模型自觉；
- `route_after_llm` 看到空 tool_calls 就不会路由回 `tool_node`，必然走向结束。

设计原则：**不信任 LLM 输出，执行点放在状态机层而非模型层**。

## 验证

- 73 个单测全过；
- 线上回归 agent-regression 作为部署门禁（见 ci 篇），改完后跑通。
