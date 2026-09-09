# Mitta 用户存储 MySQL → PostgreSQL 全量迁移 开发日志

> 涉及模块：登录服务（`src/service/login_service.py`）、用户档案服务（`src/service/user_profile_service.py`）、文件上传服务（`src/service/file_upload_service.py`）、配置（`src/config.py` / `src/init.py`）、部署（`docker-compose.yml` / `.env.example` / `requirements.txt`）、迁移脚本（`scripts/migrate_mysql_to_pg.py`）、`README.md`
> 关联提交：`806eba2`（feat: 用户存储 MySQL 迁移 PostgreSQL + 知识库增量更新 API + README 更新）

---

## 一、问题背景与动机

外部评审对项目提出"技术栈过于叠 buff"的质疑：**一个项目同时使用 PostgreSQL + MySQL 两个关系数据库**——PostgreSQL 管 LangGraph Checkpointer/Store，MySQL 管用户表（userInfo / user_profile / user_files）。对个人开发者或小团队，运维成本不低，且"为用而用"的嫌疑明显。

**决策**（用户拍板）：
1. 服务器是轻量级，**不考虑本地部署模型**
2. **去掉 MySQL，用户信息迁移到 PostgreSQL**，减少一个依赖
3. 顺带把用户表的三张表建到同一个 PG 实例

---

## 二、现状盘点（迁移前）

| 服务 | 原数据库 | 连接方式 | 表 |
|------|---------|---------|-----|
| `login_service` | MySQL | pymysql + PooledDB | `userInfo`（登录/注册/改密） |
| `user_profile_service` | MySQL | pymysql + PooledDB | `user_profile`（头像/风格/Prompt/主题/MCP配置） |
| `file_upload_service` | MySQL | pymysql + PooledDB | `user_files`（base64 存文件） |

PG 侧已有先例：`chat_service` / `mcp_config_service` 用 `psycopg_pool` 连接 `POSTGRESQL_DB_URL`，迁移后三个服务应统一走同一条 PG 连接串。

---

## 三、排查过程

### 第 1 步：确认迁移范围（不只是三个服务）

全仓 grep `pymysql` / `MYSQL_DB_URL` / `DictCursor` / `DBUtils`，确认共 6 处需要同步清理：

| 文件 | 残留 |
|------|------|
| `src/config.py` | `REQUIRED_ENV_VARS` 里有 `MYSQL_DB_URL` 必填校验 |
| `src/init.py` | 顶层 `import pymysql` + `from pymysql.cursors import DictCursor` |
| `requirements.txt` | `pymysql==1.2.0` + `DBUtils==3.1.2` |
| `docker-compose.yml` | mysql 服务 + 环境变量 + depends_on + volume |
| `.env.example` / `.env` | `MYSQL_DB_URL` 配置段 |

**结论**：迁移不是"改三个服务文件"这么简单，必须全链路清理，否则残留 import 会直接启动失败、残留必填校验会让 `validate_config` 报错。

### 第 2 步：明确 PG 与 MySQL 的语法差异点

| 差异 | MySQL | PostgreSQL |
|------|-------|-----------|
| 自增主键 | `AUTO_INCREMENT` + `lastrowid` | `BIGSERIAL` + `INSERT ... RETURNING id` |
| 字段长度 | `TEXT` 64KB 上限，超长需 `MEDIUMTEXT` | `TEXT` 无上限，去掉迁移逻辑 |
| 表名大小写 | 驼峰 `userInfo` | 统一小写 `userinfo` |
| upsert | `INSERT ... ON DUPLICATE KEY UPDATE` | `INSERT ... ON CONFLICT (user_id) DO UPDATE` |
| JSON | `JSON` 类型 | `JSONB`（psycopg 自动适配 dict，无需手动 dumps） |
| 连接池 | `PooledDB` 返回连接对象 | `psycopg_pool.ConnectionPool` |

### 第 3 步：踩坑——psycopg_pool 3.x 的 `connection()` 不是连接对象

首个版本手写 `conn = self._pool.connection()` 然后直接 `.execute()`，真机测试立即暴露：

```
ERROR | service.login_service:register:149 - 数据库执行异常 '_GeneratorContextManager' object has no attribute 'execute'
AttributeError: '_GeneratorContextManager' object has no attribute 'close'
```

**根因**：psycopg_pool 3.x 的 `pool.connection()` 返回的是**上下文管理器**（`_GeneratorContextManager`），不是连接对象；必须 `with self._pool.connection() as conn:` 进入后才拿到真正的连接，退出时自动归还池。

**修复**：三个服务全部改为 with 块模式（与 `mcp_config_service` 已有写法保持一致）：

```python
with self._pool.connection() as conn:
    conn.row_factory = dict_row   # 需要 dict 行时在 with 块内设置
    cur = conn.execute("SELECT ...", (user_id,))
    row = cur.fetchone()
```

---

## 四、解决方案

### 1. 三个服务重写为 PostgreSQL 连接池

**`login_service.py`**：
- `PooledDB` → `psycopg_pool.ConnectionPool`（min_size=1, max_size=10, timeout=5, open=True）
- `userInfo` 表 → `userinfo`（小写），`BIGSERIAL PRIMARY KEY`
- `INSERT ... RETURNING id` 替代 `lastrowid`
- 注册/登录/改密逻辑不变，仅换 SQL 方言

**`user_profile_service.py`**：
- `_upsert_profile` 用 `ON CONFLICT (user_id) DO UPDATE SET ..., updated_at = now()`
- `mcp_config` 存 `JSONB`，psycopg 对 dict/list 自动适配（代码里 `json.dumps` 后传入亦可）
- 去掉 MySQL 的 `TEXT→MEDIUMTEXT` 迁移逻辑

**`file_upload_service.py`**：
- `BIGSERIAL` + `RETURNING id` 拿 file_id
- 索引改为 PG 语法（`CREATE INDEX IF NOT EXISTS`）
- base64 内容仍存 `TEXT`（10MB 文件 base64 后约 13.3MB，PG TEXT 无上限，安全）

### 2. 依赖与配置清理

| 文件 | 修改 |
|------|------|
| `requirements.txt` | 删除 `pymysql==1.2.0`、`DBUtils==3.1.2`，注释同步更新 |
| `src/config.py` | `REQUIRED_ENV_VARS` 删除 `MYSQL_DB_URL`，`POSTGRESQL_DB_URL` 说明改为"Checkpointer/Store + 用户表" |
| `src/init.py` | 删除 `import pymysql` / `from pymysql.cursors import DictCursor` |
| `docker-compose.yml` | 删除 mysql 服务 + 环境变量 + depends_on + `mysql_data` volume |
| `.env.example` / `.env`（本地） | 删除 `MYSQL_DB_URL` 配置段 |

### 3. 一次性数据迁移脚本 `scripts/migrate_mysql_to_pg.py`

- 读 `MYSQL_DB_URL`（pymysql）→ 写 `POSTGRESQL_DB_URL`（psycopg_pool）
- 三表迁移：`userInfo→userinfo`、`user_profile→user_profile`、`user_files→user_files`
- **幂等**：按 `user_id` 判断目标已存在则跳过，防止误改密码/重复插入
- `mcp_config` 字段 `::jsonb` 转换
- 服务器升级时在停 MySQL 前跑一次

---

## 五、验证结果（真机集成）

用真实后端启动路径（连接池 + 建表 + CRUD）逐项验证：

```
=== 1. login_service ===
open OK / userinfo 表已就绪（PostgreSQL）
register -> flag=True resp=1
login -> dict: test_migrate_001
recover -> 1
login2 -> dict: test_migrate_001

=== 2. user_profile_service ===
profile -> username=新名字, system_prompt='你是测试助手'
theme -> dark
mcp_config -> [{'args': [], 'name': 'x', 'command': 'echo'}]

=== 3. file_upload_service ===
save -> {'file_id': 1, 'file_name': 'hello.md', 'file_size': 22, 'file_ext': '.md'}
list -> 1 file(s), first=hello.md
extract -> '# Hello\n\nWorld content'
delete -> True
```

**全部通过**，测试数据已清理。验证点覆盖：注册/登录/改密、profile 读写（含 system_prompt/theme/JSONB mcp_config）、文件保存/列表/文本提取/删除。

---

## 六、遗留与注意事项

| 项 | 状态 | 说明 |
|----|------|------|
| 服务器存量数据迁移 | **待执行** | `scripts/migrate_mysql_to_pg.py` 已就绪，升级时在停 MySQL 前跑一次 |
| 本地 MySQL 容器 | 停用 | 数据迁移后可删 `mysql_data` volume |
| `tests/test_rand_id_util.py` | 保留 | 仍引用 `MYSQL_INT_MAX` 常量（uuid4 取模上限），PG 下依旧安全，仅 README 描述改为中性表述 |
| 端口 | 已释放 | 3306 不再被本项目占用 |

---

## 七、经验沉淀

1. **"换数据库"不等于"改连接串"**：import、必填校验、依赖、compose、env 模板必须全链路同步清理，残留一处就是启动失败
2. **psycopg_pool 3.x 的 `connection()` 是上下文管理器**：直接当连接对象用会报 `_GeneratorContextManager has no attribute execute`；统一 `with pool.connection() as conn:` 模式
3. **方言差异列个对照表再动手**：BIGSERIAL/RETURNING、ON CONFLICT、JSONB、TEXT 无上限——先列差异，避免逐文件试错
4. **迁移脚本必须幂等**：按业务主键判断跳过，防止误覆盖密码、重复插入文件
5. **真机验证是唯一标准**：建表 + CRUD 全链路跑一遍，比读代码自信得多
