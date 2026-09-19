# persona 手选路由修复 + 首 token 提速 + 前端交互优化（H-20260919-09）

docs_sync: required

日期：2026-09-19
类别：agent / persona / frontend

## 背景

H-20260919-09 四项任务：
(A) 线上实测"选疯狂米塔却答成帽子米塔"——persona 手选短路未生效；
(B) 闲聊/自我介绍类短问题不跑检索子图（首 token 提速）；
(C) 前端聊天区右下角"跳到底部"悬浮按钮；
(D) 人格选择按钮旁加工具范围提示。

## 任务 A：persona 手选路由修复（根因）

### 根因

前端链路全通（`chat_schema` → `chat_router` → `chat_service._build_stream_config(persona_override=persona)` → `config.configurable.persona_override` → `persona_router_node` 手选短路 → `state.persona`），
但 `src/graphs/nodes/routes.py::route()` 用 `Send("retrieve_node"/"llm_node", payload)` 分发时，**payload 只含 `input_str + messages`，未携带 `persona`**。

LangGraph 的 `Send` 任务**不继承父 state** → `llm_node` 里 `state.get("persona")` 恒为 None → 兜底 `DEFAULT_PERSONA(cappie)`。
检索/非检索两条分支都受影响，与线上"选 crazy 答成帽子米塔"完全吻合。

### 改动

- `src/graphs/nodes/routes.py`：`Send` payload 补 `"persona": state.get("persona")`。

### 为什么此前没暴露

`cappie` 恰是兜底人格：手选 crazy/kind/manager 全部被归一成 cappie，与 H-03（默认人格叠语气层）场景表象一致，H-03 验证无法发现该 bug。

## 任务 B：闲聊/自我介绍快速短路（首 token 提速）

### 改动

- `src/graphs/nodes/classify_node.py`：新增 `_QUICK_NO_PATTERNS` 正则组 + `_quick_no_retrieval(text)`，命中直接返回 `needs_retrieval=False`，**跳过 LLM 分类调用**（省一跳，直接进 llm_node 流式输出）。

### 判定规则（刻意保守）

- 精确身份问句：`你是谁 / 你是哪个 / 你叫什么 / 你是什么`（可带标点/空白结尾）；
- 身份变体（包含式）：`你是谁`/`你是哪个`/`你叫什么`/`你是什么` 开头，句内含身份特征词 `米塔|角色|身份|名字|版本|扮演|介绍|来的`（覆盖"你是哪个版本的米塔""你是谁扮演的"）；
- 介绍类：`介绍一下你 / 介绍自己 / 自我介绍 / who are you` 等；
- 纯寒暄短句：`你好|您好|嗨|哈喽|hello|hi|在吗|在不在` **完整匹配**（带内容如"你好，帮我查X"不命中，走 LLM 分类）。

### 边界验证

25 个用例全部通过（命中 15 / 不命中 10）：
- 命中：你是谁 / 你是哪个米塔 / 你叫什么名字 / 介绍一下你自己 / 你好 / hi / 你是什么米塔 / 你是谁扮演的 / 你是哪个版本的米塔 等；
- 不命中（保守走 LLM）：你好帮我查X / 你是用什么框架实现的 / 你是什么时候创建的 / 米塔是什么游戏 / 介绍一下Mitta项目的架构 / 你是谁不重要（含后续口语）等。

## 任务 C：跳底悬浮按钮

- 模板：`messages-container` 内、`messages-wrapper` 后新增 `scroll-to-bottom-btn`（absolute 定位右下，不随内容滚动），`messages-container` 加 `@scroll="_onMessagesScroll"`；
- JS：`showScrollToBottom` ref（距底 > 200px 显示）、`jumpToBottom()`（平滑滚底 + 立即隐藏）；
- CSS：`.messages-container` 加 `position: relative`，新增 `.scroll-to-bottom-btn` 样式。

## 任务 D：人格工具范围提示

- `PERSONA_OPTIONS` 每项加 `hint`，与 `persona_constant.py` 的 `allowed_tools` 白名单一一对应：
  - auto 按问题自动分配；cappie 全量工具（None）；kind 日常问答·只读（无 git）；crazy 纯对话·无工具（[]）；manager 技术问答·Git 只读（kind 全部 + git_status/log/diff/branch）；
- `persona-option` 改为两行（label + hint 小字），`persona-btn` title 增加 hint；新增 `personaHint` computed。

## 验证

- `routes.py` / `classify_node.py`：`ast.parse` 通过；classify 25 边界用例全过；`pytest tests/` 73/73 通过；
- 前端 `app.js`：`node --check` 语法通过，补丁点全部落位（@scroll / 按钮模板 / hint 渲染 / 导出）；
- 未能本地起完整后端（RedisSearch/Chroma/SiliconFlow + 真实 LLM）实测线上回答风格，需 push 后现网验证。

## 影响面

- 手选人格（crazy/kind/manager）现在真正生效，回答风格/工具白名单随选择变化 → README/项目详解中"人格手选""工具白名单"描述需文档 Agent 核对同步；
- 闲聊/自我介绍类首 token 提速（跳过 LLM 分类调用）；
- 前端新增跳底按钮与人格工具范围提示（UI 行为变化）。
