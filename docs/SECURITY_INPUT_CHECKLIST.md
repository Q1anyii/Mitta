# 用户输入安全 Checklist

> **用途**：每写一个「接受用户输入」的函数/接口/工具前，先过一遍本清单。
> 原则：**默认拒绝（fail-closed）、最小权限、解析后判定、不信任任何外部输入。**
> 来源：A2–A11 安全加固项落地沉淀。

---

## 一、输入校验与命令执行

- [ ] **A1 · 路径校验不能用 `startswith`**
  - 反例：`str(target).startswith(str(ALLOWED_ROOT))` 是字符串前缀比较，`/app` 的兄弟 `/app2/secret` 会被判为合法。
  - 做法：`target.relative_to(ALLOWED_ROOT.resolve())` 包在 try/except 里，抛 `ValueError` 即拒绝。

- [ ] **A2 · 外部命令/包名白名单要默认拒绝，不能只白名单第一个参数**
  - 反例：`python3 -c "..."`、`node -e "..."` 里 `-c`/`-e` 被当 flag 跳过 → 任意代码执行。
  - 做法：参数中出现 `-c / -e / -r / -m / --eval / --require`（含 `=xxx` 形态）任一即拒绝；只允许「白名单包 + 白名单参数形态」。

- [ ] **A4 · 敏感操作不能只靠「知道用户名」**
  - 反例：`/api/recover` 仅传 user_id 就改密码 = 无凭证账号接管。
  - 做法：验证码（邮件/短信）+ 一次性 token，token 有 TTL 且**用后即焚**（GETDEL/删除）；用户不存在也返回成功，防枚举。

- [ ] **A3 · 凡是验证/重置凭据的端点必须独立限流**
  - 做法：login / recover 按 `IP + userId` 双维度 Redis 滑动窗口，失败计数 + **指数退避**；Redis 挂降级放行但不影响主流程。

## 二、认证与授权（fail-closed）

- [ ] **A6 · 授权判断里 `None` 必须当拒绝，不能当放行**
  - 反例：`if owner and owner != uid` —— `owner` 为 `None` 时短路为 False → 放行（fail-open）。
  - 做法：`owner is None` 且「记录存在但归属不明」→ 403；只有「记录不存在（新建）」才放行。核心：**校验拿不到值 = 拒绝**。

- [ ] **A10 · 限流/配额键必须来自「已验签」的身份**
  - 反例：`jwt.decode(token, options={"verify_signature": False})` 取 sub 当键 → 伪造 JWT 改 sub 即可换桶。
  - 做法：必须 `jwt.decode(token, SECRET_KEY, algorithms=[...])` **验签通过后**才用 sub；验签失败一律退回 IP 键。

## 三、会话与 Token

- [ ] **A7 · Refresh token 必须有 `jti` + 轮换 + 绝对过期**
  - 反例：每次续签都 `setex(now+30天)` → TTL 被不断重置 = refresh 永不过期（滑动）。
  - 做法：payload 加 `jti`(唯一) + `iat`；续签时**继承旧 token 的绝对过期**不延长，换新 jti（旧 token 立即失效）。

- [ ] **（token 存储）Token 不能放前端 JS 可读的地方**
  - 反例：token 存 localStorage，配合 XSS 可被直接偷走。
  - 做法：优先 httpOnly Cookie；过渡期至少保证输出渲染无 XSS（见 A9）。

## 四、SSRF / XSS / 注入

- [ ] **A8 · SSRF 防护必须「解析成 IP 再判定」，不能字符串匹配**
  - 反例：`any(x in url for x in ["127.0.0.1", ...])` —— `http://127.1`、`http://[::1]`、十进制 `2130706433`、八进制 `0177.0.0.1` 全绕过。
  - 做法：`urlparse` 取 host → `getaddrinfo` 解析所有 A/AAAA → `ipaddress.ip_address()` 判 `is_private/is_loopback/is_link_local/is_multicast/is_reserved`；解析失败 **fail-closed**。

- [ ] **A9 · 渲染用户/知识库内容前必须消毒（防 XSS）**
  - 反例：`marked.parse()` 后未消毒直接 `v-html` → 注入 `<img src=x onerror=偷localStorage>`。
  - 做法：输出经 `DOMPurify.sanitize()` 再渲染；代码块/链接白名单化。

## 五、部署与运行时

- [ ] **A11 · 容器不以 root 运行**
  - 做法：`RUN useradd -m appuser` + `USER appuser`，工作目录/可写目录 `chown` 给它；运行时需的工具（uvx 等）装到全局共享路径并 chown，别放 `/root/.local`。

---

## 自检口诀

1. **默认拒绝**：任何「校验失败/取不到值」按拒绝处理，不按放行。
2. **解析后判定**：IP、域名、路径都要解析成真实对象再判断，不做字符串匹配。
3. **最小权限**：进程非 root、凭据操作独立限流、敏感操作要二次凭证。
4. **不丢状态**：token 有绝对过期、一次性 token 用后即焚、jti 可轮换。
5. **不信任渲染**：任何外部内容上页面前先消毒。
