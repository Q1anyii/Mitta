# Agent 基础与范式

> 基于 AgentProject 项目总结，涵盖 Agent 的定义、核心组件、主流范式（ReAct / Plan-and-Execute / Reflexion）、
> Agent 与 Workflow / Chain / RAG 的区别，以及 Mitta 项目五节点 Agent 主图的实际实现。

## 一、什么是 Agent

Agent（智能体）是 **LLM 驱动的自主系统**：模型不仅生成文本，还能根据目标**规划动作、调用工具、观察结果、迭代修正**，直到完成任务。与"一问一答"的聊天机器人不同，Agent 的核心特征是**闭环决策**：

```
目标 → 观察 → 规划 → 行动（工具调用）→ 观察结果 → 再规划 → ... → 输出
```

一个完整的 Agent 至少包含四个组件：

| 组件 | 职责 | Mitta 对应实现 |
|------|------|----------------|
| LLM | 推理与决策中枢 | DeepSeek / 可配置多模型 |
| 工具（Tools） | 与外部世界交互的能力 | MCP 工具集（41 个，支持免改代码接入） |
| 记忆（Memory） | 保存上下文与长期知识 | 双层记忆（短期会话 + 长期用户画像） |
| 规划器（Planner） | 拆解任务、决定下一步 | LangGraph 主图节点编排 + 工具装配 |

## 二、Agent 与 Workflow / Chain / RAG 的区别

| 维度 | Chain / Workflow | Agent | RAG |
|------|------------------|-------|-----|
| 决策方式 | 预定义流程，节点顺序固定 | 模型自主决定下一步 | 检索增强生成，流程固定 |
| 灵活性 | 低：流程写死 | 高：可动态分支、循环、调用工具 | 中：检索→生成两段 |
| 工具调用 | 通常无 | 核心能力 | 通常无 |
| 适用场景 | 稳定流程（如 ETL） | 开放任务（如自主研究） | 知识问答 |
| 失败处理 | 流程中断即失败 | 可观察结果、换路重试 | 检索空则靠模型兜底 |

**判断标准**：如果"下一步做什么"由 LLM 根据上下文决定，就是 Agent；如果由代码写死，就是 Workflow。Mitta 在检索类问答走固定 RAG 子图（Workflow 性质），在工具/记忆场景由 LLM 决策（Agent 性质），属于 **混合式**。

## 三、主流 Agent 范式

### 3.1 ReAct（Reason + Act）

让模型交替输出"思考（Thought）→ 行动（Action）→ 观察（Observation）"，把推理和行动显式写进循环：

```
Thought: 用户问当前时间，需要调用工具
Action: get_current_time()
Observation: 2026-09-21 10:30:00
Thought: 拿到结果，可以直接回答
Answer: 现在是 2026-09-21 10:30
```

**要点**：
- 显式推理轨迹让模型在复杂任务上更稳，也便于排查
- 每轮工具调用后必须把结果（Observation）喂回模型，形成闭环
- 缺点：多轮串行，延迟高、token 消耗大

### 3.2 Plan-and-Execute（先规划后执行）

先让模型产出完整计划（Plan），再逐步执行（Execute），每步执行后可修正计划：

```
Plan:
  1. 解析用户问题意图
  2. 决定走检索还是闲聊（路由）
  3. 检索 → 重排 → 生成
```

**与 ReAct 的区别**：ReAct 是"边想边做"（interleaved），Plan-and-Execute 是"先想后做"（sequential）。前者更灵活但更贵，后者更快但计划偏差难纠正。

### 3.3 Reflexion（反思修正）

在失败或反馈后让模型**复盘错误**，把反思写入记忆，下次避免同样错误。Mitta 的工具失败熔断即是类似思想：同一工具连续失败 2 次从本轮摘除，并写入"该工具暂时不可用"的提示，避免模型反复踩坑。

## 四、Agent 生命周期

```
接收输入 → 意图识别 → 路由决策 → 执行（检索/工具/记忆） → 结果评估 → 输出/反思
```

关键节点：
1. **意图识别**：判断是检索问答、闲聊、还是需要工具的动作请求
2. **路由**：决定走哪条链路（Mitta 统一路由：意图维度 + 人格维度双判定）
3. **执行**：检索、调用工具、读写记忆
4. **评估**：结果是否满足要求，失败则重试或降级
5. **输出**：结构化/流式返回，附引用溯源

## 五、Mitta 五节点 Agent 主图

Mitta 用 LangGraph 的 `StateGraph` 实现 Agent 主图，五个节点通过条件边编排：

```python
builder = StateGraph(state_schema=RAGState)
builder.add_node("intent_router", route_intent)   # 意图路由 + 人格分类
builder.add_node("retrieval", retrieval_subgraph) # RAG 检索子图（改写/召回/重排）
builder.add_node("generator", generate_answer)    # 生成节点（含工具装配）
builder.add_node("memory", update_memory)         # 记忆读写（fire-and-forget）
builder.add_node("output", stream_output)         # 流式输出
```

- **路由**：`intent_router` 一次判定"是否检索"与"人格维度"，检索/闲聊动态分流（统一评测 37 条：意图 91%~94%、人格 32/32）
- **缓存**：命中时跳过检索子图（改写/召回/重排），生成照常执行（E6-B 命中率 61%~97%）
- **记忆**：长期记忆更新与生成解耦，正文流完即结束，避免记忆拖慢首 token
- **失败兜底**：检索失败/工具熔断均有降级路径，不让单点失败阻塞主对话

## 六、FAQ

**Q1：什么时候该用 Agent，什么时候用 Workflow？**
稳定、可穷举的流程用 Workflow（可控、便宜、可测试）；开放、需要决策和工具的任务用 Agent。先用 Workflow 把 80% 的常见路径固定下来，再用 Agent 处理边界情况，是工程上更稳的做法。

**Q2：Agent 一定比 Workflow 好吗？**
不一定。Agent 延迟高、成本高、不可控。Mitta 的检索问答路径实质是固定子图（Workflow），只有需要工具/记忆时模型才自主决策——混合架构兼顾质量与成本。

**Q3：ReAct 和 Function Calling 什么关系？**
ReAct 是决策范式（思考→行动→观察循环），Function Calling 是模型与工具的**接口机制**（模型输出结构化工具调用参数）。二者互补：ReAct 决定"要不要调、调哪个"，Function Calling 决定"怎么调、传什么参数"。
