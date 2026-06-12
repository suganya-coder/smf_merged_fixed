# =============================================================
# email_alerts.py  —  EduTrack Smart Attendance System
# Real-time Gmail Alert Sender  (v2.1 — Bug-3/7 Fixed)
#
# Features:
#   ✅ Real Gmail SMTP with App Password
#   ✅ HTML + Plain-text email (renders beautifully on mobile)
#   ✅ Auto-loads .env file (no manual setup needed)
#   ✅ Retry logic (3 attempts before giving up)
#   ✅ Connection test function
#   ✅ Below 75% threshold enforcement
#   ✅ Sends to both student + parent in one call
#   ✅ Bulk send for all critical students
#   ✅ Detailed success/failure reporting
#   ✅ Bug-3 FIX: SMTP errors are logged — no more silent failures
#
# Gmail App Password Setup (ONE TIME):
#   1. Go to  myaccount.google.com
#   2. Security  ->  2-Step Verification  ->  Turn ON
#   3. Security  ->  App Passwords  ->  Select App: Mail  ->  Generate
#   4. Copy the 16-digit code  e.g. abcdefghijklmnop
#   5. Paste in .env:  SMTP_PASS=abcdefghijklmnop  (no spaces)
#
# .env required keys:
#   SMTP_HOST=smtp.gmail.com
#   SMTP_PORT=587
#   SMTP_USER=errorbug31@gmail.com          ← sender account
#   SMTP_PASS=your16digitapppassword
#
# Role email destinations (OTP recipients):
#   ADMIN_EMAIL=suganyainbox25@gmail.com
#   HOD_EMAIL=suganyainbox32@gmail.com
#   FACULTY_DEFAULT_EMAIL=g3260998@gmail.com
# =============================================================

import os
import time
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

log = logging.getLogger(__name__)

# ── Auto-load .env file ───────────────────────────────────────
def _load_dotenv():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val

_load_dotenv()

ATTENDANCE_THRESHOLD = 75.0
ALERT_SUBJECT        = "Urgent: Low Attendance Alert"
MAX_RETRIES          = 3


def _cfg():
    """Always re-read from environment so runtime .env changes apply."""
    return {
        "host":     os.environ.get("SMTP_HOST", "smtp.gmail.com"),
        "port":     int(os.environ.get("SMTP_PORT", 587)),
        "user":     os.environ.get("SMTP_USER", "errorbug31@gmail.com").strip(),
        "password": os.environ.get("SMTP_PASS", "").strip(),
    }


# =============================================================
# EMAIL BODY BUILDERS
# =============================================================

def _build_plain(name, sid, roll, dept, sec, pct):
    now = datetime.now().strftime("%d-%b-%Y %I:%M %p")
    return (
        f"Dear Parent and Student,\n\n"
        f"This is an important academic alert from the Smart Attendance System.\n"
        f"Your student / your son / you has LOW attendance.\n"
        f"Please try to come to college regularly.\n\n"
        f"--- Student Details ---\n"
        f"Name        : {name}\n"
        f"Student ID  : {sid}\n"
        f"Roll Number : {roll}\n"
        f"Department  : {dept}\n"
        f"Section     : {sec}\n"
        f"Attendance  : {pct}%   (Required: 75%)\n\n"
        f"Your son/daughter has low attendance.\n"
        f"Please ensure they attend college regularly to avoid academic consequences.\n\n"
        f"This is an important academic warning. Kindly take this seriously.\n"
        f"Contact your Class Incharge if you have valid reasons for absence.\n\n"
        f"Alert sent on: {now}\n\n"
        f"Regards,\n"
        f"College Administration\n"
        f"Smart Attendance System\n"
    )


def _build_html(name, sid, roll, dept, sec, pct):
    now   = datetime.now().strftime("%d-%b-%Y %I:%M %p")
    color = "#c0392b" if pct < 65 else "#e67e22"
    badge = "CRITICAL" if pct < 65 else "WARNING"
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f0f4f8;font-family:Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="padding:24px 0;">
<tr><td align="center">
<table width="560" cellpadding="0" cellspacing="0"
  style="background:#fff;border-radius:12px;box-shadow:0 4px 16px rgba(0,0,0,0.12);overflow:hidden;max-width:560px;">

  <!-- HEADER -->
  <tr>
    <td style="background:{color};padding:30px 32px;text-align:center;">
      <div style="font-size:36px;margin-bottom:8px;">&#9888;&#65039;</div>
      <h1 style="color:#fff;margin:0;font-size:22px;letter-spacing:1px;">ATTENDANCE ALERT</h1>
      <span style="display:inline-block;background:rgba(255,255,255,0.22);color:#fff;
                   font-size:12px;font-weight:bold;padding:4px 16px;border-radius:20px;margin-top:10px;">
        {badge}
      </span>
    </td>
  </tr>

  <!-- GREETING -->
  <tr>
    <td style="padding:28px 32px 10px;">
      <p style="color:#2c3e50;font-size:16px;margin:0 0 10px;">Dear <strong>Parent and Student</strong>,</p>
      <p style="color:#555;font-size:15px;margin:0;line-height:1.7;">
        This is an important academic alert from the <strong>Smart Attendance System</strong>.<br>
        Your student / your son / <strong>you has low attendance</strong>.<br>
        Please try to come to college regularly.
      </p>
    </td>
  </tr>

  <!-- ATTENDANCE BADGE -->
  <tr>
    <td style="padding:14px 32px;">
      <div style="background:{color};border-radius:10px;padding:22px;text-align:center;">
        <div style="color:rgba(255,255,255,0.80);font-size:12px;letter-spacing:2px;margin-bottom:4px;">CURRENT ATTENDANCE</div>
        <div style="color:#fff;font-size:56px;font-weight:bold;line-height:1;">{pct}%</div>
        <div style="color:rgba(255,255,255,0.85);font-size:13px;margin-top:6px;">Minimum Required: <strong>75%</strong></div>
      </div>
    </td>
  </tr>

  <!-- DETAILS TABLE -->
  <tr>
    <td style="padding:14px 32px;">
      <table width="100%" cellpadding="11" cellspacing="0"
        style="border-collapse:collapse;border:1px solid #e0e7ef;border-radius:8px;overflow:hidden;">
        <tr style="background:#eaf0fb;">
          <td colspan="2" style="color:#2c3e50;font-weight:bold;font-size:13px;letter-spacing:0.5px;">
            &#128203; STUDENT DETAILS
          </td>
        </tr>
        <tr style="border-top:1px solid #e0e7ef;">
          <td style="color:#7f8c8d;font-size:14px;width:42%;">Student Name</td>
          <td style="color:#2c3e50;font-weight:bold;font-size:14px;">{name}</td>
        </tr>
        <tr style="background:#f8fafc;border-top:1px solid #e0e7ef;">
          <td style="color:#7f8c8d;font-size:14px;">Student ID</td>
          <td style="color:#2c3e50;font-size:14px;">{sid}</td>
        </tr>
        <tr style="border-top:1px solid #e0e7ef;">
          <td style="color:#7f8c8d;font-size:14px;">Roll Number</td>
          <td style="color:#2c3e50;font-size:14px;">{roll}</td>
        </tr>
        <tr style="background:#f8fafc;border-top:1px solid #e0e7ef;">
          <td style="color:#7f8c8d;font-size:14px;">Department</td>
          <td style="color:#2c3e50;font-size:14px;">{dept}</td>
        </tr>
        <tr style="border-top:1px solid #e0e7ef;">
          <td style="color:#7f8c8d;font-size:14px;">Section</td>
          <td style="color:#2c3e50;font-size:14px;">{sec}</td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- WARNING BOX -->
  <tr>
    <td style="padding:6px 32px 20px;">
      <div style="background:#fff5f5;border-left:5px solid {color};border-radius:4px;padding:14px 18px;">
        <p style="color:{color};font-size:14px;font-weight:bold;margin:0 0 8px;">&#9888;  Important Academic Warning</p>
        <p style="color:#6b3a3a;font-size:14px;margin:0;line-height:1.7;">
          Your son/daughter has low attendance. Please ensure they attend college regularly
          to meet the mandatory attendance requirement and avoid academic consequences.<br><br>
          Contact your <strong>Class Incharge</strong> immediately if there are valid reasons for absence.
        </p>
      </div>
    </td>
  </tr>

  <!-- FOOTER -->
  <tr>
    <td style="background:#2c3e50;padding:16px 32px;text-align:center;">
      <p style="color:#95a5a6;font-size:12px;margin:0 0 4px;">Automated alert — Smart Attendance System</p>
      <p style="color:#7f8c8d;font-size:11px;margin:0;">Alert sent on {now} &nbsp;|&nbsp; College Administration</p>
    </td>
  </tr>

</table>
</td></tr>
</table>
</body>
</html>"""


# =============================================================
# CORE GMAIL SENDER (with retry)
# Bug-3 FIX: errors are now logged at every failure point
# =============================================================

def _send_via_gmail(to_addr, name, sid, roll, dept, sec, pct):
    """
    Send a single HTML+plain email via Gmail SMTP with retry logic.
    Returns {"ok": True} or {"ok": False, "error": "..."}

    Bug-3 FIX: all error paths now emit a log.error() so failures
    are visible in server logs instead of disappearing silently.
    """
    cfg = _cfg()

    if not cfg["user"] or not cfg["password"]:
        err = (
            "Gmail SMTP not configured. "
            "Set SMTP_USER=errorbug31@gmail.com and "
            "SMTP_PASS=your_16digit_app_password in your .env file."
        )
        log.error("[EmailAlert] %s", err)
        return {"ok": False, "error": err}

    msg = MIMEMultipart("alternative")
    msg["Subject"]    = ALERT_SUBJECT
    msg["From"]       = f"Smart Attendance System <{cfg['user']}>"
    msg["To"]         = to_addr
    msg["X-Priority"] = "1"
    msg.attach(MIMEText(_build_plain(name, sid, roll, dept, sec, pct), "plain", "utf-8"))
    msg.attach(MIMEText(_build_html(name, sid, roll, dept, sec, pct),  "html",  "utf-8"))

    last_error = ""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with smtplib.SMTP(cfg["host"], cfg["port"], timeout=20) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(cfg["user"], cfg["password"])
                server.sendmail(cfg["user"], [to_addr], msg.as_string())
            log.info("[EmailAlert] Sent to %s (attempt %d)", to_addr, attempt)
            return {"ok": True}

        except smtplib.SMTPAuthenticationError:
            err = (
                "Gmail authentication failed. "
                "SMTP_PASS must be a 16-digit App Password, NOT your Gmail login password. "
                "Go to: myaccount.google.com -> Security -> App Passwords"
            )
            log.error("[EmailAlert] Auth error for %s: %s", to_addr, err)
            return {"ok": False, "error": err}    # no retry on auth failure

        except smtplib.SMTPRecipientsRefused:
            err = f"Email address refused by Gmail server: {to_addr}"
            log.error("[EmailAlert] %s", err)
            return {"ok": False, "error": err}

        except Exception as e:
            last_error = f"SMTP error on attempt {attempt}/{MAX_RETRIES}: {str(e)}"
            log.warning("[EmailAlert] %s (to=%s)", last_error, to_addr)
            if attempt < MAX_RETRIES:
                time.sleep(2)

    log.error("[EmailAlert] All %d attempts failed for %s. Last error: %s",
              MAX_RETRIES, to_addr, last_error)
    return {"ok": False, "error": last_error}


# =============================================================
# PUBLIC API
# =============================================================

def test_gmail_connection() -> dict:
    """
    Test Gmail SMTP login without sending any email.
    Call this to verify .env credentials are correct.
    Returns {"ok": True/False, "message"/"error": "..."}
    """
    cfg = _cfg()
    if not cfg["user"] or not cfg["password"]:
        return {
            "ok": False,
            "error": "SMTP_USER or SMTP_PASS missing from .env"
        }
    try:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(cfg["user"], cfg["password"])
        return {
            "ok": True,
            "message": f"Gmail connection OK — ready to send from {cfg['user']}"
        }
    except smtplib.SMTPAuthenticationError:
        return {
            "ok": False,
            "error": (
                "Gmail authentication failed. "
                "Use App Password (16-digit), not regular password. "
                "myaccount.google.com -> Security -> App Passwords"
            )
        }
    except Exception as e:
        return {"ok": False, "error": f"Connection failed: {str(e)}"}


def send_low_attendance_alert(student: dict) -> dict:
    """
    Send a real-time Gmail alert to student + parent if attendance < 75%.

    Accepted field names in student dict:
        name / student_name
        student_id
        roll_number / roll
        department / dept
        section
        attendance_percentage / pct
        student_email
        parent_email

    Returns:
        {"sent": bool, "sent_to": [...], "errors": [...], "message": "..."}
    """
    pct     = float(student.get("attendance_percentage") or student.get("pct") or 0)
    name    = student.get("name") or student.get("student_name") or "Student"
    sid     = student.get("student_id", "")
    roll    = student.get("roll_number") or student.get("roll") or sid
    dept    = student.get("department") or student.get("dept", "")
    sec     = student.get("section", "")
    s_email = (student.get("student_email") or "").strip()
    p_email = (student.get("parent_email") or "").strip()

    if pct >= ATTENDANCE_THRESHOLD:
        return {
            "sent": False, "sent_to": [], "errors": [],
            "message": f"{name} has {pct}% — above 75%, no alert needed."
        }

    if not s_email and not p_email:
        return {
            "sent": False, "sent_to": [], "errors": [],
            "message": f"No registered email for {name} ({sid}) — skipped."
        }

    sent_to, errors = [], []

    if s_email:
        r = _send_via_gmail(s_email, name, sid, roll, dept, sec, pct)
        if r["ok"]:
            sent_to.append(f"student({s_email})")
        else:
            errors.append(r["error"])

    if p_email:
        r = _send_via_gmail(p_email, name, sid, roll, dept, sec, pct)
        if r["ok"]:
            sent_to.append(f"parent({p_email})")
        else:
            errors.append(r["error"])

    ok = bool(sent_to)
    return {
        "sent":    ok,
        "sent_to": sent_to,
        "errors":  errors,
        "message": (
            f"Alert sent to {', '.join(sent_to)} for {name} ({pct}% attendance)."
            if ok else
            f"Failed to send alert for {name}. Error: {'; '.join(errors)}"
        )
    }


def send_bulk_alerts(students: list) -> dict:
    """
    Send real-time Gmail alerts to ALL students with attendance < 75%.
    Returns summary with per-student detail.
    """
    total = emails_sent = skipped = failed = 0
    results = []

    for s in students:
        total  += 1
        pct     = float(s.get("attendance_percentage") or s.get("pct") or 0)
        name    = s.get("name") or "Student"
        sid     = s.get("student_id", "")
        roll    = s.get("roll_number") or s.get("roll") or sid
        dept    = s.get("department") or s.get("dept", "")
        sec     = s.get("section", "")
        s_email = (s.get("student_email") or "").strip()
        p_email = (s.get("parent_email") or "").strip()

        if pct >= ATTENDANCE_THRESHOLD:
            skipped += 1
            results.append({"student_id": sid, "name": name, "pct": pct,
                             "status": "skipped", "reason": "attendance >= 75%",
                             "sent_to": [], "errors": []})
            continue

        if not s_email and not p_email:
            skipped += 1
            results.append({"student_id": sid, "name": name, "pct": pct,
                             "status": "skipped", "reason": "no email on file",
                             "sent_to": [], "errors": []})
            continue

        severity = "critical" if pct < 65 else ("warning" if pct < 70 else "low")
        sent_to, errors = [], []

        if s_email:
            r = _send_via_gmail(s_email, name, sid, roll, dept, sec, pct)
            if r["ok"]:
                sent_to.append(f"student({s_email})")
            else:
                errors.append(r["error"])

        if p_email:
            r = _send_via_gmail(p_email, name, sid, roll, dept, sec, pct)
            if r["ok"]:
                sent_to.append(f"parent({p_email})")
            else:
                errors.append(r["error"])

        if sent_to:
            emails_sent += len(sent_to)
            status = "sent"
        else:
            failed += 1
            status = "failed"

        results.append({
            "student_id":    sid,
            "name":          name,
            "pct":           pct,
            "severity":      severity,
            "student_email": s_email,
            "parent_email":  p_email,
            "sent_to":       sent_to,
            "errors":        errors,
            "status":        status
        })

    return {
        "total":       total,
        "emails_sent": emails_sent,
        "skipped":     skipped,
        "failed":      failed,
        "results":     results,
        "message": (
            f"Real-time Gmail done: {emails_sent} emails sent "
            f"({skipped} skipped, {failed} failed) out of {total} students."
        )
    }
