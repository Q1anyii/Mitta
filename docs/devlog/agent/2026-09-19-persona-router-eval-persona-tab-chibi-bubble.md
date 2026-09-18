docs_sync: required

# 2026-09-19 方案A验收收尾：persona_router 实测 + 前端人格tab + chibi气泡 + 白名单校准

## 背景
H-20260919-01：方案A多人格上线后三个未验收项收尾。

## 改动

### ① persona_router 四分类离线实测（新增）
- 新增 `src/ragas_test/persona_router_eval.py`：绕过手选短路，直接复用 `PERSONA_ROUTER_PROMPT` + 生产同款 deepseek-v4-flash 模型，对四类各 4 条典型 query 跑分类。
- 结果：**16/16 = 100%**，混淆矩阵对角线全满，无错分 case；报告落盘 `src/ragas_test/persona_router_eval_report.json`。
- 诚实标注：样本集 16 条均为典型表达，边界模糊用例（如"顺便帮我看下这段代码有没有问题"）未覆盖，100% 是乐观基线而非上线真值。

### ② 前端人格 tab + chibi 浮动气泡（新增）
- `apiChat` 新增 `persona` / `onChibi` 参数；body 仅当 persona≠'auto' 时透传（auto=后端 router 自动分发，不传）。
- SSE 循环新增 `chunk.chibi` 分支：吐槽文本走独立回调，**不进主消息流**。
- 输入区新增人格选择按钮（自动/帽子/善良/疯狂/短发），localStorage 持久化 `personaMode`；选中具体人格 = 后端 `persona_override` 短路，不调分类模型。
- chibi 气泡：右侧 `fixed` 浮动层（`.chibi-dock`），30s 自动收起 + 手动关闭，不影响主对话布局。

### ③ kind/manager 工具白名单按运行时真实工具名校准
- 新增 `scripts/list_runtime_tools.py`：复现 main.py 装配链（first_party MCP 连接 → safety_filter），打印运行时真实 `t.name`（本地实测 26 个 + mitta-tools 13 个注册名）。
- kind 白名单从原 11 个（含不存在于运行时的臆测名）扩到 23 个纯只读工具：文件读/目录列/时间/只读 SQL/只读图谱节点/web_search/fetch_url；
- manager 白名单 = kind 全部 + git 只读 4 个（git_status/log/diff/branch）；git_add/commit/checkout 写操作仍不给。
- 之前白名单里 `read_local_file/search_project_files/get_project_info` 经核对确实在 mitta-tools 注册名中，保留；新增遗漏的 `read_media_file/read_multiple_files/list_directory_with_sizes/directory_tree/list_allowed_directories/read_query/list_tables/describe_table/read_graph/search_nodes/open_nodes/convert_time`。

## 验证
- `node --check app.js` 语法通过；前端 21 处插入点 grep 全落位。
- persona_constant.py `ast.parse` 语法通过。
- 路由评测 16 条全 OK（见报告 JSON）。
- 未做浏览器端联调（本地后端未起）；人格 tab 交互、chibi 气泡渲染需用户在线上验证。

## 遗留
- persona_router 100% 为小样本典型集结果，建议后续补边界模糊用例（执行+情绪混合）再测一轮。
- 本地跑 list_runtime_tools.py 时 mitta-tools 因脚本路径指向服务器目录而连接失败，mitta-tools 的 13 个工具名以其源码 `@m.tool()` 函数名为准（已核对）。
