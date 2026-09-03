# ============================================================
# MCP 配置服务（PostgreSQL 存储，按用户隔离）
# 作用：管理每个用户的 MCP 服务器配置，存储在 PostgreSQL 中
# 替代原有的本地 JSON 文件存储方案
# 安全：命令白名单、路径隔离、Windows→Linux 路径自动转换
# ============================================================

import json
import os
import re
from typing import Any, Optional

from loguru import logger
from psycopg import sql
from psycopg_pool import ConnectionPool


# ============================================================
# 安全常量
# ============================================================

# 允许的 MCP 启动命令白名单（防止用户执行任意命令）
ALLOWED_COMMANDS = {"npx", "uvx", "node", "python", "python3", "pipx"}

# 允许的 MCP 服务器类型
ALLOWED_TYPES = {"stdio", "sse"}

# 已知安全的 MCP 包名白名单（npx/uvx 后第一个非 - 参数）
# 不在白名单中的包会被拒绝，防止执行恶意包
SAFE_MCP_PACKAGES = {
    "@modelcontextprotocol/server-filesystem",
    "@modelcontextprotocol/server-git",
    "@modelcontextprotocol/server-fetch",
    "@modelcontextprotocol/server-sequential-thinking",
    "@modelcontextprotocol/server-memory",
    "@modelcontextprotocol/server-sqlite",
    "@modelcontextprotocol/server-puppeteer",
    "mcp-server-fetch",
    "mcp-server-git",
    "mcp-server-sqlite",
    "mcp-server-memory",
}

# 用户文件系统根目录（filesystem MCP 只能访问此目录下的文件）
USER_FILESYSTEM_ROOT = "/app/user_files"

# 单个用户最多 MCP 服务器数量
MAX_SERVERS_PER_USER = 10


# ============================================================
# PostgreSQL 表结构
# ============================================================

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS user_mcp_servers (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL,
    name VARCHAR(64) NOT NULL,
    type VARCHAR(16) NOT NULL DEFAULT 'stdio',
    command VARCHAR(255),
    args JSONB NOT NULL DEFAULT '[]',
    cwd VARCHAR(512),
    env JSONB NOT NULL DEFAULT '{}',
    url VARCHAR(512),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, name)
);
CREATE INDEX IF NOT EXISTS idx_user_mcp_servers_user_id ON user_mcp_servers(user_id);
"""


# ============================================================
# 路径转换与安全校验
# ============================================================

def is_windows_path(path: str) -> bool:
    """判断是否为 Windows 路径（如 E:/工作文件/... 或 E:\\工作文件\\...）"""
    if not path:
        return False
    # 匹配盘符开头：C:/、D:\、E:/ 等
    return bool(re.match(r'^[a-zA-Z]:[/\\]', path))


def convert_windows_to_linux_path(win_path: str, user_id: str = "") -> str:
    """将 Windows 路径转换为 Linux 容器内路径。

    转换规则：
    - E:/工作文件/AgentProject → /app/user_files/{user_id}/project
    - 任意 Windows 路径 → /app/user_files/{user_id}/ 下的安全子目录
    - 已经是 Linux 路径且在允许目录内 → 保持不变
    - 已经是 Linux 路径但不在允许目录内 → 重定向到用户目录

    Args:
        win_path: 原始路径
        user_id: 用户 ID（用于构建隔离目录）

    Returns:
        转换后的 Linux 绝对路径
    """
    if not win_path:
        return win_path

    # 已经是 Linux 绝对路径
    if win_path.startswith("/"):
        # 检查是否在允许的目录内
        allowed_prefixes = [
            USER_FILESYSTEM_ROOT,
            "/app/resources",
            "/app/config",
            "/tmp",
        ]
        for prefix in allowed_prefixes:
            if win_path.startswith(prefix):
                return win_path
        # 不在允许目录内，重定向到用户目录
        safe_name = os.path.basename(win_path.rstrip("/\\")) or "data"
        return f"{USER_FILESYSTEM_ROOT}/{user_id}/{safe_name}"

    # Windows 路径转换
    if is_windows_path(win_path):
        # 提取路径中的有意义目录名（最后一级或两级）
        # E:/工作文件/AgentProject → project
        # E:/工作文件/AgentProject/resources → resources
        parts = re.split(r'[/\\]+', win_path)
        # 去掉盘符部分（parts[0] = 'E:'）
        meaningful = [p for p in parts[1:] if p and p not in ('工作文件', 'workspace', 'projects')]
        if meaningful:
            # 取最后两级目录名
            subdir = "_".join(meaningful[-2:]) if len(meaningful) >= 2 else meaningful[-1]
        else:
            subdir = "project"
        # 清理非法字符
        subdir = re.sub(r'[^\w\-]', '_', subdir)
        return f"{USER_FILESYSTEM_ROOT}/{user_id}/{subdir}"

    # 相对路径，转为用户目录下
    return f"{USER_FILESYSTEM_ROOT}/{user_id}/{win_path}"


def validate_mcp_server_config(cfg: dict, user_id: str) -> dict:
    """校验并清洗单个 MCP 服务器配置。

    安全检查：
    1. type 必须在白名单内
    2. command 必须在白名单内
    3. 包名必须在安全白名单内（防止执行恶意包）
    4. cwd 路径转换为 Linux 路径并限制在用户目录
    5. env 中不允许包含敏感环境变量（PATH、HOME 等）
    6. sse 类型的 url 必须是 http/https

    Args:
        cfg: 原始 MCP 服务器配置
        user_id: 用户 ID

    Returns:
        清洗后的安全配置

    Raises:
        ValueError: 配置不通过安全校验
    """
    if not isinstance(cfg, dict):
        raise ValueError("MCP 服务器配置必须是对象")

    name = cfg.get("name", "").strip()
    if not name:
        raise ValueError("MCP 服务器名称不能为空")
    if len(name) > 64:
        raise ValueError("MCP 服务器名称不能超过 64 字符")

    server_type = cfg.get("type", "stdio").strip().lower()
    if server_type not in ALLOWED_TYPES:
        raise ValueError(f"不支持的 MCP 服务器类型: {server_type}，允许: {ALLOWED_TYPES}")

    cleaned = {
        "name": name,
        "type": server_type,
        "command": None,
        "args": [],
        "cwd": None,
        "env": {},
        "url": None,
    }

    if server_type == "stdio":
        command = cfg.get("command", "").strip().lower()
        if not command:
            raise ValueError(f"MCP 服务器 [{name}] 缺少 command")
        if command not in ALLOWED_COMMANDS:
            raise ValueError(
                f"MCP 服务器 [{name}] 的命令 '{command}' 不在白名单内，"
                f"允许: {ALLOWED_COMMANDS}"
            )
        cleaned["command"] = command

        args = cfg.get("args", [])
        if not isinstance(args, list):
            raise ValueError(f"MCP 服务器 [{name}] 的 args 必须是数组")
        cleaned["args"] = [str(a) for a in args]

        # 校验包名安全（npx/uvx 后第一个非 - 参数）
        package_name = None
        for arg in cleaned["args"]:
            if not arg.startswith("-"):
                package_name = arg
                break
        if package_name and package_name not in SAFE_MCP_PACKAGES:
            raise ValueError(
                f"MCP 服务器 [{name}] 的包 '{package_name}' 不在安全白名单内。"
                f"如需添加，请联系管理员。当前允许: {SAFE_MCP_PACKAGES}"
            )

        # cwd 路径转换
        cwd = cfg.get("cwd")
        if cwd:
            cleaned["cwd"] = convert_windows_to_linux_path(str(cwd), user_id)

        # args 中的路径转换（filesystem 的路径参数、sqlite 的 --db 参数等）
        converted_args = []
        for i, arg in enumerate(cleaned["args"]):
            if is_windows_path(arg) or (arg.startswith("/") and not arg.startswith("/app") and not arg.startswith("/tmp")):
                converted_args.append(convert_windows_to_linux_path(arg, user_id))
            else:
                converted_args.append(arg)
        cleaned["args"] = converted_args

        # env 安全过滤
        env = cfg.get("env", {})
        if isinstance(env, dict):
            forbidden_env = {"PATH", "HOME", "USER", "SHELL", "LD_LIBRARY_PATH", "PYTHONPATH"}
            safe_env = {}
            for k, v in env.items():
                if k.upper() in forbidden_env:
                    logger.warning(f"MCP 服务器 [{name}] 的环境变量 {k} 被过滤（安全限制）")
                    continue
                safe_env[str(k)] = str(v)
            cleaned["env"] = safe_env

    elif server_type == "sse":
        url = cfg.get("url", "").strip()
        if not url:
            raise ValueError(f"MCP 服务器 [{name}] (sse) 缺少 url")
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"MCP 服务器 [{name}] 的 url 必须是 http/https 协议")
        # 禁止访问内网地址（安全限制）
        if any(x in url for x in ["localhost", "127.0.0.1", "0.0.0.0", "192.168.", "10.", "172.16."]):
            raise ValueError(f"MCP 服务器 [{name}] 的 url 不能是内网地址（安全限制）")
        cleaned["url"] = url

    return cleaned


# ============================================================
# MCP 配置服务（PostgreSQL）
# ============================================================

class McpConfigService:
    """基于 PostgreSQL 的用户 MCP 配置管理服务。

    按 user_id 隔离，每个用户独立管理自己的 MCP 服务器配置。
    所有写入操作均经过安全校验和路径转换。
    """

    def __init__(self, pool: ConnectionPool):
        self.pool = pool

    def init_table(self) -> None:
        """初始化数据库表（不存在则创建）。"""
        with self.pool.connection() as conn:
            conn.execute(CREATE_TABLE_SQL)
            conn.commit()
        logger.info("user_mcp_servers 表已初始化")

    def get_user_servers(self, user_id: str) -> list[dict]:
        """获取用户的所有 MCP 服务器配置（已清洗为可用格式）。

        Args:
            user_id: 用户 ID

        Returns:
            MCP 服务器配置列表，格式与 load_mcp_server_configs() 一致
        """
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT name, type, command, args, cwd, env, url FROM user_mcp_servers WHERE user_id = %s ORDER BY id",
                (user_id,)
            ).fetchall()

        servers = []
        for row in rows:
            name, stype, command, args, cwd, env, url = row
            server = {
                "name": name,
                "type": stype,
            }
            if stype == "stdio":
                server["command"] = command
                server["args"] = args if isinstance(args, list) else json.loads(args) if args else []
                if cwd:
                    server["cwd"] = cwd
                if env:
                    server["env"] = env if isinstance(env, dict) else json.loads(env) if env else {}
            elif stype == "sse":
                server["url"] = url
            servers.append(server)
        return servers

    def save_user_servers(self, user_id: str, servers: list[dict]) -> tuple[bool, str]:
        """保存用户的全部 MCP 服务器配置（全量替换）。

        先校验所有配置，通过后删除旧配置并插入新配置（事务）。

        Args:
            user_id: 用户 ID
            servers: MCP 服务器配置列表

        Returns:
            (是否成功, 消息)
        """
        if not isinstance(servers, list):
            return False, "mcp_servers 必须是数组"

        if len(servers) > MAX_SERVERS_PER_USER:
            return False, f"单个用户最多配置 {MAX_SERVERS_PER_USER} 个 MCP 服务器"

        # 先全部校验
        cleaned_servers = []
        seen_names = set()
        for cfg in servers:
            try:
                cleaned = validate_mcp_server_config(cfg, user_id)
            except ValueError as e:
                return False, str(e)
            if cleaned["name"] in seen_names:
                return False, f"MCP 服务器名称重复: {cleaned['name']}"
            seen_names.add(cleaned["name"])
            cleaned_servers.append(cleaned)

        # 事务：删除旧配置 + 插入新配置
        try:
            with self.pool.connection() as conn:
                conn.execute("DELETE FROM user_mcp_servers WHERE user_id = %s", (user_id,))
                for s in cleaned_servers:
                    conn.execute(
                        """INSERT INTO user_mcp_servers (user_id, name, type, command, args, cwd, env, url)
                           VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s::jsonb, %s)
                           ON CONFLICT (user_id, name) DO UPDATE SET
                               type = EXCLUDED.type,
                               command = EXCLUDED.command,
                               args = EXCLUDED.args,
                               cwd = EXCLUDED.cwd,
                               env = EXCLUDED.env,
                               url = EXCLUDED.url,
                               updated_at = CURRENT_TIMESTAMP""",
                        (
                            user_id,
                            s["name"],
                            s["type"],
                            s.get("command"),
                            json.dumps(s.get("args", []), ensure_ascii=False),
                            s.get("cwd"),
                            json.dumps(s.get("env", {}), ensure_ascii=False),
                            s.get("url"),
                        )
                    )
                conn.commit()
            logger.success(f"用户 [{user_id}] 的 MCP 配置已保存（{len(cleaned_servers)} 个服务器）")
            return True, f"已保存 {len(cleaned_servers)} 个 MCP 服务器配置"
        except Exception as e:
            logger.error(f"保存用户 [{user_id}] 的 MCP 配置失败: {e}")
            return False, f"保存失败: {e}"

    def delete_user_server(self, user_id: str, name: str) -> bool:
        """删除用户的单个 MCP 服务器配置。

        Args:
            user_id: 用户 ID
            name: 服务器名称

        Returns:
            是否成功
        """
        with self.pool.connection() as conn:
            result = conn.execute(
                "DELETE FROM user_mcp_servers WHERE user_id = %s AND name = %s",
                (user_id, name)
            )
            conn.commit()
            return result.rowcount > 0


# 模块级单例（在 chat_service.open() 中初始化 pool 后设置）
mcp_config_service: Optional[McpConfigService] = None


def init_mcp_config_service(pool: ConnectionPool) -> McpConfigService:
    """初始化全局 MCP 配置服务单例。

    Args:
        pool: PostgreSQL 连接池

    Returns:
        MCP 配置服务实例
    """
    global mcp_config_service
    mcp_config_service = McpConfigService(pool)
    mcp_config_service.init_table()
    # 确保用户文件系统目录存在
    os.makedirs(USER_FILESYSTEM_ROOT, exist_ok=True)
    return mcp_config_service
