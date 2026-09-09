import os
from pathlib import Path
from typing import Optional

from loguru import logger
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from utils.jwt_utils import verify_password, get_password_hash
from datetime import datetime


class LoginService:
    # 环境变量 key 统一（已从 MySQL 迁移到 PostgreSQL）
    ENV_DB_URL = "POSTGRESQL_DB_URL"
    persist_path: str | Path

    def __init__(self, db_url: Optional[str] = None):
        # 优先传入参数，其次读取环境变量
        self.db_url: Optional[str] = db_url or os.getenv(self.ENV_DB_URL)

        # 数据库连接池对象
        self._pool: Optional[ConnectionPool] = None

        # 【移除无关LangGraph变量,LoginService只负责登录数据库，不要混入graph、checkpointer】
        self.persist_path = ""

    def open(self) -> None:
        """初始化 PostgreSQL 连接池，打开连接。"""
        if not self.db_url:
            raise ValueError(f"数据库配置缺失，请设置环境变量 {self.ENV_DB_URL} 或者传入 db_url 参数")

        self._pool = ConnectionPool(
            conninfo=self.db_url,
            kwargs={"autocommit": True},
            min_size=1,
            max_size=10,
            timeout=5,  # 借连接 5 秒快速失败
            open=True,
        )
        try:
            self._pool.check()
            self._ensure_table()
            logger.success("PostgreSQL连接池初始化成功（LoginService）")
        except Exception as e:
            logger.error(f"PostgreSQL数据库连接失败（LoginService）：{e}")
            raise

    def _ensure_table(self) -> None:
        """确保 userinfo 表存在（原 MySQL 迁移，PG 统一小写表名）。"""
        with self._pool.connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS userinfo (
                    id BIGSERIAL PRIMARY KEY,
                    user_id VARCHAR(64) NOT NULL UNIQUE,
                    password VARCHAR(255) NOT NULL,
                    username VARCHAR(64) DEFAULT NULL,
                    role VARCHAR(32) DEFAULT '学员',
                    create_time TIMESTAMPTZ DEFAULT now(),
                    update_time TIMESTAMPTZ DEFAULT now()
                )
            """)
            # 迁移：旧表无 role 列时补充（ALTER ADD COLUMN IF NOT EXISTS 幂等，重复启动安全）
            conn.execute("ALTER TABLE userinfo ADD COLUMN IF NOT EXISTS role VARCHAR(32) DEFAULT '学员'")
            conn.commit()
        logger.info("userinfo 表已就绪（PostgreSQL）")

    def close(self, timeout: int = 10) -> None:
        """关闭连接池，释放全部资源。"""
        if self._pool:
            self._pool.close(timeout=timeout)
            self._pool = None
            logger.info("PostgreSQL连接池已关闭（LoginService）")

    # 支持 with 上下文管理器
    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def login(self, user_id, password):
        with self._pool.connection() as conn:
            conn.row_factory = dict_row
            cur = conn.execute(
                "SELECT * FROM userinfo WHERE user_id = %s",
                (user_id,)
            )
            user_info = cur.fetchone()
            if user_info:
                if verify_password(password, user_info["password"]):
                    return user_info
                else:
                    return "密码错误"
            else:
                return f"用户{user_id}不存在"

    def get_user_by_id(self, user_id):
        """按用户 ID 查询用户信息（聊天接口 JWT 认证后获取当前用户）"""
        with self._pool.connection() as conn:
            conn.row_factory = dict_row
            cur = conn.execute("SELECT * FROM userinfo WHERE user_id = %s", (user_id,))
            return cur.fetchone()

    def register(self, username, user_id, password):
        """用户注册。

        Args:
            username: 用户名
            user_id: 用户 ID
            password: 明文密码（内部会 bcrypt 哈希）

        Returns:
            tuple: (flag, response)，flag=True 表示成功
        """
        now = datetime.now()
        flag = False
        try:
            with self._pool.connection() as conn:
                cur = conn.execute("SELECT user_id FROM userinfo WHERE user_id = %s", (user_id,))
                if cur.fetchone():
                    return flag, "该用户已存在"
                password = get_password_hash(password)
                # PG：id 由 BIGSERIAL 自动生成
                success = conn.execute(
                    "INSERT INTO userinfo (user_id, password, username, create_time, update_time) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (user_id, password, username, now, now),
                )
                flag = True
                return flag, success.rowcount
        except Exception as e:
            logger.error(f"数据库执行异常 {e}")
            return flag, f"账号注册失败,请联系管理员"

    def recover(self, user_id, new_password):
        update_time = datetime.now()
        try:
            with self._pool.connection() as conn:
                conn.row_factory = dict_row
                cur = conn.execute("SELECT user_id, password FROM userinfo WHERE user_id = %s", (user_id,))
                user_info = cur.fetchone()
                if not user_info:
                    return f"用户{user_id}不存在"
                user_id = user_info["user_id"]
                old_password = user_info["password"]
                if verify_password(new_password, old_password):
                    return f"密码不可与原密码相同"
                new_password = get_password_hash(new_password)
                result = conn.execute(
                    "UPDATE userinfo SET password = %s, update_time = %s WHERE user_id = %s",
                    (new_password, update_time, user_id),
                )
                return result.rowcount
        except Exception as e:
            logger.error(f"数据库执行异常, 联系管理员")
            raise


login_service = LoginService()

# ---------------- 业务示例 登录查询用户 ----------------
if __name__ == "__main__":
    # .env 文件配置： POSTGRESQL_DB_URL=postgresql://root:1234@127.0.0.1:5432/agentproject?sslmode=disable
    from dotenv import load_dotenv
    load_dotenv()

    with LoginService() as service:
        with service._pool.connection() as conn:
            conn.row_factory = dict_row
            cur = conn.execute("SELECT * FROM userinfo WHERE user_id = %s", ("user",))
            user = cur.fetchone()
            print(user)
