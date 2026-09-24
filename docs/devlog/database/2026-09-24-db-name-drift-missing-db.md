docs_sync: none

# 部署 PoolTimeout 根因排查：PG 目标库漂移（agentproject → mitta）

## 现象

CI（acr-cicd.yml step 部署段）每次起 api 都失败：
- 健康检查循环 24 次全部 `HTTP 000000`；
- api 容器日志：`chat_service.open()` → `checkpointer.setup()` 阶段抛
  `psycopg_pool.PoolTimeout: couldn't get a connection after 5.00 sec`，随后 `Application startup failed`。

表象全是"拿不到 PG 连接"，排查被带偏了好几轮。

## 走过的弯路（均被证据推翻）

1. 旧 api 容器未停、PG idle 连接残留 → CI 加 `stop api` + `sleep 5`，仍失败。
2. PG 连接池未清空 → CI 改 `restart postgres` + `until pg_isready`，仍失败。
3. PG crash recovery 未完成、用户名不对 → `pg_isready -U postgres` 改 `-U root`、再加 `sleep 10`，仍失败。
4. 1.6G 内存不足导致 PG fork 连接慢 → `free -m` 实测有 2G swap（仅用 344M）、available 482M、`dmesg` 无 OOM killed，排除。

## 决定性证据

在 api 容器内手动建 pool（timeout 放到 20s）：

```
pool open 用时 0.0s
error connecting in 'pool-1': connection to server at "172.18.0.4", port 5432 failed:
FATAL:  database "mitta" does not exist
```

`\l` 列出实际数据库：`agentproject`（项目旧名）、`postgres`、`template0/1`——**根本没有 mitta 库**。

psycopg_pool `open=True` 立即返回（0.0s），后台 worker 反复尝试连 mitta 都被 PG 拒绝，池里永远没有可用连接；业务侧 `getconn(timeout=5)` 等不到连接 → 抛 PoolTimeout。PoolTimeout 只是表象，真正错误（database does not exist）只在 pool 后台日志里。

## 根因

`POSTGRES_DB` 环境变量**只在 postgres 数据卷首次初始化（空目录）时建库**。
该卷是项目旧名 AgentProject 时期初始化的，建的库叫 `agentproject`。
后来 compose / .env 把目标库改成 `mitta`（`POSTGRES_DB:-mitta`），但已有数据卷不会补建 mitta，于是应用连 `postgres:5432/mitta` 一直报库不存在。

## 修复

服务器上一条改名（保留全部历史数据，可逆）：

```bash
docker compose stop api
docker exec mitta-postgres psql -U root -d postgres \
  -c "ALTER DATABASE agentproject RENAME TO mitta;"
docker compose up -d --no-build api
```

注意：**不能** `CREATE DATABASE mitta`——那会建空库，历史数据仍留在 agentproject 里，等于数据"丢失"。改名最干净。

## 教训

- 改 `POSTGRES_DB` / 连接串库名后，必须同步服务器已有数据卷（`ALTER DATABASE ... RENAME` 或迁移），否则 CI 必挂。
- `POSTGRES_DB` 不是运行时配置，是初始化参数；`restart`/`up -d` 不会重建库。
- 看到 PoolTimeout，先在应用容器内手动建 pool 打真实连接错误（FATAL 那行），不要先怀疑内存/连接残留/时序。
- 排查这类连接问题，`docker logs` 要看 pool 后台的 `error connecting in` 行，那才是真因。

## 备注

CI 里为此加的 `restart postgres` + `sleep 10` 等时序措施已无必要（根因不是时序），后续可评估是否简化；`pg_isready -U root` 是对的（PG 超级用户确为 root），保留。
