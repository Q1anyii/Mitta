#!/bin/bash
# ============================================================
# PostgreSQL 每日备份：pg_dump 到 ./backups，保留最近 7 天
# 用法: 手动跑 ./backup_pg.sh，或挂 crontab：
#   0 3 * * * /opt/mitta/deploy/backup_pg.sh >> /opt/mitta/logs/backup.log 2>&1
# ============================================================
set -e
cd "$(dirname "$0")/.."

# 从 .env 读库配置（未设则用默认）
set -a; [ -f .env ] && . ./.env; set +a
PG_USER="${POSTGRES_USER:-root}"
PG_DB="${POSTGRES_DB:-agentproject}"

BACKUP_DIR="./backups"
mkdir -p "$BACKUP_DIR"
TS=$(date +%Y%m%d_%H%M%S)
OUT="$BACKUP_DIR/pg_${TS}.sql.gz"

docker exec mitta-postgres pg_dump -U "$PG_USER" "$PG_DB" | gzip > "$OUT"

# 清理 7 天前的备份
find "$BACKUP_DIR" -name "pg_*.sql.gz" -mtime +7 -delete

# 简单磁盘水位提示（超过 85% 告警，不阻断）
USAGE=$(df / | awk 'NR==2{print $5}' | tr -d '%')
echo "✅ 备份完成: $OUT ($(du -h "$OUT" | cut -f1))，磁盘使用率 ${USAGE}%"
if [ "$USAGE" -ge 85 ]; then
  echo "⚠️ 磁盘使用率 ${USAGE}% 偏高，请清理"
fi
