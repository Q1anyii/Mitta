# ============================================================
# 用户扩展信息服务层
# 作用：管理用户个人信息（头像、助手风格、自定义 system prompt、主题、MCP 配置）
# 存储：PostgreSQL user_profile 表（与 userinfo 表通过 user_id 关联）
# 说明：已从 MySQL 迁移到 PostgreSQL，连接池与 chat_service / login_service 一致
# ============================================================

import json
from typing import Optional, Dict, Any

from loguru import logger
from dotenv import load_dotenv
import os
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

load_dotenv()


class UserProfileService:
    """用户扩展信息服务。

    与 LoginService 共用 PostgreSQL 连接池思路，但独立管理 user_profile 表。
    所有方法均为同步实现，FastAPI 路由层用普通 def 自动放入线程池。
    """

    def __init__(self):
        self._pool: Optional[ConnectionPool] = None

    def open(self):
        """初始化连接池（与 ChatService.open() 同期调用）。"""
        if self._pool:
            return
        db_url = os.getenv("POSTGRESQL_DB_URL")
        if not db_url:
            raise ValueError("数据库配置缺失，请设置环境变量 POSTGRESQL_DB_URL")

        self._pool = ConnectionPool(
            conninfo=db_url,
            kwargs={"autocommit": False},  # 需要事务控制（INSERT 失败回滚）
            min_size=1,
            max_size=10,
            timeout=5,
            open=True,
        )
        try:
            self._pool.check()
        except Exception as e:
            logger.error(f"PostgreSQL数据库连接失败（UserProfileService）：{e}")
            raise
        self._ensure_table()
        logger.info("UserProfileService 连接池已初始化（PostgreSQL）")

    def close(self, timeout: int = 5):
        """关闭连接池。"""
        if self._pool:
            self._pool.close(timeout=timeout)
            self._pool = None
            logger.info("UserProfileService 连接池已关闭")

    def _ensure_table(self):
        """确保 user_profile 表存在，不存在则创建（PG 语法）。"""
        with self._pool.connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_profile (
                    user_id VARCHAR(64) PRIMARY KEY,
                    username VARCHAR(64) DEFAULT NULL,
                    avatar TEXT DEFAULT NULL,
                    assistant_style TEXT DEFAULT NULL,
                    system_prompt TEXT DEFAULT NULL,
                    theme VARCHAR(32) DEFAULT 'default',
                    mcp_config JSONB DEFAULT NULL,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    updated_at TIMESTAMPTZ DEFAULT now()
                )
            """)
            # PG TEXT 无 64KB 上限，无需 MySQL 的 TEXT→MEDIUMTEXT 迁移逻辑
            conn.commit()

    # ==================== 基础 CRUD ====================

    def get_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        """获取用户扩展信息，不存在返回 None。"""
        with self._pool.connection() as conn:
            conn.row_factory = dict_row
            cur = conn.execute("SELECT * FROM user_profile WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
            if row and row.get("mcp_config"):
                # mcp_config 是 JSONB（dict）或字符串，统一归一为 list
                cfg = row["mcp_config"]
                if isinstance(cfg, str):
                    try:
                        row["mcp_config"] = json.loads(cfg)
                    except json.JSONDecodeError:
                        row["mcp_config"] = []
                elif isinstance(cfg, dict):
                    row["mcp_config"] = [cfg]
            return row

    def _upsert_profile(self, user_id: str, fields: Dict[str, Any]):
        """内部方法：插入或更新用户扩展信息。

        Args:
            user_id: 用户 ID
            fields: 要更新的字段字典（字段名 -> 值）
        """
        if not fields:
            return
        try:
            with self._pool.connection() as conn:
                # mcp_config 需序列化为 JSON 字符串存入 JSONB（psycopg 自动适配 dict，无需手动 dumps）
                values = dict(fields)
                if "mcp_config" in values and isinstance(values["mcp_config"], (dict, list)):
                    values["mcp_config"] = json.dumps(values["mcp_config"], ensure_ascii=False)

                # PG upsert：ON CONFLICT (user_id) DO UPDATE
                columns = list(values.keys())
                set_clause = ", ".join([f"{k} = EXCLUDED.{k}" for k in columns])
                placeholders = ", ".join([f"%({k})s" for k in columns])
                conn.execute(
                    f"""INSERT INTO user_profile (user_id, {', '.join(columns)})
                        VALUES (%(user_id)s, {placeholders})
                        ON CONFLICT (user_id) DO UPDATE SET
                            {set_clause},
                            updated_at = now()""",
                    {"user_id": user_id, **values},
                )
                conn.commit()
        except Exception as e:
            logger.error(f"更新用户扩展信息失败 user_id={user_id}: {e}")
            raise

    # ==================== 个人信息 ====================

    def update_basic_info(self, user_id: str, username: Optional[str] = None,
                           avatar: Optional[str] = None,
                           system_prompt: Optional[str] = None) -> bool:
        """更新用户基本信息（用户名、头像、全局 system prompt）。

        Args:
            user_id: 用户 ID
            username: 显示用户名（None 表示不更新）
            avatar: 头像（base64 data URL，None 表示不更新）
            system_prompt: 全局 system prompt（None 表示不更新，空字符串表示清除）

        Returns:
            bool: 是否成功
        """
        fields = {}
        if username is not None:
            fields["username"] = username
        if avatar is not None:
            fields["avatar"] = avatar
        if system_prompt is not None:
            fields["system_prompt"] = system_prompt
        if not fields:
            return False
        self._upsert_profile(user_id, fields)
        return True

    # ==================== 自定义 System Prompt ====================

    def get_system_prompt(self, user_id: str) -> Optional[str]:
        """获取用户自定义 system prompt，不存在或为空返回 None。"""
        profile = self.get_profile(user_id)
        if profile and profile.get("system_prompt"):
            return profile["system_prompt"]
        return None

    def update_system_prompt(self, user_id: str, content: str) -> bool:
        """更新用户自定义 system prompt。

        Args:
            user_id: 用户 ID
            content: system prompt 内容（空字符串表示清除）

        Returns:
            bool: 是否成功
        """
        self._upsert_profile(user_id, {"system_prompt": content})
        return True

    # ==================== 主题配置 ====================

    def get_theme(self, user_id: str) -> str:
        """获取用户主题名称，默认 'default'。"""
        profile = self.get_profile(user_id)
        if profile and profile.get("theme"):
            return profile["theme"]
        return "default"

    def update_theme(self, user_id: str, theme: str) -> bool:
        """更新用户主题。"""
        self._upsert_profile(user_id, {"theme": theme})
        return True

    # ==================== MCP 配置 ====================

    def get_mcp_config(self, user_id: str) -> list:
        """获取用户 MCP 服务器配置，返回 list（每个元素是一个 dict）。"""
        profile = self.get_profile(user_id)
        if profile and profile.get("mcp_config"):
            config = profile["mcp_config"]
            if isinstance(config, list):
                return config
            if isinstance(config, dict):
                return [config]
        return []

    def update_mcp_config(self, user_id: str, mcp_servers: list) -> bool:
        """更新用户 MCP 服务器配置。

        Args:
            user_id: 用户 ID
            mcp_servers: MCP 服务器配置列表，每个元素是 dict
                         格式示例：[{"name": "server1", "command": "npx", "args": ["-y", "mcp-server"], "env": {}}]

        Returns:
            bool: 是否成功
        """
        if not isinstance(mcp_servers, list):
            raise ValueError("mcp_servers 必须是列表")
        self._upsert_profile(user_id, {"mcp_config": mcp_servers})
        return True


# 模块级单例（与 chat_service / login_service 一致）
user_profile_service = UserProfileService()
