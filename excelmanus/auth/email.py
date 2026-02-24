"""Email sending module — Resend API (primary) + SMTP (fallback).

Auto-selects backend based on environment variables:
  - If EXCELMANUS_RESEND_API_KEY is set → use Resend
  - Elif EXCELMANUS_SMTP_HOST is set    → use SMTP
  - Else                                → log warning, skip (dev mode)
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
    """Render the verification email body as HTML."""
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
<body style="margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f5f5;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f5f5;padding:40px 0;">
    <tr><td align="center">
      <table width="480" cellpadding="0" cellspacing="0"
             style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08);">
        <!-- Header -->
        <tr>
          <td style="background:linear-gradient(135deg,#2563eb,#7c3aed);padding:32px 40px;text-align:center;">
            <h1 style="margin:0;color:#ffffff;font-size:22px;font-weight:700;letter-spacing:.5px;">
              ExcelManus
            </h1>
            <p style="margin:6px 0 0;color:rgba(255,255,255,.8);font-size:13px;">{title}</p>
          </td>
        </tr>
        <!-- Body -->
        <tr>
          <td style="padding:40px 40px 32px;">
            <p style="margin:0 0 20px;color:#374151;font-size:15px;line-height:1.6;">{intro}</p>
            <!-- Code box -->
            <div style="text-align:center;margin:28px 0;">
              <span style="display:inline-block;background:#f0f4ff;border:2px dashed #2563eb;
                           border-radius:10px;padding:14px 40px;
                           font-size:36px;font-weight:700;letter-spacing:12px;color:#1d4ed8;
                           font-family:'Courier New',monospace;">
                {code}
              </span>
            </div>
            <p style="margin:0;color:#6b7280;font-size:13px;text-align:center;">{note}</p>
          </td>
        </tr>
        <!-- Footer -->
        <tr>
          <td style="padding:20px 40px;border-top:1px solid #f3f4f6;text-align:center;">
            <p style="margin:0;color:#9ca3af;font-size:12px;">
              此邮件由 ExcelManus 自动发送，请勿直接回复。
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _make_text(code: str, purpose: PurposeType) -> str:
    """Plain text fallback."""
    action = "注册验证" if purpose == "register" else "密码重置"
    return (
        f"ExcelManus {action}\n\n"
        f"您的验证码是：{code}\n\n"
        "验证码 10 分钟内有效，请勿泄露给他人。\n"
    )


# ── Resend backend ─────────────────────────────────────────


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


# ── SMTP backend ───────────────────────────────────────────


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


# ── Public API ─────────────────────────────────────────────


async def send_verification_email(to: str, code: str, purpose: PurposeType) -> bool:
    """Send a verification code email.

    Returns True on success, False on failure (so callers can decide whether to raise).
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

    # No email backend configured
    logger.warning(
        "未配置邮件服务（EXCELMANUS_RESEND_API_KEY 或 EXCELMANUS_SMTP_HOST），"
        "验证码 %s 未发送给 %s。请在生产环境中配置邮件服务。",
        code, to,
    )
    return False


def is_email_configured() -> bool:
    """Return True if at least one email backend is configured."""
    return bool(
        os.environ.get("EXCELMANUS_RESEND_API_KEY", "").strip()
        or os.environ.get("EXCELMANUS_SMTP_HOST", "").strip()
    )
