# -*- coding: utf-8 -*-
"""
MySQL → PostgreSQL 用户数据一次性迁移脚本

背景：项目已将用户相关存储从 MySQL（userInfo/user_profile/user_files 表）
迁移到 PostgreSQL（userinfo/user_profile/user_files 表）。
本脚本把 MySQL 中已有的存量数据导入 PostgreSQL，供升级时使用一次。

用法：
    cd 项目根目录
    set MYSQL_DB_URL=mysql+pymysql://root:1234@127.0.0.1:3306/mitta
    set POSTGRESQL_DB_URL=postgresql://root:1234@127.0.0.1:5432/agentproject?sslmode=disable
    python scripts/migrate_mysql_to_pg.py

说明：
- 需要先安装 pymysql（迁移用一次）：pip install pymysql DBUtils
- 幂等：目标表已有相同 user_id 时跳过（不覆盖，防止误改密码）
- 迁移完成后可停用 MySQL 容器
"""

import os
import sys
from pathlib import Path

# 项目 src 目录加入路径（复用项目的 psycopg 连接串解析）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from loguru import logger  # noqa: E402


def get_mysql_conn():
    import pymysql

    url = os.getenv("MYSQL_DB_URL")
    if not url:
        raise RuntimeError("缺少 MYSQL_DB_URL 环境变量")
    rest = url.split("://", 1)[1]
    user_pass, host_port_db = rest.split("@", 1)
    user, password = user_pass.split(":", 1)
    host_port, dbname = host_port_db.split("/", 1)
    host, port = host_port.split(":", 1)
    return pymysql.connect(
        host=host, port=int(port), user=user, password=password,
        database=dbname, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
    )


def get_pg_pool():
    from psycopg_pool import ConnectionPool

    url = os.getenv("POSTGRESQL_DB_URL")
    if not url:
        raise RuntimeError("缺少 POSTGRESQL_DB_URL 环境变量")
    return ConnectionPool(conninfo=url, min_size=1, max_size=5, open=True)


def migrate_users(mysql_conn, pg_pool):
    """迁移 userInfo → userinfo。"""
    cur = mysql_conn.cursor()
    cur.execute("SELECT id, user_id, password, username, create_time, update_time FROM userInfo")
    rows = cur.fetchall()
    cur.close()
    count = 0
    with pg_pool.connection() as conn:
        for row in rows:
            exists = conn.execute(
                "SELECT 1 FROM userinfo WHERE user_id = %s", (row["user_id"],)
            ).fetchone()
            if exists:
                continue
            conn.execute(
                """INSERT INTO userinfo (id, user_id, password, username, create_time, update_time)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (row["id"], row["user_id"], row["password"], row["username"],
                 row["create_time"], row["update_time"]),
            )
            count += 1
        conn.commit()
    logger.success(f"userinfo 迁移完成：新写入 {count} 条（已存在跳过）")
    return count


def migrate_profiles(mysql_conn, pg_pool):
    """迁移 user_profile。"""
    cur = mysql_conn.cursor()
    cur.execute("SELECT * FROM user_profile")
    rows = cur.fetchall()
    cur.close()
    count = 0
    with pg_pool.connection() as conn:
        for row in rows:
            exists = conn.execute(
                "SELECT 1 FROM user_profile WHERE user_id = %s", (row["user_id"],)
            ).fetchone()
            if exists:
                continue
            conn.execute(
                """INSERT INTO user_profile
                   (user_id, username, avatar, assistant_style, system_prompt, theme, mcp_config, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)""",
                (row["user_id"], row["username"], row["avatar"], row["assistant_style"],
                 row["system_prompt"], row["theme"], row["mcp_config"],
                 row["created_at"], row["updated_at"]),
            )
            count += 1
        conn.commit()
    logger.success(f"user_profile 迁移完成：新写入 {count} 条（已存在跳过）")
    return count


def migrate_files(mysql_conn, pg_pool):
    """迁移 user_files。"""
    cur = mysql_conn.cursor()
    cur.execute("SELECT * FROM user_files")
    rows = cur.fetchall()
    cur.close()
    count = 0
    with pg_pool.connection() as conn:
        for row in rows:
            conn.execute(
                """INSERT INTO user_files
                   (id, user_id, thread_id, file_name, file_type, file_ext, file_size, file_content, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (row["id"], row["user_id"], row["thread_id"], row["file_name"],
                 row["file_type"], row["file_ext"], row["file_size"], row["file_content"],
                 row["created_at"]),
            )
            count += 1
        conn.commit()
    logger.success(f"user_files 迁移完成：新写入 {count} 条")
    return count


def main():
    from dotenv import load_dotenv

    load_dotenv()
    mysql_conn = get_mysql_conn()
    pg_pool = get_pg_pool()
    try:
        migrate_users(mysql_conn, pg_pool)
        migrate_profiles(mysql_conn, pg_pool)
        migrate_files(mysql_conn, pg_pool)
        logger.success("=" * 40)
        logger.success("MySQL → PostgreSQL 数据迁移全部完成")
    finally:
        mysql_conn.close()
        pg_pool.close()


if __name__ == "__main__":
    main()
