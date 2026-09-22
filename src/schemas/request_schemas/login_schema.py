from pydantic import BaseModel, Field, field_validator
from datetime import datetime
from typing import Optional
import re


# 密码强度：至少 8 位，且同时含字母和数字
_PASSWORD_MIN = 8
_PASSWORD_MAX = 64
_PASSWORD_RE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d).+$")

# 邮箱格式：宽松校验（xxx@xxx.xx）
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _validate_password_strength(v: str) -> str:
    if not v or len(v) < _PASSWORD_MIN:
        raise ValueError(f"密码至少 {_PASSWORD_MIN} 位")
    if len(v) > _PASSWORD_MAX:
        raise ValueError(f"密码不能超过 {_PASSWORD_MAX} 位")
    if not _PASSWORD_RE.match(v):
        raise ValueError("密码需同时包含字母和数字")
    return v


def _validate_email(v: str) -> str:
    v = (v or "").strip()
    if not v:
        raise ValueError("邮箱不能为空")
    if len(v) > 128:
        raise ValueError("邮箱长度不能超过 128")
    if not _EMAIL_RE.match(v):
        raise ValueError("邮箱格式不正确")
    return v


# 认证请求模型（PostgreSQL 用户表校验）
class LoginRequest(BaseModel):
    userId: str
    password: str

class RegisterRequest(BaseModel):
    userName: str
    userId: str
    email: str
    password: str = Field(min_length=_PASSWORD_MIN, max_length=_PASSWORD_MAX)
    # 创建/更新时间由后端生成，前端注册时无需传入
    createTime: Optional[datetime] = None
    updateTime: Optional[datetime] = None

    @field_validator("password")
    @classmethod
    def _pw_strength(cls, v: str) -> str:
        return _validate_password_strength(v)

    @field_validator("email")
    @classmethod
    def _email_format(cls, v: str) -> str:
        return _validate_email(v)

    def __iter__(self):
        # 迭代顺序
        yield self.userName
        yield self.userId
        yield self.email
        yield self.password

class RecoverCodeRequest(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def _email_format(cls, v: str) -> str:
        return _validate_email(v)

class RecoverRequest(BaseModel):
    email: str
    code: str
    newPassword: str

    @field_validator("email")
    @classmethod
    def _email_format(cls, v: str) -> str:
        return _validate_email(v)

    @field_validator("newPassword")
    @classmethod
    def _npw_strength(cls, v: str) -> str:
        return _validate_password_strength(v)
