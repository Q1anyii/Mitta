from pydantic import BaseModel, Field, field_validator
from datetime import datetime
from typing import Optional
import re


# 密码强度：至少 8 位，且同时含字母和数字
_PASSWORD_MIN = 8
_PASSWORD_MAX = 64
_PASSWORD_RE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d).+$")


def _validate_password_strength(v: str) -> str:
    if not v or len(v) < _PASSWORD_MIN:
        raise ValueError(f"密码至少 {_PASSWORD_MIN} 位")
    if len(v) > _PASSWORD_MAX:
        raise ValueError(f"密码不能超过 {_PASSWORD_MAX} 位")
    if not _PASSWORD_RE.match(v):
        raise ValueError("密码需同时包含字母和数字")
    return v


# 认证请求模型（MySQL 用户表校验）
class LoginRequest(BaseModel):
    userId: str
    password: str

class RegisterRequest(BaseModel):
    userName: str
    userId: str
    password: str = Field(min_length=_PASSWORD_MIN, max_length=_PASSWORD_MAX)
    # 创建/更新时间由后端生成，前端注册时无需传入
    createTime: Optional[datetime] = None
    updateTime: Optional[datetime] = None

    @field_validator("password")
    @classmethod
    def _pw_strength(cls, v: str) -> str:
        return _validate_password_strength(v)

    def __iter__(self):
        # 迭代顺序
        yield self.userName
        yield self.userId
        yield self.password

class RecoverCodeRequest(BaseModel):
    userId: str

class RecoverRequest(BaseModel):
    userId: str
    code: str
    newPassword: str

    @field_validator("newPassword")
    @classmethod
    def _npw_strength(cls, v: str) -> str:
        return _validate_password_strength(v)
