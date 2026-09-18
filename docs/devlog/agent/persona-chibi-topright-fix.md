# chibi 气泡移右上角 + crazy 手选不生效诊断（H-20260919-04）

docs_sync: none

日期：2026-09-19
类别：agent / persona+ui

## 任务A：手选 crazy 不生效——诊断结论（代码侧无 bug）

**现象**：线上 www.mittaai.xyz 选"疯狂"人格发消息，回答仍是原 system_prompt 温柔风，crazy 偏执甜妹风格未生效。

**排查链路（静态走查，全部正确）**：
1. 前端 `app.js:390` `if (persona !== 'auto') body.persona = persona` ✅；调用处 `:2631` 实参传 `personaMode.value` ✅
2. `chat_schema.py:13` `ChatRequest.persona: Optional[str]` 字段已定义（Pydantic 不会静默丢弃）✅
3. `chat_router.py:86` `persona=request_body.persona` → service ✅
4. `chat_service.py:629` `_build_stream_config(..., persona_override=persona)` → `:508` `config.configurable.persona_override` ✅
5. `chat_service.py:642` `graph.stream({"input_str":...}, config=config)` config 透传 ✅
6. `persona_router_node.py:40-43` 短路 `hand = config.configurable.persona_override`，命中返回 `{"persona": hand}`，日志 `[persona] 手选短路 key=...` ✅
7. `state.py:29` persona LastValue 覆盖语义；`main_graph.py:161` persona_router 为 START 后首节点 ✅
8. `llm_node.py:116-120` `persona_key = state.persona or DEFAULT_PERSONA`，非默认拼 `PROMPT_CRAZY` ✅

**最小复现验证**：直接调 `persona_router_node(state, config, None)`，`config.configurable.persona_override="crazy"` → 日志打印 `[persona] 手选短路 key=crazy`，返回 `{"persona": "crazy"}`，**PASS**。

**根因结论**：代码侧链路完整、本地复现通过；persona 代码已在 main（`73a50ea` 多人格 v1 + `e809b1a` 前端人格 tab）。**线上不生效 = 线上部署版本旧**——服务器跑的后端镜像/容器早于 `73a50ea`，未拉取 persona 逻辑代码。解决方式：重新推送并触发 CI/CD 部署（scp/rsync 拉新代码 + 重启后端容器），无需改代码。

## 任务B：chibi 气泡移右上角（纯 CSS）

- `style.css` `.chibi-dock`：`right:24px; bottom:96px` → `right:24px; top:96px`（右上角，避开顶部控制栏，不遮挡输入区与主对话流）
- `.chibi-bubble` 尖角：`border-radius` 从右下尖角 `14px 14px 4px 14px` 改为左下尖角 `14px 14px 14px 4px`（气泡在右上，尖角指向对话流方向）
- mini 头像：未加（handoff 授权"视效果决定"；当前纯文字气泡位置正确即可达标，避免额外引入素材依赖）

## 验证

- 任务A：最小复现脚本（已删）短路 PASS；日志正常打印
- 任务B：CSS 落盘回读确认；git diff 仅 chibi 两处（dock 定位 + 尖角方向）
