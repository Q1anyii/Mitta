docs_sync: required

# 注册绑定邮箱 + 找回密码改按邮箱发验证码（H-20260922-01）

## 改动

### 后端
- `login_schema.py`：
  - `RegisterRequest` 新增 `email: str` 字段 + Pydantic 邮箱格式校验（`_EMAIL_RE`）
  - `RecoverCodeRequest` / `RecoverRequest` 入参从 `userId` 改为 `email`
- `login_service.py`：
  - `_ensure_table()` 加 `ALTER TABLE userinfo ADD COLUMN IF NOT EXISTS email VARCHAR(128)`（幂等迁移，老用户 email 允许 NULL）
  - `register()` 新增 `email` 参数，INSERT 写入 email 列
  - 新增 `find_user_id_by_email(email)` 按邮箱反查 user_id
- `email_service.py`：`send_recover_code(user_id, code)` → `send_recover_code(email, code)`，receiver 直接用真实邮箱（修掉原来把 user_id 当邮箱的错误假设）
- `auth_router.py`：
  - register 透传 email
  - recover/code：按 email 反查 user_id，查到才发码（查不到也返回成功，防枚举）
  - recover：按 email 反查 user_id，再校验 Redis `recover:code:{user_id}`

### 前端（app.js）
- RegisterForm：用户ID和密码之间加邮箱 input，前端正则校验，提交 body 加 email
- RecoverForm：用户ID字段改邮箱，副标题改"输入邮箱获取验证码"，sendCode/handleRecover body 字段改 email

## 安全语义保留
- 防枚举：邮箱查不到时接口仍返回"验证码已发送"，不暴露邮箱是否存在
- Redis key 仍用 user_id 维度（`recover:code:{user_id}`），只是从前端 email 反查这一步
- 限流维度从 userId 改为 email（同 IP+email 桶）

## 验证
- 4 个后端文件 ast.parse 通过
- 前端 10 处替换全部命中（grep 确认）

## 遗留
- 老用户 qianyi 无 email，走不了找回密码（预期，测试账户不处理）
- SMTP 线上未配，发码仍打日志
- 前端 5 个 input 纵向排，布局未实测（表单高度增加）
