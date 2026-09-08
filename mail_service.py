import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from config import Config

logger = logging.getLogger("mail_service")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s [MAIL] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def send_email(
    to_email: str,
    subject: str,
    html_content: str,
    text_content: Optional[str] = None,
) -> bool:
    """
    Sends an email using the SMTP settings from Config.
    If SMTP_SERVER is empty or fails, logs the email safely to console/logs.
    """
    if not to_email:
        logger.warning("Attempted to send email with no recipient specified.")
        return False

    server_host = Config.SMTP_SERVER.strip()
    sender = Config.SMTP_SENDER_EMAIL

    # If no SMTP server configured, simulate sending (useful for local dev/testing)
    if not server_host:
        logger.info(
            f"[SIMULATED EMAIL] To: {to_email} | Subject: '{subject}'\n"
            f"Content:\n{text_content or html_content}\n"
        )
        return True

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = to_email

        if text_content:
            msg.attach(MIMEText(text_content, "plain"))
        if html_content:
            msg.attach(MIMEText(html_content, "html"))

        with smtplib.SMTP(server_host, Config.SMTP_PORT, timeout=10) as server:
            if Config.SMTP_USE_TLS:
                server.starttls()
            if Config.SMTP_USERNAME and Config.SMTP_PASSWORD:
                server.login(Config.SMTP_USERNAME, Config.SMTP_PASSWORD)
            server.sendmail(sender, [to_email], msg.as_string())

        logger.info(f"Email successfully delivered to {to_email} via {server_host}")
        return True

    except Exception as e:
        logger.error(f"Failed to send email to {to_email} via {server_host}: {e}")
        # Log content anyway so OTP is visible during development
        logger.info(f"[FAILED EMAIL FALLBACK LOG] To: {to_email} | Subject: '{subject}' | Body: {text_content or html_content}")
        return False


def send_otp_email(to_email: str, otp_code: str, emp_name: str = "Employee") -> bool:
    """Send an OTP code for login verification."""
    subject = f"Docket - Your Login OTP Code: {otp_code}"

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f4f5f7; margin: 0; padding: 20px; }}
            .card {{ max-width: 480px; margin: 0 auto; background: #ffffff; border-radius: 8px; border: 1px solid #e1e4e8; padding: 32px; }}
            .brand {{ font-size: 18px; font-weight: 700; color: #1e293b; margin-bottom: 24px; }}
            .otp-box {{ background: #f0f4ff; border: 1px dashed #3b82f6; border-radius: 6px; padding: 16px; text-align: center; margin: 24px 0; }}
            .otp-code {{ font-size: 32px; font-weight: 800; letter-spacing: 6px; color: #1d4ed8; }}
            .note {{ font-size: 13px; color: #64748b; line-height: 1.5; }}
            .footer {{ margin-top: 30px; font-size: 11px; color: #94a3b8; border-top: 1px solid #f1f5f9; padding-top: 12px; }}
        </style>
    </head>
    <body>
        <div class="card">
            <div class="brand">Docket Department Portal</div>
            <p>Hello <strong>{emp_name}</strong>,</p>
            <p>Use the one-time password below to complete your login verification:</p>
            <div class="otp-box">
                <div class="otp-code">{otp_code}</div>
            </div>
            <p class="note">This code is valid for 10 minutes. If you did not request this login, please contact IT support.</p>
            <div class="footer">
                JSW Dharamtar Port &middot; Docket Portal
            </div>
        </div>
    </body>
    </html>
    """

    text_content = f"Hello {emp_name},\n\nYour Docket login OTP code is: {otp_code}\n\nValid for 10 minutes."
    return send_email(to_email, subject, html_content, text_content)


def send_request_outcome_email(to_email: str, req_id: str, outcome: str, remark: str = "") -> bool:
    """Send guest house approval/rejection outcome notification."""
    subject = f"Docket - Guest House Request {req_id} {outcome.capitalize()}"
    status_color = "#16a34a" if outcome.lower() == "approved" else "#dc2626"

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #f4f5f7; padding: 20px; }}
            .card {{ max-width: 500px; margin: 0 auto; background: #ffffff; border-radius: 8px; padding: 28px; border: 1px solid #e2e8f0; }}
            .badge {{ display: inline-block; padding: 4px 12px; border-radius: 9999px; color: #fff; background: {status_color}; font-weight: 600; font-size: 13px; text-transform: uppercase; }}
        </style>
    </head>
    <body>
        <div class="card">
            <h3>Guest House Request Update</h3>
            <p>Request <strong>{req_id}</strong> has been:</p>
            <p><span class="badge">{outcome}</span></p>
            {f"<p><strong>Remarks:</strong> {remark}</p>" if remark else ""}
            <p>Log in to Docket portal for full details.</p>
        </div>
    </body>
    </html>
    """
    text_content = f"Your guest house request {req_id} was {outcome}. {f'Remarks: {remark}' if remark else ''}"
    return send_email(to_email, subject, html_content, text_content)
