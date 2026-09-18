# cappie 默认人格叠加 PROMPT_CAPPIE 语气层（H-20260919-03）

docs_sync: none

日期：2026-09-19
类别：agent / persona

## 背景

现网实测：技术类问题（如"前端如何用 Fetch + ReadableStream 接收 SSE"）被 persona_router 自动分类为 cappie（默认人格），而 `llm_node.py` 原门控为"cappie=默认=不拼人格 prompt"，回答完全走原 system_prompt，干巴巴无米塔味。

## 改动

- `src/graphs/nodes/llm_node.py`（4.1 人格注入段）：删除 `if not is_default_persona:` 门控与 `is_default_persona` 判断，改为**所有人格（含默认 cappie）无条件追加** `persona['prompt']` 到 system prompt。顺序不变：原 system_prompt / 用户自定义 / 长期记忆在前，人格 prompt 在后（语气层不推翻事实层与隐私铁律）。
- 工具白名单分支不动：cappie `allowed_tools=None` 全量；kind/crazy/manager 按各自白名单收缩。
- `PROMPT_CAPPIE` 内容不动（已含"做完邀功搞定啦""语气词可用呀哦啦，颜文字少量点缀""严禁描写表情动作"，自带克制）。

## 验证

- 语法 `ast.parse` 通过；`llm_node` 模块 import 链路正常。
- 组装逻辑模拟断言：cappie / kind / crazy / manager 四人格 + 默认(None→cappie) 全部 `prompt_appended=True`；cappie `allowed_tools=None`、其余白名单与改动前逐项一致。
- 边界说明：未能本地起完整后端（RedisSearch/Chroma/SiliconFlow + 真实 LLM）实测真实回答的语气浓度；叠加后技术回答是否过度活泼留待现网验证，若过跳按 handoff 兜底在 PROMPT_CAPPIE 末尾追加克制度（本改动未加）。

## 影响面

- 所有默认回答多一层人设语气；kind/crazy/manager 行为不变。
- 回答风格变化 → 需文档 Agent 同步（项目详解/README 中关于"默认人格回答风格"的描述）。

## 回归修复（同日）

并行会话提交 `7bb4e2d` 删除门控时**一并删除了 `is_default_persona` 变量定义**，但 `llm_node.py:194` 裸模型分支 `elif not is_default_persona and allowed == []:`（同函数作用域）仍引用该变量 → 在"无可用工具/工具达上限"路径下会 NameError。修复：恢复 `is_default_persona = persona_key == DEFAULT_PERSONA` 定义（仅保留变量，无条件叠加门控不回退）。静态验证：定义存在 / 无条件叠加语句存在 / `if not is_default_persona:` 门控已删（elif 保留）/ `ast.parse` 通过。

## 遗留

- 现网验证叠加后语气浓度；必要时加兜底克制度。
