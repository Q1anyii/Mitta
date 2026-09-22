我按刚才分的 A/B/C 三类，把报告里的问题重新归一次。先说明判定标准：

- **A 类（知识缺口）**：靠「慢慢悟」悟不出来，必须被告诉一次 → 直接给你结论
- **B 类（经验积累）**：值得自己啃，懂了能迁移到下一个项目 → 你主导
- **C 类（体力活）**：零认知收益 → 批量清

---

## C 类：体力活（约 2 小时，可批量清）

这些不用思考，清完就消失：

| 项 | 位置 | 动作 |
|---|---|---|
| 死代码 | `classify_node()` 主体、`persona_router_node.py`、`rand_id_util.py` | 删 |
| 注释残留 "MySQL" | `chat_router.py:210`、`auth_router.py:34`、`user_router.py:191` | 改 PG |
| Magic number | `parallel_nodes.py:78,118`、`fusion_nodes.py:349` 的 `[:3]` / `n_results=20` | 提常量 |
| 死文件 | `deploy/nginx/default.conf`（与实跑的 `resources/frontend/nginx.conf` 不同源） | 删或合并 |
| 注释与实现不符 | `cache_nodes.py:58` 写 "TTL 15 秒"，实际 `CACHE_DEFAULT_TTL=900` | 改注释 |
| LSH 未设种子 | `lsh_util.py:5` `np.random.randn` 无 seed | 加 `np.random.default_rng(42)` |

**注意 LSH 那条**：加 seed 不只是「清代码」，它会让你的语义缓存命中率**从「每次重启后漂移」变成稳定**——你之前 E6-B 测出 `hit_rate 0.9722`，但那个数字下次重启就不一定复现。这是个隐性 bug，顺手修掉。

---

## A 类：知识缺口（每条约 15 分钟，直接给结论）

这类我不跟你绕。每条就是「为什么危险 + 怎么改」：

**A1 · 路径校验不能用 `startswith`**（`mitta_tools_server.py:227`）
`str(target).startswith(str(ALLOWED_ROOT))` 是**字符串比较**，不是路径包含判断。`/app` 的「同前缀兄弟」`/app2/secret` 会被判为合法。
改法：`target.relative_to(ALLOWED_ROOT.resolve())` 包在 try/except 里，抛 `ValueError` 即拒绝。

**A2 · 用户可控输入不能直传 subprocess**（`mcp_config_service.py:302`）
你的包名白名单只查「第一个非 `-` 参数」，但 `python3 -c "..."` 里 `-c` 被当 flag 跳过 → 任意代码执行。
改法：stdlib MCP 只允许**白名单包 + 白名单参数形态**，显式拒绝 `-c/-e/--eval/--require/-r`。默认拒绝，不是默认放行。

**A3 · 认证端点必须独立限流**
你的限流中间件只认 `POST /api/chat/`。经验法则：**凡是「验证凭据」和「重置凭据」的端点，是最需要限流的端点**，因为它们是爆破的目标。
改法：login/recover 加 Redis 滑动窗口（按 IP + userId 双维度），失败计数 + 指数退避。

**A4 · 密码重置不能只靠「知道用户名」**
`/api/recover` 仅需 `user_id` 改密码 = 无凭证的账号接管。
改法：验证码（邮件/短信）+ 一次性 token，token 有 TTL 且用后即焚。

**A5 · 认证失败信息不能区分「用户不存在」和「密码错误」**
`auth_router.py:40` 回显「用户不存在」= 账号枚举。
改法：统一文案「用户名或密码错误」，且**响应时间也要对齐**（先走一次假 bcrypt）。

**A6 · 授权判断里 `None` 必须当拒绝，不能当放行**
`chat_router.py:43` 的 `if owner and owner != uid` —— owner 为 `None` 时整条判断短路为 False → 放行。
改法：读作「`owner is None` → 403」。核心原则：**fail-closed，不是 fail-open**。这条是安全编码里最重要的一条，记住它。

**A7 · Refresh token 必须有 `jti` + 轮换 + 绝对过期**
你现在每次续签重签并覆盖，等于 refresh 永不过期。
改法：payload 加 `jti` 和 `iat`，续签时**作废旧 jti**（rotation）+ 记录绝对过期时间，到期不续。

**A8 · SSRF 防护要用 IP 解析，不能用字符串匹配**
`http://127.1`、`http://[::1]`、`http://2130706433` 都能绕过你的字符串黑名单。
改法：`ipaddress.ip_address()` + `.is_private` / `.is_loopback` 判定，且**解析后判定**（防 DNS rebinding 要再校验连接目标 IP）。

**A9 · JWT 存 localStorage = XSS 直接偷 token**
你前端 `v-html` 渲染 LLM 输出且 `marked` 未消毒，知识库注入 `< img src=x onerror=...>` 即可读 localStorage。
改法：token 走 httpOnly Cookie；或渲染前过 DOMPurify。

**A10 · 限流键必须来自「已验签」的身份**
`rate_limit_middleware.py:42` 用 `verify_signature: False` 解析 `sub` 当限流键 = 伪造 JWT 就能换桶。
改法：限流键用**验签后**的 `sub`，验签失败退回 IP 键。

**A11 · 容器不要以 root 运行**（`Dockerfile` 无 `USER`）
改法：`RUN useradd -m appuser` + `USER appuser`，且 `chown` 好工作目录。

> 这 11 条你花两小时读完，比你自己踩三个月坑快。它们不是「技巧」，是**安全编码的基本盘**。读完建议做一件事：把这 11 条整理成一份 checklist，以后每写一个接受用户输入的函数，先过一遍。

---

## B 类：这才是你该慢下来的地方（建议 3–4 个晚上）

**B1 · State reducer 与并发写语义**（核心，优先级最高）

问题现场：`retrieve_graph.py` 里 `store_cache` 和 `filter` 从 `rerank` 并行扇出，**两个节点都读写 `reranked_docs`**，而 `state.py:55` 里这个 key 是裸 `List[Any]`，**没有 reducers**。

你要自己想清楚的问题：
- LangGraph 里 `Annotated[list, operator.add]` 到底在什么时候生效？并行分支各自返回时怎么合并？
- 为什么「无 reducer 的 key 被两个并行节点同时写」会出问题？去翻 LangGraph 源码里的 `InvalidUpdateError`
- `store_cache` 缓存的数据是 filter **前**还是**后**的？这个语义模糊是不是设计问题？
- 你已经给别的 key 用了 reducer 吗？去看 `state.py` 全貌，对比哪些加了哪些没加，**规律是什么**

> 为什么这条最值得啃：**状态建模是 LangGraph 应用最容易翻车的地方**，而且它没有「标准答案」——你得理解框架的合并语义才能设计对。搞懂这个，你下个 Agent 项目能直接设计对，而不是等报错。

**B2 · `Send` 与条件边的语义边界**（架构级）

现场：`routes.py:13-34` 的 `route()` 返回 `list[Send]`，但 `main_graph.py:171-175` 用 `add_conditional_edges(..., ["retrieve_node","llm_node"])` 注册。

你要想清楚：
- `Send` 是干什么的？它和「条件边返回字符串」的本质区别是什么？（提示：一个用于 map-reduce 扇出，一个用于单路径分支）
- `Send` 的 payload **不继承父 state**——这一点作者在注释里已经痛苦地踩过一次（`persona` 丢失事故 `H-20260919-09`）。那么 `retrieve_res` 会不会也丢？`needs_retrieval` 呢？
- 如果 `retrieve_node` 收不到完整 state，为什么现在的检索**看起来还能跑**？是凑巧还是真有隐患？→ **去跑一遍验证，别信我的结论**

> 这条我特意没给死结论。我在报告里标了 P0，但**你应该自己验证一遍**——因为「文档说 Send 不继承 state」和「代码实际怎么跑」之间，可能还有 LangGraph 版本差异。自己跑出来的结论才是你的。

**B3 · 循环熔断应该是硬约束还是软约束**（设计品味）

现场：`llm_node.py:299` 的 `force_stop` 只是往 prompt 里注入「别再调工具了」，但**没有剥离 `final_chunk.tool_calls`**。如果模型不听话，`route_after_llm` 还是会路由回 `tool_node`。

你要想清楚：
- 「提示模型别做」和「代码层面做不到」的区别是什么？为什么前者不可靠？
- 这类问题在分布式系统里叫什么？（提示：guard condition / circuit breaker 的**执行点**应该在状态机层还是模型层）
- 对照你自己的 `_is_repeating()` 设计——那个是硬约束，为什么这里就软了？**设计一致性**出了什么问题？

> 这条训练的是**「不信任 LLM 输出」的工程直觉**。这是做 Agent 最重要的一条心法。

**B4 · 文档漂移的根因（流程问题，不是代码问题）**

你的 README 有两处系统性漂移：说「classify_node 主图」实际是 `router_node`；说「BM25 双路互补」实际 ingest 从不建索引。

思考：
- 为什么漂移方向**总是朝着「更复杂更工程化」**？（这是很有意思的自我观察）
- 你甚至专门提交过 `docs: README 去开发日志化` ——说明你在意文档。那问题出在哪？
- 怎么设计一个**机制**（不是靠自觉），让「改代码 → 文档必须同步」？

> 这条没有标准答案，但想清楚它会让你从「写代码的人」变成「维护系统的人」。

---

## 建议的推进节奏

| 时间 | 做什么 |
|---|---|
| **今晚 1 小时** | 清完 C 类（6 项），顺手修 LSH seed |
| **本周内 2 小时** | 读完 A 类 11 条，**理解后再自己动手修 P0**（A2/A4/A6/A7 是必改项） |
| **下周 3–4 个晚上** | 按 B1 → B2 → B3 → B4 顺序啃，每个问题**先自己想、再验证、最后才看答案** |
| **持续** | 把 A 类整理成 checklist，每次写接受输入的函数前过一遍 |

**一个提醒**：A 类里 A2/A4/A6/A7 是那 6 个 P0 的核心，**它们是事故响应，别拖到「学完再改」**。你可以先按我给的改法修掉、把服务保护住，然后**再回头慢慢理解为什么**。保护和理解不冲突，别用「我要先学会」当延迟修复的理由——这是我上一轮就想提醒你的点。

有想深挖的 B 类问题，随时把你自己推演的结论发我，我帮你验证对错。