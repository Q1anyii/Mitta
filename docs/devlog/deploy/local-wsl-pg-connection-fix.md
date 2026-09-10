# 本地开发环境 PostgreSQL 连接修复（WSL NAT 模式）

## 现象

- 后端启动时 `psycopg_pool.PoolTimeout: couldn't get a connection after 5.00 sec`，`Application startup failed. Exiting.`。
- 现象不固定：有时 `POSTGRESQL_DB_URL` 指向 `localhost:5432` 能连上，重启后同样配置又超时；`Test-NetConnection localhost 5432` 时通时不通。
- 日志中 `PostgreSQL连接池初始化成功`（`pool.check()` 只检查池对象本身），随后 `checkpointer.setup()` 真正取连接时才超时。

## 排查

1. **确认网络层可达**：`Test-NetConnection localhost:5432` 与直连 WSL IP `172.29.183.97:5432`（TCP 5s 内）均通——TCP 层不是问题。
2. **确认 WSL 状态**：`wsl -l -v` 显示 Ubuntu Running、docker-desktop Stopped；PG 在 WSL 内监听 `0.0.0.0:5432`，Redis 监听 6380。
3. **直连 WSL IP 复现真实错误**：用 `psycopg.connect` 直连 WSL IP，拿到决定性报错：
   `FATAL: no pg_hba.conf entry for host "172.29.176.1", user "root", database "agentproject", no encryption`
4. **根因定位**：
   - Windows 处于 **WSL NAT 模式**（`wsl --version` 的 localhostForwarding 不稳定），`localhost:5432` 转发不可靠——转发生效时 PG 看到来源 `127.0.0.1`（pg_hba 放行），转发失效时后端直接超时。
   - 改用 WSL IP 直连后，连接来源是 Windows NAT 网关 IP `172.29.176.1`，而 WSL 内 PG 的 `pg_hba.conf` 只放行 `127.0.0.1/32`、`::1/128` 和 `0.0.0.0/0`（仅限 `langchain_db.langchain_user`），因此 `agentproject.root` 被拒绝。

## 解决

1. **pg_hba.conf 放行 Windows NAT 网段**（WSL 内）：
   在 `/etc/postgresql/16/main/pg_hba.conf` 的 `0.0.0.0/0` 规则前插入：
   `host all all 172.29.0.0/16 scram-sha-256`
   然后 `sudo -u postgres psql -c "select pg_reload_conf();"` 热加载（无需重启 PG）。
2. **`.env` 改用 WSL IP 直连**（本地开发环境配置，`.env` 已被 .gitignore，不入库）：
   - `POSTGRESQL_DB_URL="postgresql://root:1234@172.29.183.97:5432/agentproject?sslmode=disable"`
   - `REDIS_DB_URL="redis://172.29.183.97:6380"`
3. 重启后端：`python src/main.py`，`/health` 返回 `{"status":"ok","db":true}`。

## 验证

- WSL 内 `psql 'postgresql://root:1234@localhost:5432/agentproject' -c 'select 1'` 通过（认证本身没问题）。
- Windows 侧 `psycopg.connect(WSL_IP_URL, connect_timeout=5)`：`OK (1,) 0.02s`。
- 后端完整启动，MCP 冷启动完成后健康检查通过。

## 经验

- WSL NAT 模式下 `localhost` 转发不可靠：**转发走的是 WSL 的 localhost 代理，直连走的是 NAT 网关**，两者的来源 IP 不同（前者 127.0.0.1，后者如 172.29.176.1），pg_hba 规则必须同时覆盖。
- `ConnectionPool(...).check()` 只验证连接串/池对象，不真正建立连接；`checkpointer.setup()` 取连接时才暴露 pg_hba 拒绝——排查时直接用 `psycopg.connect` 复现，别被"初始化成功"误导。
- 修改 pg_hba 后 `pg_reload_conf()` 即可热加载，不需要重启 PostgreSQL 服务。
- WSL IP 每次重启可能变化（`wsl --shutdown` 后 `hostname -I` 重新确认）；若 IP 变化需同步更新 `.env`。
