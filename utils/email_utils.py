"""
utils/email_utils.py  —  Smart Attendance System v9.6 (E Auth Merge)
=====================================================================
Real-time OTP email sender via Gmail SMTP (TLS).

Configure in .env:
    SMTP_HOST=smtp.gmail.com
    SMTP_PORT=587
    SMTP_USER=errorbug31@gmail.com       ← sender Gmail (App Password)
    SMTP_PASS=your_16_char_app_password

Role email addresses (used as OTP destinations, set in .env):
    ADMIN_EMAIL=suganyainbox25@gmail.com
    HOD_EMAIL=suganyainbox32@gmail.com
    FACULTY_DEFAULT_EMAIL=g3260998@gmail.com

Bug-7 FIX: send_attendance_alert_email now logs SMTP failures instead of
           silently swallowing them with a bare `except: pass`.
=====================================================================
"""

import os
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

log = logging.getLogger(__name__)

# ── SMTP credentials (always re-read from env so .env changes apply) ──
def _smtp_cfg():
    return {
        "host": os.environ.get("SMTP_HOST", "smtp.gmail.com"),
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": os.environ.get("SMTP_USER", "errorbug31@gmail.com").strip(),
        "pass": os.environ.get("SMTP_PASS", "").strip(),
    }

FROM_NAME = "EduTrack Pro — Smart Attendance"


def _get_smtp_credentials():
    """Return (user, password) from environment. Raises RuntimeError if missing."""
    cfg = _smtp_cfg()
    if not cfg["user"] or not cfg["pass"]:
        raise RuntimeError(
            "SMTP credentials not configured. "
            "Set SMTP_USER and SMTP_PASS in your .env file."
        )
    return cfg["user"], cfg["pass"], cfg["host"], cfg["port"]


def send_otp_email(to_email: str, otp: str) -> None:
    """
    Send a password-reset OTP email via Gmail SMTP (TLS).

    Raises RuntimeError if SMTP credentials are not configured.
    Raises smtplib.SMTPException on send failure (caller should handle).
    """
    smtp_user, smtp_pass, smtp_host, smtp_port = _get_smtp_credentials()

    subject = "EduTrack Pro — Your Password Reset OTP"
    html_body = f"""
    <div style="font-family:'Segoe UI',sans-serif;max-width:480px;margin:0 auto;
                background:#f8fafc;border-radius:16px;padding:32px;">
      <div style="text-align:center;margin-bottom:24px;">
        <h2 style="background:linear-gradient(135deg,#06b6d4,#3b82f6);
                   -webkit-background-clip:text;-webkit-text-fill-color:transparent;
                   font-size:24px;font-weight:800;margin:0;">EduTrack Pro</h2>
        <p style="color:#64748b;font-size:13px;margin:4px 0 0;">
          Smart Attendance &amp; Face Recognition System
        </p>
      </div>
      <div style="background:#fff;border-radius:12px;padding:28px;
                  border:1.5px solid #e2e8f0;text-align:center;">
        <p style="font-size:16px;font-weight:600;color:#0f172a;margin-bottom:8px;">
          Your Password Reset OTP
        </p>
        <p style="color:#64748b;font-size:13px;margin-bottom:20px;">
          This code expires in <strong>10 minutes</strong>.
          Do not share it with anyone.
        </p>
        <div style="font-size:36px;font-weight:800;letter-spacing:10px;
                    color:#06b6d4;background:#f0fdfe;border-radius:10px;
                    padding:16px 24px;display:inline-block;">{otp}</div>
        <p style="color:#94a3b8;font-size:12px;margin-top:20px;">
          If you did not request a password reset, please ignore this email.
        </p>
      </div>
      <p style="text-align:center;color:#cbd5e1;font-size:11px;margin-top:16px;">
        &copy; 2024 EduTrack Pro — Smart Attendance System. All rights reserved.
      </p>
    </div>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{FROM_NAME} <{smtp_user}>"
    msg["To"]      = to_email
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, to_email, msg.as_string())

    log.info("[OTP] Email sent to %s", to_email)


def send_attendance_alert_email(
    to_email: str,
    student_name: str,
    roll: str,
    pct: float,
    department: str,
    section: str,
    recipient_type: str = "student",
) -> None:
    """
    Send attendance alert email (low attendance warning).
    recipient_type: "student" or "parent"

    Bug-7 FIX: SMTP errors are now logged instead of silently ignored.
    """
    try:
        smtp_user, smtp_pass, smtp_host, smtp_port = _get_smtp_credentials()
    except RuntimeError as e:
        log.warning("[AttendanceAlert] SMTP not configured — skipping alert to %s: %s", to_email, e)
        return

    if recipient_type == "parent":
        subject   = f"[Attendance Alert] Your ward {student_name}'s attendance is {pct:.1f}%"
        greeting  = "Dear Parent / Guardian,"
        body_line = f"Your ward {student_name} (Roll: {roll}) has {pct:.1f}% attendance."
    else:
        subject   = f"[Attendance Alert] Your attendance is critically low — {pct:.1f}%"
        greeting  = f"Dear {student_name},"
        body_line = f"Your current attendance is {pct:.1f}%, which is below the required 75%."

    html_body = f"""
    <div style="font-family:'Segoe UI',sans-serif;max-width:520px;margin:0 auto;
                background:#fff8f0;border-radius:12px;padding:28px;
                border:1.5px solid #fed7aa;">
      <h2 style="color:#c2410c;font-size:18px;margin-top:0;">
        ⚠️ Attendance Alert — EduTrack Pro
      </h2>
      <p style="color:#1e293b;">{greeting}</p>
      <p style="color:#1e293b;">{body_line}</p>
      <p style="color:#64748b;font-size:13px;">
        Department: <strong>{department}</strong> &nbsp;|&nbsp;
        Section: <strong>{section}</strong>
      </p>
      <p style="color:#1e293b;font-size:13px;">
        Please ensure regular attendance to meet the 75% minimum requirement.
        Contact your Class Incharge if you have valid reasons for absence.
      </p>
      <p style="color:#94a3b8;font-size:11px;margin-top:20px;">
        This is an automated message from EduTrack Pro Smart Attendance System.
      </p>
    </div>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{FROM_NAME} <{smtp_user}>"
    msg["To"]      = to_email
    msg.attach(MIMEText(html_body, "html"))

    # Bug-7 FIX: log errors instead of silently swallowing them
    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, to_email, msg.as_string())
        log.info("[AttendanceAlert] Sent to %s (%s)", to_email, recipient_type)
    except smtplib.SMTPAuthenticationError:
        log.error(
            "[AttendanceAlert] SMTP authentication failed sending to %s. "
            "Check SMTP_USER / SMTP_PASS in .env (must be a Gmail App Password).",
            to_email,
        )
    except smtplib.SMTPRecipientsRefused:
        log.error("[AttendanceAlert] Recipient refused by SMTP server: %s", to_email)
    except smtplib.SMTPException as exc:
        log.error("[AttendanceAlert] SMTP error sending to %s: %s", to_email, exc)
    except Exception as exc:
        log.error("[AttendanceAlert] Unexpected error sending to %s: %s", to_email, exc)
