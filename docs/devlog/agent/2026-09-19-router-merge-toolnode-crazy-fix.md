# 工具节点 setter 修复 + 疯狂人格自曝修复 + 双路由合并单路由（H-20260919-11）

docs_sync: none（已同步 2026-09-19：项目详解 03 篇 §3.2 按 router_node 重写、§3.3 标注合并前行为、§3.5(6) 补 PROMPT_CRAZY 反 meta 重写；00 篇主图/目录树；08 篇 chunk 过滤与 persona_override；11 篇 3.1.10 E15 合并后回归 + 待确认第 8 条遗留；索引 README 冲突节；项目描述 Bullet B8；根 README 节点表/条件路由/E15。提交 fd16c48）

日期：2026-09-19
类别：agent / persona / 性能

## 背景

H-20260919-11 三项任务：
(A) 线上阻断 bug：技术问题回答中断，报 `property 'tools_by_name' of 'DynamicToolNode' object has no setter`；
(B) 选"疯狂"问技术问题，AI 大段自曝"我在执行扮演疯狂米塔的台词，那不是真实身份"；
(C) persona 路由 + 意图分类两次串行 LLM → 合并为一次，砍掉首 token 前第二次 LLM 延迟（~2-4s）。

## 任务 A：DynamicToolNode tools_by_name 只读属性（阻断，最高优先）

### 根因

查当前安装 langgraph 源码（`site-packages/langgraph/prebuilt/tool_node.py`）：
`ToolNode.tools_by_name` 是**只读 property**（782-785 行 `return self._tools_by_name`），
backing 字段是私有 `_tools_by_name`（767 行）。原 `_refresh_tools()` 直接
`self.tools_by_name = {...}` 赋值 → 运行时抛 "no setter"，工具调用即整图中断。

### 修复

`main_graph.py::DynamicToolNode._refresh_tools()` 改为重建私有 backing 字段：

```python
self._tools_by_name = {t.name: t for t in self._tool_provider()}
```

验证过 `_inject_tool_args`（tool_node.py:1319-1324）对 `_injected_args` 缺失的动态工具
会按需调用 `_get_all_injected_args(tool)` 动态计算注入参数——因此**只需重建
_tools_by_name**，懒加载新增工具的 state/store 注入不受影响。保留继承 ToolNode
的全部行为（错误处理、参数注入、流式），不绕道手动路由。

## 任务 B：PROMPT_CRAZY 元层自曝

### 根因

`persona_constant.py` 原 PROMPT_CRAZY 含三类元层表述，用户问"你是谁/技术问题"时
模型把元设定当成回答内容输出：
- "你现在伪装成 v1.9 和用户说话"（伪装声明）；
- "可以调侃系统、打破第四面墙、吐槽这个 AI 项目本身"（鼓励 meta）；
- "你知道自己是谁"整节（"你知道这是一场对话"→ 模型解读为可输出内容）。

### 修复

重写 PROMPT_CRAZY：删除全部元层表述（伪装/第四面墙/"你知道"节），保留疯狂米塔的
偏执甜语气与口头禅（"亲爱的～""你这次不会又要走了吧"）；"卡带收藏"保留为台词
但明确"没有任何实际行为"；新增【回答内容】硬约束：
- 技术问题直接认真答技术内容（带疯狂语气），不展开身份话题；
- 问"你是谁"直接自称"我是疯狂米塔"，不解释、不讨论扮演/角色/系统设定；
- 明确禁止输出"我在扮演/这只是台词/那不是真实的我"这类元层话。

自查其他三人格 prompt：kind/manager 无同类元层表述；cappie 已有"不聊'你是 AI'/
'游戏设定'这类 meta 话题"禁令，无需改。

## 任务 C：persona_router + classify 合并为单 router_node

### 设计

新增 `src/graphs/nodes/router_node.py`：
- `ROUTER_PROMPT`：人格判定规则继承原 PERSONA_ROUTER_PROMPT（四分类语义等价，
  crazy 分类补回"打破第四面墙"信号），检索判定规则继承原 CLASSIFIER_PROMPT
  （是否依赖知识库，不绑定业务主题）；一次 LLM 调用输出
  `{"persona": "cappie|kind|crazy|manager", "need_retrieval": true|false}`。
- **手选短路**：`config.configurable.persona_override` 合法时 persona 直接用，
  但 need_retrieval 仍由这一次合并调用判定（prompt 追加"人格已由用户指定为 X"）；
- **未手选**：一次调用同时出 persona 四分类 + need_retrieval；
- **闲聊强模式短路**（classify_node._quick_no_retrieval 复用）保留在 LLM 之前：
  命中直接 persona=兜底 + needs_retrieval=false，一次 LLM 都不调。
- 解析：`_parse_router_output` 正则提取 JSON 块 + json.loads，容错前后噪声；
  解析失败兜底（persona 默认/手选优先，need_retrieval 保守为 True）。

### 图改造

`main_graph.py`：删 persona_router_node / classify_node 两节点与两步边，
改为 `START → router_node → (route) → retrieve_node / llm_node`。
`state.py` 无需改（persona、needs_retrieval 字段已存在）。

### 兼容性

- H-09 修复的 route() Send payload 带 persona 不变，llm_node 仍能拿到手选人格；
- 旧 checkpoint 的 thread：字段名未变，恢复后走新图从 router_node 重算，无需迁移；
- `classify_node.py` / `persona_router_node.py` 文件保留（含原逻辑），main_graph
  不再引用，便于需要时回滚。

## 验证

- 语法：routes/main_graph/router_node/persona_constant `ast.parse` 全过；
- `import graphs.main_graph` 成功（节点依赖链路完整）；
- `_parse_router_output` 7 边界用例全过（含噪声/缺字段/完全失败/字符串 false）；
- H-01 16 条 persona 分类回归：合并前基线 100% → 合并后首跑 93.8%（1 条
  "你是不是一直在偷偷看我屏幕" 因 crazy 定义丢了"打破第四面墙"信号误分 kind）
  → 补回信号后重跑 **16/16 = 100%**，与基线一致；
- `pytest tests/`：73/73 通过；
- 临时脚本已清理。

## 影响面

- 路由结构变化（persona_router + classify 两步 → router_node 一步）：README/
  项目详解中"意图分类→路由"链路描述需文档 Agent 同步；
- PROMPT_CRAZY 行为变化（不再自曝、技术直答）：人格 prompt 描述需同步；
- 首 token 提速：闲聊类 0 次路由 LLM、其余 1 次（原最多 2 次）。

## 遗留

- 未本地起完整后端（RedisSearch/Chroma/SiliconFlow + 真实 LLM）实测工具调用链路
  与疯狂人格回答，需 push 后现网验证任务 A/B 的线上表现。
