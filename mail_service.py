import logging
import smtplib
import threading
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
    from_email: Optional[str] = None,
) -> bool:
    """
    Sends an email using the SMTP settings from Config.
    If SMTP_SERVER is empty or fails, logs the email safely to console/logs.
    """
    if not to_email:
        logger.warning("Attempted to send email with no recipient specified.")
        return False

    server_host = Config.SMTP_SERVER.strip()
    sender = from_email or Config.SMTP_SENDER_EMAIL

    # If no SMTP server configured, simulate sending (useful for local dev/testing)
    if not server_host:
        logger.info(
            f"[SIMULATED EMAIL] From: {sender} | To: {to_email} | Subject: '{subject}'\n"
            f"Content:\n{text_content or html_content}\n"
        )
        return True

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = sender
        if from_email:
            msg["Reply-To"] = from_email
        msg["To"] = to_email

        if text_content:
            msg.attach(MIMEText(text_content, "plain"))
        if html_content:
            msg.attach(MIMEText(html_content, "html"))

        timeout_sec = getattr(Config, "SMTP_TIMEOUT", 10.0)
        with smtplib.SMTP(server_host, Config.SMTP_PORT, timeout=timeout_sec) as server:
            if Config.SMTP_USE_TLS and server.has_extn("starttls"):
                server.starttls()
            if Config.SMTP_USERNAME and Config.SMTP_PASSWORD and server.has_extn("auth"):
                server.login(Config.SMTP_USERNAME, Config.SMTP_PASSWORD)
            server.sendmail(sender, [to_email], msg.as_string())

        logger.info(f"Email successfully delivered to {to_email} via {server_host}")
        return True

    except Exception as e:
        logger.warning(f"Primary email delivery to {to_email} via {server_host}:{Config.SMTP_PORT} failed: {e}")
        # Automatic fallback to JSW internal mail relay if primary connection failed or timed out
        if server_host.lower() != "mail.jsw.in":
            try:
                logger.info(f"Attempting automatic fallback delivery to {to_email} via internal relay mail.jsw.in:25...")
                with smtplib.SMTP("mail.jsw.in", 25, timeout=10.0) as fb_server:
                    fb_server.sendmail(sender, [to_email], msg.as_string())
                logger.info(f"Email successfully delivered to {to_email} via fallback relay mail.jsw.in")
                return True
            except Exception as fb_err:
                logger.error(f"Fallback delivery via mail.jsw.in also failed: {fb_err}")
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
    """Send request approval/rejection outcome notification."""
    subject = f"Docket - Process Request {req_id} {outcome.capitalize()}"
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
            <h3>Process Request Update</h3>
            <p>Request <strong>{req_id}</strong> has been:</p>
            <p><span class="badge">{outcome}</span></p>
            {f"<p><strong>Remarks:</strong> {remark}</p>" if remark else ""}
            <p>Log in to Docket portal for full details.</p>
        </div>
    </body>
    </html>
    """
    text_content = f"Your request {req_id} was {outcome}. {f'Remarks: {remark}' if remark else ''}"
    return send_email(to_email, subject, html_content, text_content)


def send_request_outcome_email_async(to_email: str, req_id: str, outcome: str, remark: str = "") -> None:
    """Send outcome email asynchronously in a background thread to prevent UI latency."""
    t = threading.Thread(target=send_request_outcome_email, args=(to_email, req_id, outcome, remark), daemon=True)
    t.start()


def send_final_approval_email(
    to_email: str,
    recipient_name: str,
    is_admin_department: bool,
    req_summary: dict,
) -> bool:
    """
    Sends rich, formal final approval email for a request.
    Differentiates between End User copy and Admin Department notification copy.
    """
    req_id = req_summary.get("req_id", "N/A")
    workflow_name = req_summary.get("workflow_name", "Approval Process")
    title = req_summary.get("title", "Request")
    applicant_name = req_summary.get("applicant_name", "Employee")
    applicant_emp_id = req_summary.get("applicant_emp_id", "")
    applicant_dept = req_summary.get("applicant_dept", "")
    applicant_phone = req_summary.get("applicant_phone", "")
    start_date = req_summary.get("start_date", "")
    end_date = req_summary.get("end_date", "")
    purpose = req_summary.get("purpose", "")
    approved_by = req_summary.get("approved_by", "Approving Authority")
    approved_at = req_summary.get("approved_at", "")
    remarks = req_summary.get("remarks", "")
    form_fields = req_summary.get("form_fields", [])

    if is_admin_department:
        subject = f"Docket [Admin Action Required] - Final Approved: {req_id} - {workflow_name} ({applicant_name})"
        headline_title = "Admin Department Notification: Final Approval Granted"
        intro_text = (
            f"Hello <strong>{recipient_name}</strong> (Admin Department),<br><br>"
            f"The following process request <strong>{req_id}</strong> submitted by <strong>{applicant_name}</strong> "
            f"has received <strong>FINAL APPROVAL</strong>. Please find the complete requisition details below "
            f"to carry out required logistics, facility allocation, scheduling, and official record-keeping."
        )
        footer_action_note = "This notification has been forwarded automatically to the Admin Department for official action and tracking."
    else:
        subject = f"Docket - Final Approval Granted for Request {req_id}: {workflow_name}"
        headline_title = "Process Request Approved"
        approved_by_str = f" from <strong>{approved_by}</strong>" if (approved_by and approved_by != "Approving Authority") else ""
        intro_text = (
            f"Hello <strong>{recipient_name}</strong>,<br><br>"
            f"Good news! Your request <strong>{req_id}</strong> for <strong>{workflow_name}</strong> has received "
            f"<strong>FINAL APPROVAL</strong>{approved_by_str}."
        )
        footer_action_note = "The Admin Department has been officially notified to proceed with any necessary logistical arrangements."

    # Build HTML rows for form fields if available
    custom_fields_html = ""
    if form_fields:
        field_rows = []
        for f in form_fields:
            lbl = f.get("label") or f.get("name") or "Field"
            val = f.get("value")
            if val is None or val == "":
                continue
            if isinstance(val, bool):
                val = "Yes" if val else "No"
            field_rows.append(f"""
                <tr>
                    <td style="padding: 8px 12px; font-size: 13px; font-weight: 600; color: #475569; background: #f8fafc; border-bottom: 1px solid #e2e8f0; width: 38%;">{lbl}</td>
                    <td style="padding: 8px 12px; font-size: 13px; color: #1e293b; border-bottom: 1px solid #e2e8f0;">{val}</td>
                </tr>
            """)
        if field_rows:
            custom_fields_html = f"""
            <div style="margin-top: 20px;">
                <h4 style="margin: 0 0 10px; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px; color: #0756b9; border-bottom: 2px solid #e2e8f0; padding-bottom: 6px;">Submitted Form Particulars</h4>
                <table style="width: 100%; border-collapse: collapse; border: 1px solid #e2e8f0; border-radius: 6px; overflow: hidden;">
                    {"".join(field_rows)}
                </table>
            </div>
            """

    dates_row = ""
    if start_date or end_date:
        dates_val = f"{start_date} to {end_date}" if (start_date and end_date and start_date != end_date) else (start_date or end_date)
        dates_row = f"""
        <tr>
            <td style="padding: 8px 12px; font-size: 13px; font-weight: 600; color: #475569; background: #f8fafc; border-bottom: 1px solid #e2e8f0;">Schedule / Dates</td>
            <td style="padding: 8px 12px; font-size: 13px; color: #1e293b; border-bottom: 1px solid #e2e8f0;">{dates_val}</td>
        </tr>
        """

    purpose_row = ""
    if purpose:
        purpose_row = f"""
        <tr>
            <td style="padding: 8px 12px; font-size: 13px; font-weight: 600; color: #475569; background: #f8fafc; border-bottom: 1px solid #e2e8f0;">Business Justification</td>
            <td style="padding: 8px 12px; font-size: 13px; color: #1e293b; border-bottom: 1px solid #e2e8f0;">{purpose}</td>
        </tr>
        """

    remarks_html = ""
    if remarks:
        remarks_html = f"""
        <div style="margin-top: 18px; padding: 12px 16px; background: #ecfdf5; border-left: 4px solid #10b981; border-radius: 4px;">
            <div style="font-size: 12px; font-weight: 700; color: #065f46; text-transform: uppercase; margin-bottom: 4px;">Approver Remarks:</div>
            <div style="font-size: 13.5px; color: #047857; font-style: italic;">"{remarks}"</div>
        </div>
        """

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f1f5f9; margin: 0; padding: 24px; color: #1e293b; }}
            .container {{ max-width: 620px; margin: 0 auto; background: #ffffff; border-radius: 10px; border: 1px solid #cbd5e1; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.05); }}
            .header {{ background: #0756b9; color: #ffffff; padding: 22px 28px; }}
            .header-badge {{ display: inline-block; background: #10b981; color: #ffffff; font-size: 11px; font-weight: 800; letter-spacing: 1px; padding: 4px 10px; border-radius: 9999px; text-transform: uppercase; margin-bottom: 10px; }}
            .header-title {{ margin: 0; font-size: 20px; font-weight: 700; }}
            .header-sub {{ margin: 4px 0 0 0; font-size: 13px; opacity: 0.9; }}
            .body {{ padding: 28px; }}
            .intro {{ font-size: 14px; line-height: 1.6; color: #334155; margin-bottom: 22px; }}
            .meta-table {{ width: 100%; border-collapse: collapse; border: 1px solid #e2e8f0; border-radius: 6px; overflow: hidden; margin-top: 12px; }}
            .footer {{ background: #f8fafc; border-top: 1px solid #e2e8f0; padding: 18px 28px; font-size: 12px; color: #64748b; line-height: 1.5; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <span class="header-badge">&#10004; FINAL APPROVAL GRANTED</span>
                <h2 class="header-title">{headline_title}</h2>
                <p class="header-sub">JSW Dharamtar Port &middot; Docket Requisition System</p>
            </div>
            <div class="body">
                <div class="intro">{intro_text}</div>

                <h4 style="margin: 0 0 10px; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px; color: #0756b9; border-bottom: 2px solid #e2e8f0; padding-bottom: 6px;">Request Summary</h4>
                <table class="meta-table">
                    <tr>
                        <td style="padding: 8px 12px; font-size: 13px; font-weight: 600; color: #475569; background: #f8fafc; border-bottom: 1px solid #e2e8f0; width: 38%;">Request ID</td>
                        <td style="padding: 8px 12px; font-size: 13px; font-weight: 700; color: #0756b9; border-bottom: 1px solid #e2e8f0;">{req_id}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 12px; font-size: 13px; font-weight: 600; color: #475569; background: #f8fafc; border-bottom: 1px solid #e2e8f0;">Process / Workflow</td>
                        <td style="padding: 8px 12px; font-size: 13px; color: #1e293b; border-bottom: 1px solid #e2e8f0;">{workflow_name}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 12px; font-size: 13px; font-weight: 600; color: #475569; background: #f8fafc; border-bottom: 1px solid #e2e8f0;">Subject / Title</td>
                        <td style="padding: 8px 12px; font-size: 13px; color: #1e293b; border-bottom: 1px solid #e2e8f0;">{title}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 12px; font-size: 13px; font-weight: 600; color: #475569; background: #f8fafc; border-bottom: 1px solid #e2e8f0;">Applicant</td>
                        <td style="padding: 8px 12px; font-size: 13px; color: #1e293b; border-bottom: 1px solid #e2e8f0;">{applicant_name} ({applicant_emp_id or 'Staff'})</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 12px; font-size: 13px; font-weight: 600; color: #475569; background: #f8fafc; border-bottom: 1px solid #e2e8f0;">Applicant Department</td>
                        <td style="padding: 8px 12px; font-size: 13px; color: #1e293b; border-bottom: 1px solid #e2e8f0;">{applicant_dept or 'N/A'}</td>
                    </tr>
                    {dates_row}
                    {purpose_row}
                    <tr>
                        <td style="padding: 8px 12px; font-size: 13px; font-weight: 600; color: #475569; background: #f8fafc; border-bottom: 1px solid #e2e8f0;">Final Approver</td>
                        <td style="padding: 8px 12px; font-size: 13px; color: #1e293b; border-bottom: 1px solid #e2e8f0;"><strong>{approved_by}</strong></td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 12px; font-size: 13px; font-weight: 600; color: #475569; background: #f8fafc;">Approval Timestamp</td>
                        <td style="padding: 8px 12px; font-size: 13px; color: #1e293b;">{approved_at or 'Just now'}</td>
                    </tr>
                </table>

                {remarks_html}
                {custom_fields_html}
            </div>
            <div class="footer">
                <p style="margin: 0 0 6px 0;"><strong>Notice:</strong> {footer_action_note}</p>
                <p style="margin: 0; color: #94a3b8; font-size: 11px;">This is an automated system notification from JSW Dharamtar Port Docket. Please do not reply directly to this email.</p>
            </div>
        </div>
    </body>
    </html>
    """

    # Plain text alternative
    text_content = (
        f"{headline_title}\n\n"
        f"Request ID: {req_id}\n"
        f"Process: {workflow_name}\n"
        f"Title: {title}\n"
        f"Applicant: {applicant_name} ({applicant_dept})\n"
        f"Approved By: {approved_by} at {approved_at}\n"
        f"Remarks: {remarks or 'None'}\n\n"
        f"{footer_action_note}\n"
    )

    return send_email(to_email, subject, html_content, text_content)


def notify_final_approval_async(
    req_summary: dict,
    end_user_email: Optional[str] = None,
    end_user_name: Optional[str] = None,
    admin_email: Optional[str] = None,
    admin_name: Optional[str] = None,
) -> None:
    """Dispatches final approval emails to end user and admin department asynchronously."""
    def _worker():
        # 1. Email the end user
        if end_user_email and end_user_email.strip():
            u_email = end_user_email.strip()
            u_name = end_user_name or "Applicant"
            logger.info(f"Dispatching final approval email to End User: {u_email} ({u_name})")
            send_final_approval_email(
                to_email=u_email,
                recipient_name=u_name,
                is_admin_department=False,
                req_summary=req_summary,
            )

        # 2. Email the one employee from the Admin Department
        if admin_email and admin_email.strip():
            a_email = admin_email.strip()
            a_name = admin_name or "Admin Officer"
            logger.info(f"Dispatching final approval email to Admin Department employee: {a_email} ({a_name})")
            send_final_approval_email(
                to_email=a_email,
                recipient_name=a_name,
                is_admin_department=True,
                req_summary=req_summary,
            )

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# Vehicle Booking Notification Emails
# ---------------------------------------------------------------------------
def send_vehicle_arrangement_alert(
    to_email: str = "dppl.admin@jsw.in",
    req_summary: Optional[dict] = None,
) -> bool:
    """
    Sends an urgent email to dppl.admin@jsw.in requesting to arrange the car
    once final approval has been granted for a vehicle booking request.
    """
    req_summary = req_summary or {}
    req_id = req_summary.get("req_id", "REQ")
    workflow_name = req_summary.get("workflow_name", "Vehicle Booking")
    title = req_summary.get("title", "")
    applicant_name = req_summary.get("applicant_name", "Applicant")
    applicant_emp_id = req_summary.get("applicant_emp_id", "")
    applicant_dept = req_summary.get("applicant_dept", "")
    applicant_phone = req_summary.get("applicant_phone", "")
    start_date = req_summary.get("start_date", "")
    end_date = req_summary.get("end_date", "")
    purpose = req_summary.get("purpose", "")
    approved_by = req_summary.get("approved_by", "Approving Authority")
    approved_at = req_summary.get("approved_at", "")
    form_fields = req_summary.get("form_fields", [])

    subject = f"Docket - PLEASE ARRANGE THE CAR: Approved Vehicle Booking [{req_id}] ({applicant_name})"

    # Form fields HTML
    custom_fields_html = ""
    if form_fields:
        field_rows = []
        for f in form_fields:
            lbl = f.get("label") or f.get("name") or "Field"
            val = f.get("value")
            if val is None or val == "":
                continue
            if isinstance(val, bool):
                val = "Yes" if val else "No"
            field_rows.append(f"""
                <tr>
                    <td style="padding: 8px 12px; font-size: 13px; font-weight: 600; color: #475569; background: #f8fafc; border-bottom: 1px solid #e2e8f0; width: 38%;">{lbl}</td>
                    <td style="padding: 8px 12px; font-size: 13px; color: #1e293b; border-bottom: 1px solid #e2e8f0;">{val}</td>
                </tr>
            """)
        if field_rows:
            custom_fields_html = f"""
            <div style="margin-top: 20px;">
                <h4 style="margin: 0 0 10px; font-size: 13px; text-transform: uppercase; letter-spacing: 0.5px; color: #0756b9; border-bottom: 2px solid #e2e8f0; padding-bottom: 6px;">Passenger / Trip Requirements</h4>
                <table style="width: 100%; border-collapse: collapse; border: 1px solid #e2e8f0; border-radius: 6px; overflow: hidden;">
                    {"".join(field_rows)}
                </table>
            </div>
            """

    dates_val = f"{start_date} to {end_date}" if (start_date and end_date and start_date != end_date) else (start_date or end_date or "As per schedule")
    approved_phrase = f" from <strong>{approved_by}</strong>" if (approved_by and approved_by != "Approving Authority") else ""

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f1f5f9; margin: 0; padding: 24px; }}
            .container {{ max-width: 640px; margin: 0 auto; background: #ffffff; border-radius: 12px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 4px 16px rgba(15, 23, 42, 0.05); }}
            .header {{ background: linear-gradient(135deg, #0756b9 0%, #032d69 100%); padding: 24px 32px; color: #ffffff; }}
            .alert-banner {{ background: #fef3c7; border-left: 5px solid #d97706; padding: 16px 20px; margin: 24px 32px 16px; border-radius: 6px; }}
            .body-content {{ padding: 0 32px 32px; }}
            .details-table {{ width: 100%; border-collapse: collapse; margin-top: 14px; border: 1px solid #e2e8f0; border-radius: 6px; overflow: hidden; }}
            .footer {{ background: #f8fafc; padding: 18px 32px; font-size: 12px; color: #64748b; border-top: 1px solid #e2e8f0; }}
            .btn {{ display: inline-block; background: #0756b9; color: #ffffff !important; padding: 12px 24px; border-radius: 6px; font-size: 14px; font-weight: 700; text-decoration: none; margin-top: 20px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <table style="width: 100%;">
                    <tr>
                        <td>
                            <span style="font-size: 11px; font-weight: 800; letter-spacing: 1.5px; text-transform: uppercase; color: #93c5fd;">JSW DHARAMTAR PORT &bull; TRANSPORT DESK</span>
                            <h2 style="margin: 4px 0 0 0; font-size: 22px; font-weight: 800; color: #ffffff;">Action Required: Please Arrange the Car</h2>
                        </td>
                        <td style="text-align: right;">
                            <span style="background: #ef4444; color: #ffffff; padding: 5px 12px; border-radius: 20px; font-size: 11px; font-weight: 800; letter-spacing: 0.5px;">URGENT</span>
                        </td>
                    </tr>
                </table>
            </div>

            <div class="alert-banner">
                <div style="font-size: 15px; font-weight: 800; color: #92400e; margin-bottom: 4px;">PLEASE ARRANGE THE CAR</div>
                <div style="font-size: 13.5px; color: #78350f; line-height: 1.5;">
                    The vehicle booking request <strong>{req_id}</strong> for <strong>{applicant_name}</strong> has received <strong>FINAL APPROVAL</strong>{approved_phrase}.<br>
                    Please arrange the designated vehicle and driver, then enter the vehicle particulars in Docket Portal.
                </div>
            </div>

            <div class="body-content">
                <h4 style="margin: 20px 0 10px; font-size: 13px; text-transform: uppercase; letter-spacing: 0.5px; color: #0756b9; border-bottom: 2px solid #e2e8f0; padding-bottom: 6px;">Approved Requisition Summary</h4>
                <table class="details-table">
                    <tr>
                        <td style="padding: 8px 12px; font-size: 13px; font-weight: 600; color: #475569; background: #f8fafc; border-bottom: 1px solid #e2e8f0; width: 38%;">Request ID</td>
                        <td style="padding: 8px 12px; font-size: 13px; color: #1e293b; border-bottom: 1px solid #e2e8f0;"><strong>{req_id}</strong></td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 12px; font-size: 13px; font-weight: 600; color: #475569; background: #f8fafc; border-bottom: 1px solid #e2e8f0;">Process Type</td>
                        <td style="padding: 8px 12px; font-size: 13px; color: #1e293b; border-bottom: 1px solid #e2e8f0;">{workflow_name}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 12px; font-size: 13px; font-weight: 600; color: #475569; background: #f8fafc; border-bottom: 1px solid #e2e8f0;">Applicant</td>
                        <td style="padding: 8px 12px; font-size: 13px; color: #1e293b; border-bottom: 1px solid #e2e8f0;"><strong>{applicant_name}</strong> ({applicant_emp_id or 'Staff'})</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 12px; font-size: 13px; font-weight: 600; color: #475569; background: #f8fafc; border-bottom: 1px solid #e2e8f0;">Department</td>
                        <td style="padding: 8px 12px; font-size: 13px; color: #1e293b; border-bottom: 1px solid #e2e8f0;">{applicant_dept or 'N/A'}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 12px; font-size: 13px; font-weight: 600; color: #475569; background: #f8fafc; border-bottom: 1px solid #e2e8f0;">Contact Number</td>
                        <td style="padding: 8px 12px; font-size: 13px; color: #1e293b; border-bottom: 1px solid #e2e8f0;">{applicant_phone or 'N/A'}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 12px; font-size: 13px; font-weight: 600; color: #475569; background: #f8fafc; border-bottom: 1px solid #e2e8f0;">Date of Travel</td>
                        <td style="padding: 8px 12px; font-size: 13px; color: #1e293b; border-bottom: 1px solid #e2e8f0;"><strong>{dates_val}</strong></td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 12px; font-size: 13px; font-weight: 600; color: #475569; background: #f8fafc; border-bottom: 1px solid #e2e8f0;">Business Purpose</td>
                        <td style="padding: 8px 12px; font-size: 13px; color: #1e293b; border-bottom: 1px solid #e2e8f0;">{purpose or 'Official Transport'}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 12px; font-size: 13px; font-weight: 600; color: #475569; background: #f8fafc;">Final Approved By</td>
                        <td style="padding: 8px 12px; font-size: 13px; color: #1e293b;">{approved_by} at {approved_at or 'Today'}</td>
                    </tr>
                </table>

                {custom_fields_html}

                <div style="text-align: center; margin-top: 26px;">
                    <a href="http://10.5.52.128:5000/admin/approvals" class="btn" style="color:#ffffff;">
                        &rarr; Open Portal to Allocate Vehicle Details
                    </a>
                </div>
            </div>

            <div class="footer">
                <p style="margin: 0 0 6px 0;"><strong>Recipient:</strong> dppl.admin@jsw.in &bull; DPPL Transport &amp; Administration</p>
                <p style="margin: 0; font-size: 11px; color: #94a3b8;">Automated action trigger from JSW Dharamtar Port Docket System.</p>
            </div>
        </div>
    </body>
    </html>
    """

    text_content = (
        f"ACTION REQUIRED: PLEASE ARRANGE THE CAR\n\n"
        f"A vehicle booking request has received final approval.\n"
        f"Request ID: {req_id}\n"
        f"Applicant: {applicant_name} ({applicant_dept})\n"
        f"Date: {dates_val}\n"
        f"Purpose: {purpose}\n"
        f"Approved By: {approved_by}\n\n"
        f"Please log in to Docket Portal to assign the vehicle and driver details:\n"
        f"http://10.5.52.128:5000/admin/approvals\n"
    )

    return send_email(to_email=to_email, subject=subject, html_content=html_content, text_content=text_content)


def send_vehicle_details_to_applicant_email(
    to_email: str,
    applicant_name: str,
    req_id: str,
    vehicle_details: list,
    req_summary: Optional[dict] = None,
    from_email: str = "dppl.admin@jsw.in",
) -> bool:
    """
    Sends the allocated vehicle and driver details to the applicant whose
    request was approved, dispatched from dppl.admin@jsw.in.
    """
    if not to_email:
        logger.warning(f"Cannot send vehicle details for {req_id}: no applicant email.")
        return False

    req_summary = req_summary or {}
    workflow_name = req_summary.get("workflow_name", "Vehicle Booking")
    start_date = req_summary.get("start_date", "")
    end_date = req_summary.get("end_date", "")
    dates_val = f"{start_date} to {end_date}" if (start_date and end_date and start_date != end_date) else (start_date or end_date or "As scheduled")

    subject = f"Docket - Vehicle Arranged: Your Car & Driver Details for Request [{req_id}]"

    # Build rows of vehicle allocation particulars
    rows_html = []
    text_details_list = []
    for item in vehicle_details:
        lbl = item.get("label") or item.get("name") or "Detail"
        val = item.get("value") or "—"
        # Highlight important items
        is_highlight = any(k in lbl.lower() for k in ("vehicle no", "driver name", "driver contact", "mobile", "reporting"))
        val_style = "font-weight: 800; color: #0f172a; font-size: 14px;" if is_highlight else "color: #1e293b; font-size: 13px;"

        rows_html.append(f"""
            <tr>
                <td style="padding: 10px 14px; font-size: 13px; font-weight: 600; color: #475569; background: #f8fafc; border-bottom: 1px solid #e2e8f0; width: 38%;">{lbl}</td>
                <td style="padding: 10px 14px; {val_style} border-bottom: 1px solid #e2e8f0;">{val}</td>
            </tr>
        """)
        text_details_list.append(f"  * {lbl}: {val}")

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f1f5f9; margin: 0; padding: 24px; }}
            .container {{ max-width: 620px; margin: 0 auto; background: #ffffff; border-radius: 12px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 4px 16px rgba(15, 23, 42, 0.05); }}
            .header {{ background: linear-gradient(135deg, #059669 0%, #064e3b 100%); padding: 24px 32px; color: #ffffff; }}
            .success-banner {{ background: #ecfdf5; border-left: 5px solid #10b981; padding: 16px 20px; margin: 24px 32px 16px; border-radius: 6px; }}
            .body-content {{ padding: 0 32px 32px; }}
            .details-table {{ width: 100%; border-collapse: collapse; margin-top: 14px; border: 1px solid #e2e8f0; border-radius: 6px; overflow: hidden; }}
            .footer {{ background: #f8fafc; padding: 18px 32px; font-size: 12px; color: #64748b; border-top: 1px solid #e2e8f0; }}
            .help-card {{ background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 8px; padding: 14px 18px; margin-top: 22px; font-size: 13px; color: #1e3a8a; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <span style="font-size: 11px; font-weight: 800; letter-spacing: 1.5px; text-transform: uppercase; color: #a7f3d0;">JSW DHARAMTAR PORT &bull; TRANSPORT ALLOCATION</span>
                <h2 style="margin: 4px 0 0 0; font-size: 22px; font-weight: 800; color: #ffffff;">Your Car Has Been Arranged</h2>
            </div>

            <div class="success-banner">
                <div style="font-size: 15px; font-weight: 800; color: #065f46; margin-bottom: 4px;">VEHICLE ALLOCATION CONFIRMED</div>
                <div style="font-size: 13.5px; color: #047857; line-height: 1.5;">
                    Hello <strong>{applicant_name}</strong>,<br>
                    Your vehicle booking request <strong>{req_id}</strong> has been processed by DPPL Admin. Your assigned vehicle and driver particulars are confirmed below.
                </div>
            </div>

            <div class="body-content">
                <h4 style="margin: 18px 0 10px; font-size: 13px; text-transform: uppercase; letter-spacing: 0.5px; color: #059669; border-bottom: 2px solid #e2e8f0; padding-bottom: 6px;">Allocated Vehicle &amp; Driver Details</h4>
                <table class="details-table">
                    <tr>
                        <td style="padding: 10px 14px; font-size: 13px; font-weight: 600; color: #475569; background: #f8fafc; border-bottom: 1px solid #e2e8f0; width: 38%;">Request Reference</td>
                        <td style="padding: 10px 14px; font-size: 13px; color: #1e293b; border-bottom: 1px solid #e2e8f0;"><strong>{req_id}</strong> ({workflow_name})</td>
                    </tr>
                    <tr>
                        <td style="padding: 10px 14px; font-size: 13px; font-weight: 600; color: #475569; background: #f8fafc; border-bottom: 1px solid #e2e8f0;">Travel Date</td>
                        <td style="padding: 10px 14px; font-size: 13px; color: #1e293b; border-bottom: 1px solid #e2e8f0;">{dates_val}</td>
                    </tr>
                    {"".join(rows_html)}
                </table>

                <div class="help-card">
                    <strong>Need Help or Schedule Change?</strong><br>
                    If you have questions or your schedule changes, please contact the DPPL Admin Team directly at <a href="mailto:dppl.admin@jsw.in" style="color: #0756b9; font-weight: 700;">dppl.admin@jsw.in</a>.
                </div>
            </div>

            <div class="footer">
                <p style="margin: 0 0 6px 0;"><strong>Dispatched By:</strong> {from_email} &bull; DPPL Administration &amp; Transport Desk</p>
                <p style="margin: 0; font-size: 11px; color: #94a3b8;">Automated notification from JSW Dharamtar Port Docket System.</p>
            </div>
        </div>
    </body>
    </html>
    """

    text_content = (
        f"VEHICLE ARRANGED & CONFIRMED\n\n"
        f"Hello {applicant_name},\n"
        f"Your vehicle booking request {req_id} has been processed by DPPL Admin ({from_email}).\n\n"
        f"ALLOCATED VEHICLE DETAILS:\n"
        + "\n".join(text_details_list)
        + f"\n\nTravel Date: {dates_val}\n"
        f"If you need changes or assistance, contact {from_email}.\n"
    )

    return send_email(
        to_email=to_email,
        subject=subject,
        html_content=html_content,
        text_content=text_content,
        from_email=from_email,
    )


def notify_vehicle_arrangement_async(req_summary: dict) -> None:
    """Sends async alert to dppl.admin@jsw.in requesting car arrangement."""
    def _worker():
        logger.info(f"Dispatching 'PLEASE ARRANGE THE CAR' email to dppl.admin@jsw.in for {req_summary.get('req_id')}")
        send_vehicle_arrangement_alert(to_email="dppl.admin@jsw.in", req_summary=req_summary)
    t = threading.Thread(target=_worker, daemon=True)
    t.start()


def notify_vehicle_details_async(
    to_email: str,
    applicant_name: str,
    req_id: str,
    vehicle_details: list,
    req_summary: Optional[dict] = None,
) -> None:
    """Sends async confirmation email with vehicle details from dppl.admin@jsw.in to the applicant."""
    def _worker():
        logger.info(f"Dispatching vehicle details email to applicant {to_email} from dppl.admin@jsw.in for {req_id}")
        send_vehicle_details_to_applicant_email(
            to_email=to_email,
            applicant_name=applicant_name,
            req_id=req_id,
            vehicle_details=vehicle_details,
            req_summary=req_summary,
            from_email="dppl.admin@jsw.in",
        )
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
