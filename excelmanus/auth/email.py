"""邮件发送模块 — Resend API（主）+ SMTP（备用）。

根据环境变量自动选择后端：
  - 如果设置了 EXCELMANUS_RESEND_API_KEY → 使用 Resend
  - 否则如果设置了 EXCELMANUS_SMTP_HOST → 使用 SMTP
  - 否则 → 记录警告，跳过（开发模式）
"""

from __future__ import annotations

import asyncio
import logging
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Literal

logger = logging.getLogger(__name__)

PurposeType = Literal["register", "reset_password"]

_PURPOSE_SUBJECTS = {
    "register": "验证您的 ExcelManus 账号",
    "reset_password": "ExcelManus 密码重置验证码",
}


def _make_html(code: str, purpose: PurposeType) -> str:
    """将验证邮件正文渲染为 HTML。"""
    if purpose == "register":
        title = "邮箱验证"
        intro = "感谢注册 <strong>ExcelManus</strong>！请使用以下验证码完成邮箱验证："
        note = "验证码 10 分钟内有效，请勿泄露给他人。"
    else:
        title = "密码重置"
        intro = "您正在重置 <strong>ExcelManus</strong> 账号的密码，请使用以下验证码："
        note = "验证码 10 分钟内有效。如果您没有发起此请求，请忽略此邮件。"

    return f"""<!DOCTYPE html>
<html lang="zh">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',sans-serif;background:#f4f4f5;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f5;padding:48px 0;">
    <tr><td align="center">
      <table width="440" cellpadding="0" cellspacing="0"
             style="background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.06),0 4px 16px rgba(0,0,0,.06);">
        <!-- Header -->
        <tr>
          <td style="padding:36px 40px 28px;text-align:center;">
            <!--[if mso]>
            <table cellpadding="0" cellspacing="0" align="center"><tr>
            <td style="background:#217346;width:36px;height:36px;text-align:center;vertical-align:middle;border-radius:8px;">
              <span style="color:#ffffff;font-size:18px;font-weight:700;font-family:Consolas,monospace;">X</span>
            </td>
            <td style="padding-left:10px;">
              <span style="font-size:22px;font-weight:700;color:#1a1a1a;font-family:-apple-system,sans-serif;">ExcelManus</span>
            </td>
            </tr></table>
            <![endif]-->
            <!--[if !mso]><!-->
            <table cellpadding="0" cellspacing="0" align="center" style="display:inline-table;"><tr>
            <td style="background:#217346;width:36px;height:36px;text-align:center;vertical-align:middle;border-radius:8px;line-height:36px;">
              <span style="color:#ffffff;font-size:18px;font-weight:700;font-family:Consolas,'Courier New',monospace;">X</span>
            </td>
            <td style="padding-left:10px;vertical-align:middle;">
              <span style="font-size:22px;font-weight:700;color:#1a1a1a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">ExcelManus</span>
            </td>
            </tr></table>
            <!--<![endif]-->
          </td>
        </tr>
        <!-- Title bar -->
        <tr>
          <td style="padding:0 40px;">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr><td style="background:#217346;height:3px;border-radius:2px;font-size:0;line-height:0;">&nbsp;</td></tr>
            </table>
          </td>
        </tr>
        <!-- Body -->
        <tr>
          <td style="padding:32px 40px 28px;">
            <p style="margin:0 0 6px;color:#6b7280;font-size:13px;font-weight:600;text-transform:uppercase;letter-spacing:1px;">{title}</p>
            <p style="margin:0 0 24px;color:#374151;font-size:15px;line-height:1.7;">{intro}</p>
            <!-- Code box -->
            <div style="text-align:center;margin:0 0 24px;">
              <table cellpadding="0" cellspacing="0" align="center">
                <tr><td style="background:#f0faf4;border:2px solid #217346;border-radius:12px;padding:16px 44px;">
                  <span style="font-size:34px;font-weight:700;letter-spacing:10px;color:#217346;font-family:Consolas,'Courier New',monospace;">{code}</span>
                </td></tr>
              </table>
            </div>
            <p style="margin:0;color:#9ca3af;font-size:13px;text-align:center;line-height:1.6;">{note}</p>
          </td>
        </tr>
        <!-- Footer -->
        <tr>
          <td style="padding:20px 40px 24px;border-top:1px solid #f0f0f1;text-align:center;">
            <p style="margin:0;color:#b4b4b8;font-size:12px;line-height:1.6;">
              此邮件由 ExcelManus 系统自动发送，请勿直接回复。
            </p>
          </td>
        </tr>
      </table>
      <!-- Sub-footer branding -->
      <p style="margin:24px 0 0;color:#b4b4b8;font-size:11px;text-align:center;">
        &copy; ExcelManus &mdash; 基于大语言模型的 Excel 智能代理
      </p>
    </td></tr>
  </table>
</body>
</html>"""


def _make_text(code: str, purpose: PurposeType) -> str:
    """纯文本备用格式。"""
    action = "注册验证" if purpose == "register" else "密码重置"
    return (
        f"ExcelManus {action}\n\n"
        f"您的验证码是：{code}\n\n"
        "验证码 10 分钟内有效，请勿泄露给他人。\n"
    )


# ── Resend 后端 ─────────────────────────────────────────


async def _send_via_resend(
    api_key: str, from_addr: str, to: str, subject: str,
    html: str, text: str,
) -> None:
    import httpx

    payload = {
        "from": from_addr,
        "to": [to],
        "subject": subject,
        "html": html,
        "text": text,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://api.resend.com/emails",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
        )
    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"Resend API error {resp.status_code}: {resp.text[:300]}"
        )
    logger.debug("邮件已通过 Resend 发送至 %s", to)


# ── SMTP 后端 ───────────────────────────────────────────


def _send_via_smtp_sync(
    host: str, port: int, user: str, password: str, from_addr: str,
    to: str, subject: str, html: str, text: str, use_ssl: bool,
) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    context = ssl.create_default_context()
    if use_ssl:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=15) as server:
            server.login(user, password)
            server.sendmail(from_addr, [to], msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.ehlo()
            server.starttls(context=context)
            server.login(user, password)
            server.sendmail(from_addr, [to], msg.as_string())
    logger.debug("邮件已通过 SMTP 发送至 %s", to)


async def _send_via_smtp(
    host: str, port: int, user: str, password: str, from_addr: str,
    to: str, subject: str, html: str, text: str,
) -> None:
    use_ssl = port == 465
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        _send_via_smtp_sync,
        host, port, user, password, from_addr,
        to, subject, html, text, use_ssl,
    )


# ── 公开 API ─────────────────────────────────────────────


async def send_verification_email(to: str, code: str, purpose: PurposeType) -> bool:
    """发送验证码邮件。

    成功返回 True，失败返回 False（由调用方决定是否抛出异常）。
    """
    resend_key = os.environ.get("EXCELMANUS_RESEND_API_KEY", "").strip()
    smtp_host = os.environ.get("EXCELMANUS_SMTP_HOST", "").strip()

    subject = _PURPOSE_SUBJECTS.get(purpose, "ExcelManus 验证码")
    html = _make_html(code, purpose)
    text = _make_text(code, purpose)

    if resend_key:
        from_addr = os.environ.get(
            "EXCELMANUS_EMAIL_FROM",
            "ExcelManus <onboarding@resend.dev>",
        )
        try:
            await _send_via_resend(resend_key, from_addr, to, subject, html, text)
            return True
        except Exception:
            logger.exception("Resend 发送失败，尝试 SMTP 备用")
            if not smtp_host:
                return False

    if smtp_host:
        port = int(os.environ.get("EXCELMANUS_SMTP_PORT", "465"))
        user = os.environ.get("EXCELMANUS_SMTP_USER", "")
        password = os.environ.get("EXCELMANUS_SMTP_PASSWORD", "")
        from_addr = os.environ.get(
            "EXCELMANUS_EMAIL_FROM",
            f"ExcelManus <{user}>",
        )
        try:
            await _send_via_smtp(smtp_host, port, user, password, from_addr,
                                  to, subject, html, text)
            return True
        except Exception:
            logger.exception("SMTP 发送失败")
            return False

    # 未配置邮件后端
    logger.warning(
        "未配置邮件服务（EXCELMANUS_RESEND_API_KEY 或 EXCELMANUS_SMTP_HOST），"
        "验证码 %s 未发送给 %s。请在生产环境中配置邮件服务。",
        code, to,
    )
    return False


def is_email_configured() -> bool:
    """如果至少配置了一个邮件后端，返回 True。"""
    return bool(
        os.environ.get("EXCELMANUS_RESEND_API_KEY", "").strip()
        or os.environ.get("EXCELMANUS_SMTP_HOST", "").strip()
    )
