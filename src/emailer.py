import os
import smtplib
from dataclasses import dataclass
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class EmailResult:
    success: bool
    recipient: str | None = None
    error: str | None = None


def send_report_email(
    report_path: str, recipient: str | None = None, verbose: bool = False
) -> EmailResult:
    """Send a report file via Gmail SMTP with App Password."""
    load_dotenv()

    gmail_address = os.getenv("GMAIL_ADDRESS", "").strip()
    gmail_password = os.getenv("GMAIL_APP_PASSWORD", "").strip()

    if not gmail_address or not gmail_password:
        return EmailResult(
            success=False,
            error="GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set in .env",
        )

    if recipient is None:
        recipient = os.getenv("GMAIL_RECIPIENT", "").strip()
    if not recipient:
        return EmailResult(
            success=False,
            error="No recipient. Set GMAIL_RECIPIENT in .env",
        )

    # Read report content
    report_file = Path(report_path)
    if not report_file.exists():
        return EmailResult(
            success=False,
            recipient=recipient,
            error=f"Report file not found: {report_path}",
        )

    try:
        content = report_file.read_text(encoding="utf-8")
    except Exception as e:
        return EmailResult(success=False, recipient=recipient, error=f"Read error: {e}")

    # Build email
    subject = f"每日美股总结 {datetime.now().strftime('%Y-%m-%d')}"
    msg = MIMEText(content, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = gmail_address
    msg["To"] = recipient

    # Send via Gmail SMTP
    try:
        if verbose:
            print(f"  Connecting to smtp.gmail.com:587 ...")
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
            server.starttls()
            server.login(gmail_address, gmail_password)
            server.send_message(msg)
        return EmailResult(success=True, recipient=recipient)
    except Exception as e:
        return EmailResult(success=False, recipient=recipient, error=str(e))
