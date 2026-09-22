# ============================================================
# 验证码发送服务（密码找回）
# 说明：本项目暂无真实邮件/短信通道。当前实现为开发态——验证码打到日志，
#       并预留 SMTP 接入点。生产环境配置 SMTP_* 环境变量后自动走邮件发送。
# 安全：不向调用方泄露"用户是否存在"，发送失败只记日志不影响接口返回。
# ============================================================

import os
import smtplib
from email.mime.text import MIMEText

from loguru import logger


def send_recover_code(user_id: str, code: str) -> None:
    """向用户发送密码找回验证码。

    优先走 SMTP（配置了 SMTP_HOST 时）；否则开发态打日志，便于联调。
    任何发送异常都不抛给调用方——避免泄露用户存在性，也不阻断接口。
    """
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    if smtp_host:
        try:
            _send_smtp(user_id, code, smtp_host)
            return
        except Exception as e:
            logger.error(f"SMTP 发送验证码失败，回退日志输出：{e}")

    # 开发态：验证码打日志（生产应配 SMTP_HOST 走真实邮件）
    logger.warning(
        f"[开发态·未配 SMTP] 密码找回验证码 | user_id={user_id} | code={code}（5 分钟内有效，用后即焚）"
    )


def _send_smtp(user_id: str, code: str, smtp_host: str) -> None:
    """通过 SMTP 发送验证码邮件。需在环境变量配置：
    SMTP_HOST / SMTP_PORT(默认465) / SMTP_USER / SMTP_PASS /
    SMTP_FROM(默认=SMTP_USER) / SMTP_TO(user_id 即收件人邮箱)
    """
    port = int(os.getenv("SMTP_PORT", "465"))
    user = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASS", "")
    sender = os.getenv("SMTP_FROM", user)
    receiver = user_id  # 本项目 user_id 即登录名/邮箱

    msg = MIMEText(
        f"您正在重置 Mitta 密码，验证码：{code}\n5 分钟内有效，仅限使用一次。"
        f"\n如非本人操作请忽略。",
        "plain",
        "utf-8",
    )
    msg["Subject"] = "Mitta 密码找回验证码"
    msg["From"] = sender
    msg["To"] = receiver

    with smtplib.SMTP_SSL(smtp_host, port, timeout=10) as s:
        s.login(user, password)
        s.sendmail(sender, [receiver], msg.as_string())
    logger.info(f"验证码邮件已发送至 {receiver}")
