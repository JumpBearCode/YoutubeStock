import os
import smtplib
from dataclasses import dataclass
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

import markdown
from dotenv import load_dotenv

EMAIL_CSS = """\
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 15px;
    line-height: 1.6;
    color: #333;
    max-width: 800px;
    margin: 0 auto;
    padding: 20px;
}
h1 {
    color: #1a1a2e;
    border-bottom: 2px solid #e94560;
    padding-bottom: 8px;
    font-size: 22px;
}
h2 {
    color: #16213e;
    border-bottom: 1px solid #ddd;
    padding-bottom: 6px;
    font-size: 18px;
    margin-top: 24px;
}
h3 {
    color: #0f3460;
    font-size: 16px;
}
table {
    border-collapse: collapse;
    width: 100%;
    margin: 12px 0;
}
th {
    background-color: #16213e;
    color: #fff;
    padding: 10px 12px;
    text-align: left;
    font-size: 14px;
}
td {
    padding: 8px 12px;
    border: 1px solid #ddd;
    font-size: 14px;
}
tr:nth-child(even) {
    background-color: #f8f9fa;
}
tr:hover {
    background-color: #e8f4f8;
}
hr {
    border: none;
    border-top: 2px solid #e94560;
    margin: 32px 0;
}
strong {
    color: #1a1a2e;
}
p {
    margin: 8px 0;
}
"""


def _markdown_to_html(content: str) -> str:
    """Convert Markdown content to a full HTML document with inline CSS for email."""
    html_body = markdown.markdown(content, extensions=["tables"])
    return f"""\
<html>
<head>
<meta charset="utf-8">
<style>
{EMAIL_CSS}
</style>
</head>
<body>
{html_body}
</body>
</html>"""


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

    # Convert Markdown to HTML
    html_content = _markdown_to_html(content)

    # Build email
    subject = f"每日美股总结 {datetime.now().strftime('%Y-%m-%d')}"
    msg = MIMEText(html_content, "html", "utf-8")
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
