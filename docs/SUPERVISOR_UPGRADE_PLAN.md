# Mitta Supervisor 多 Agent 协作升级方案（C 阶段 / 未来）

> 前置：当前版本（v1-routing）是"意图路由 + 人格化 ReAct"，见 `PERSONA_MULTIAGENT_PLAN.v1-routing.bak.md`。
> 本文档描述**未来什么时候、怎么、值不值得**升级成真 Supervisor 多 Agent 协作。
>
> **触发条件**：以下任一为真才启动本方案，否则保持 v1：
> 1. 真实用户反馈"一个问题需要多个人格接力"（例如：短发分析完自动让帽子去改，用户不想切）
> 2. 演示需要"真多 Agent"亮点
> 3. 线上有闲算力，可以承担 2-3 倍 token 成本
>
> **不启动的理由也写在这**：v1 路由版在当前场景已经够用，盲目升级会把简单问题复杂化。

---

## 一、真 Supervisor 和 v1 路由版的本质区别

| | v1 路由版（当前） | C 阶段 Supervisor |
|---|---|---|
| 决策方 | 无状态分类器，一次输出标签 | LLM agent，持有对话控制权 |
| 决策时机 | 每轮开头一次 | 每轮可能多次：sub-agent 干完回来，supervisor 决定下一步 |
| sub-agent 调用 | 只调一个 | 可调多个、可串行、可跳过 |
| 控制权 | 分类完就交出 | supervisor 始终持有 |
| 防死循环 | 不需要（只一跳） | 需要循环上限 + 重复检测 |
| token 成本 | +1 次分类调用 | +2~4 次 supervisor 推理 |
| 延迟 | +0.1s | +0.5~2s |

**v1 像分诊台**：护士看一眼病情，直接送对应科室。
**C 像主任医师**：先看一个科室，不够再叫下一个，全程主任说了算。

---

## 二、目标架构

```
                    ┌─────────────────────┐
                    │  Supervisor Agent   │
                    │  (持有控制权)         │
                    │  工具:              │
                    │   - call_cappie     │
                    │   - call_kind        │
                    │   - call_crazy       │
                    │   - call_manager    │
                    │   - answer_directly │
                    └─────────┬───────────┘
                              │
        ┌──────────┬───────────┼───────────┬──────────┐
        ▼          ▼           ▼           ▼          ▼
   cappie     kind         crazy         manager     (直接答)
  (subgraph) (subgraph)   (subgraph)    (subgraph)
   全工具     只读陪伴     meta          只读技术
```

关键点：
- **Supervisor 自己也是个 ReAct agent**，但它的"工具"是调用 sub-agent
- **sub-agent 就是 v1 里的四个人格 ReAct 循环**，包装成 LangGraph subgraph
- sub-agent 干完活**返回 supervisor**，supervisor 判断：
  - 任务完成 → 调 `answer_directly` 给用户最终回答
  - 还要别的人格 → 调下一个 `call_xxx`
  - 自己能答 → 直接答
- **不做**：sub-agent 之间直接 hand-off（网状），那是 Swarm，最容易死循环

---

## 三、从 v1 代码怎么升级

### 3.1 把每个人格包装成 subgraph

当前 `llm_node` 是一个节点，升级时把它包装成一个子图：

```
persona_subgraph(persona_key) =
    START -> retrieve_node(可选) -> llm_node(用 persona_key 的 prompt+白名单)
         -> route_after_llm -> [tool_node -> llm_node 循环] -> END
```

四个人格 = 四个 subgraph，输入是 `OverAllState`，输出是"sub-agent 的回答消息"。
代码上几乎不用重写，就是把 `build_main_graph` 里的图抽成 `build_persona_subgraph(persona_key)`。

### 3.2 Supervisor 节点

新建 `graphs/nodes/supervisor_node.py`：

```python
SUPERVISOR_PROMPT = """你是米塔们的总管。用户说了一句话，你判断怎么调度：

可用工具：
- call_cappie：要执行操作/文件/Git/写代码
- call_kind：情绪陪伴/闲聊
- call_crazy：玩梗/meta/聊 AI 本质
- call_manager：技术分析/bug 定位/代码 review（只读，不改）
- answer_directly：你能直接答，不需要叫人

规则：
1. 一个任务通常只调一个 sub-agent
2. 如果 manager 分析完发现要改代码，你可以再 call_cappie 执行修改
3. 不要连续调同一个 sub-agent 两次（除非明确需要）
4. 已经调用过的 sub-agent 结果会附在对话里
"""
```

Supervisor 绑定 5 个工具（4 个 `call_xxx` + `answer_directly`），每个 `call_xxx` 工具内部 `Command` 跳转到对应 subgraph。

LangGraph 用 `Command(goto="cappie_subgraph")` 实现 sub-agent 跳转。

### 3.3 图改造

```
START -> supervisor_node ->
    [conditional based on tool call]
        -> cappie_subgraph  -> supervisor_node
        -> kind_subgraph    -> supervisor_node
        -> crazy_subgraph   -> supervisor_node
        -> manager_subgraph -> supervisor_node
        -> END (supervisor 直接答完)
```

关键：**subgraph 执行完回到 supervisor**，不是直接 END。supervisor 看到 subgraph 的输出，决定下一步。

### 3.4 循环上限（防死循环，必须做）

```python
MAX_SUPERVISOR_HOPS = 3  # 最多调 3 个 sub-agent
# 在 state 里维护 supervisor_hops 计数器
# 超过上限强制 supervisor answer_directly，不再调 sub-agent
```

参考你现有 `MAX_TOOL_ROUNDS = 8` 的做法，直接搬过来。

---

## 四、关键设计点

### 4.1 什么时候让 supervisor 直接答
不是每个问题都要调 sub-agent。Supervisor 判断：
- "谢谢" / "好的" / "嗯" → `answer_directly` 自己答
- 闲聊但不需要陪伴 → 自己答
- 真需要工具/专业能力 → 调 sub-agent

这样省调用。

### 4.2 Sub-agent 之间怎么传递上下文
subgraph 输入是 `OverAllState`（含 messages）。supervisor 调 `call_cappie` 时，subgraph 看到的 messages 包含之前 manager 分析的结论，所以帽子米塔能接着干。**不需要额外传 context**，messages 共享就行。

### 4.3 用户手动选人格怎么办
保留 v1 的 `persona` 字段：
- 用户选了 → 跳过 supervisor，直接走对应 subgraph（和 v1 一样）
- 用户选"自动" → 走 supervisor

这样 v1 的手动模式不浪费。

### 4.4 评估和监控
加三个指标：
- `supervisor_hops`：每轮平均调几个 sub-agent（健康值 1~2）
- `supervisor_token_cost`：supervisor 自己的 token 占比
- `subagent_chain_rate`：需要多 sub-agent 接力的比例（如果 <10%，说明这个升级 ROI 低，回 v1）

---

## 五、必须避开的坑

1. **防甩锅**：supervisor prompt 里写死"你是总管，你要决定谁来干，不要问用户'你想找谁'"
2. **防循环**：MAX_SUPERVISOR_HOPS = 3，连续调同一个 sub-agent 两次直接强制结束
3. **不做网状**：sub-agent 之间不能直接 hand-off，必须经过 supervisor。网状 Swarm 死得最快
4. **成本监控**：上线后先跑一周，如果 supervisor 自己的 token 占比超过 30%，说明 supervisor prompt 太长或者分类太纠结，要优化
5. **降级路径**：保留 v1 路由版作为 fallback，环境变量 `SUPERVISOR_ENABLED=false` 一键切回

---

## 六、工作量估计

| 模块 | 工作量 |
|------|--------|
| 把四个人格包装成 subgraph | 0.5 天 |
| 写 supervisor_node + 5 个 call 工具 | 0.5 天 |
| 图改造 + 循环上限 | 0.5 天 |
| 调试 hand-off 链路（这步最花时间） | 1-2 天 |
| 总计 | **3-4 天** |

比 v1（周末 2 天）重一倍。所以触发条件没满足前别启动。

---

## 七、对外口径（真升级后怎么说）

> 在 v1 意图路由版基础上，我升级成了 Supervisor 模式：supervisor agent 持有对话控制权，
> 可调多个 sub-agent 串行执行（例如短发米塔分析 bug 后自动交棒给帽子米塔修改）。
> 我做了循环上限和重复检测防死循环，保留了 v1 路由版作为低成本 fallback。
> 上线后通过 `supervisor_hops` 和 `subagent_chain_rate` 监控真实调用链，
> 发现多 sub-agent 接力的请求不到 10%，大部分场景单人格就够——这个数据反过来验证了
> "不是所有问题都需要多 Agent"的判断。

最后一句是杀手锏：**你有数据证明大多数场景不需要多 Agent**，这比"我做了多 Agent"成熟得多。

---

## 八、现在该做什么

**什么都不做**。
1. 先把 v1 路由版（当前方案）做完上线
2. 跑一个月，收集真实数据：多少请求需要多人格接力
3. 如果数据 ≥15%，再启动本方案
4. 如果 <10%，说明 v1 就是对的，把这份文档当"未来选项"留着
