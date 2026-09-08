# ============================================================
# 用户扩展信息接口（个人信息 / 自定义 prompt / 主题 / MCP）
# ============================================================
from pydantic import BaseModel
from typing import Optional, List, Dict, Any


class ProfileUpdateRequest(BaseModel):
    username: Optional[str] = None
    avatar: Optional[str] = None
    system_prompt: Optional[str] = None  # 全局 system prompt（自定义设定），空字符串表示清除


class PasswordUpdateRequest(BaseModel):
    old_password: str
    new_password: str


class ThemeUpdateRequest(BaseModel):
    theme: str


class McpConfigUpdateRequest(BaseModel):
    mcp_servers: List[Dict[str, Any]]
