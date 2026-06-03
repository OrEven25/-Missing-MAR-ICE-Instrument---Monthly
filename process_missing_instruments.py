"""
process_missing_instruments.py
--------------------------------
Connects to or.even@cubelogic.com's Outlook mailbox, scans the
'MAR/ICE Missing Ins' subfolder for Fortnightly Missing Mapping Check
emails for a given calendar month, extracts the Excel attachments,
converts them to CSV, saves them in the repository, and sends a
summary email with all CSVs attached.

Usage
-----
    python process_missing_instruments.py [YYYY-MM]

If YYYY-MM is omitted the previous calendar month is used.
The TARGET_MONTH environment variable can also be used.

Environment variables (can be placed in a .env file):
    EMAIL_ADDRESS   – sender / mailbox address (or.even@cubelogic.com)
    EMAIL_PASSWORD  – account password
    EXCHANGE_SERVER – optional Exchange host (uses autodiscover if blank)
    RECIPIENT_EMAIL – where to send the summary (defaults to EMAIL_ADDRESS)
    TARGET_MONTH    – override target month as YYYY-MM
"""

import io
import os
import re
import sys
import csv
import smtplib
import logging
from datetime import date, timedelta
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import openpyxl
from dotenv import load_dotenv
from exchangelib import (
    Account,
    Credentials,
    Configuration,
    DELEGATE,
    EWSDateTime,
    EWSTimeZone,
)
from exchangelib.folders import Folder

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
load_dotenv()

EMAIL_ADDRESS = os.environ["EMAIL_ADDRESS"]
EMAIL_PASSWORD = os.environ["EMAIL_PASSWORD"]
EXCHANGE_SERVER = os.getenv("EXCHANGE_SERVER", "")
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL", EMAIL_ADDRESS)

SUBFOLDER_PATH = ["MAR/ICE Missing Ins"]   # top-level subfolder name in Inbox
SUBJECT_SUFFIX = "-Fortnightly Missing Mapping Check"
SKIP_PATTERN = re.compile(r"total\s+records?\s*[:\-]?\s*0\s+rows?", re.IGNORECASE)


def get_target_month() -> date:
    """Return the first day of the target calendar month."""
    # Command-line argument takes highest priority
    if len(sys.argv) > 1:
        raw = sys.argv[1]
    else:
        raw = os.getenv("TARGET_MONTH", "")

    if raw:
        try:
            return date.fromisoformat(raw + "-01")
        except ValueError:
            log.error("Invalid TARGET_MONTH '%s'. Expected YYYY-MM.", raw)
            sys.exit(1)

    # Default: previous calendar month
    today = date.today()
    first_of_this_month = today.replace(day=1)
    return (first_of_this_month - timedelta(days=1)).replace(day=1)


def month_range(month_start: date):
    """Return (start_datetime, end_datetime) for the full calendar month."""
    tz = EWSTimeZone.localzone()
    year, month = month_start.year, month_start.month
    next_month = (month % 12) + 1
    next_year = year + (1 if month == 12 else 0)

    start = EWSDateTime(year, month, 1, 0, 0, 0, tzinfo=tz)
    end = EWSDateTime(next_year, next_month, 1, 0, 0, 0, tzinfo=tz)
    return start, end


# ---------------------------------------------------------------------------
# Mailbox helpers
# ---------------------------------------------------------------------------

def build_account() -> Account:
    """Create and return an exchangelib Account."""
    creds = Credentials(username=EMAIL_ADDRESS, password=EMAIL_PASSWORD)
    if EXCHANGE_SERVER:
        config = Configuration(server=EXCHANGE_SERVER, credentials=creds)
        account = Account(
            primary_smtp_address=EMAIL_ADDRESS,
            config=config,
            autodiscover=False,
            access_type=DELEGATE,
        )
    else:
        account = Account(
            primary_smtp_address=EMAIL_ADDRESS,
            credentials=creds,
            autodiscover=True,
            access_type=DELEGATE,
        )
    return account


def find_subfolder(account: Account, path: list[str]) -> Folder:
    """Walk the Inbox subfolder tree and return the target folder."""
    folder: Folder = account.inbox
    for name in path:
        matches = [f for f in folder.children if f.name == name]
        if not matches:
            raise FileNotFoundError(
                f"Subfolder '{name}' not found under '{folder.name}'. "
                f"Available folders: {[f.name for f in folder.children]}"
            )
        folder = matches[0]
    return folder


# ---------------------------------------------------------------------------
# Excel → CSV conversion
# ---------------------------------------------------------------------------

def excel_bytes_to_csv_bytes(excel_bytes: bytes, sheet_index: int = 0) -> bytes:
    """Convert the first sheet of an Excel workbook to CSV bytes."""
    wb = openpyxl.load_workbook(io.BytesIO(excel_bytes), read_only=True, data_only=True)
    ws = wb.worksheets[sheet_index]
    buf = io.StringIO()
    writer = csv.writer(buf)
    for row in ws.iter_rows(values_only=True):
        writer.writerow(["" if v is None else v for v in row])
    wb.close()
    return buf.getvalue().encode("utf-8")


# ---------------------------------------------------------------------------
# Output directory
# ---------------------------------------------------------------------------

def output_dir(month_start: date) -> Path:
    """Return (and create) the output directory for the given month."""
    repo_root = Path(__file__).parent
    folder = repo_root / month_start.strftime("%Y-%m")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# ---------------------------------------------------------------------------
# Email sending
# ---------------------------------------------------------------------------

def send_summary_email(csv_files: list[tuple[str, bytes]], month_start: date) -> None:
    """
    Send a summary email to RECIPIENT_EMAIL with all CSVs attached.
    Uses the Exchange/SMTP relay on port 587 (STARTTLS) at the same host
    that exchangelib connects to, or smtp.office365.com as a fallback.
    """
    month_label = month_start.strftime("%B %Y")
    subject = f"MAR/ICE Missing Instrument Mappings – {month_label}"
    body = (
        f"Hi,\n\n"
        f"Please find attached all missing instrument mapping CSVs extracted "
        f"from the Fortnightly Missing Mapping Check emails for {month_label}.\n\n"
        f"Files included ({len(csv_files)}):\n"
        + "\n".join(f"  • {name}" for name, _ in csv_files)
        + "\n\nThis email was generated automatically.\n"
    )

    msg = MIMEMultipart()
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = RECIPIENT_EMAIL
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    for filename, data in csv_files:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(data)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(part)

    smtp_host = EXCHANGE_SERVER if EXCHANGE_SERVER else "smtp.office365.com"
    log.info("Sending summary email via %s:587 …", smtp_host)
    with smtplib.SMTP(smtp_host, 587, timeout=30) as server:
        server.ehlo()
        server.starttls()
        server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        server.sendmail(EMAIL_ADDRESS, RECIPIENT_EMAIL, msg.as_string())
    log.info("Summary email sent to %s.", RECIPIENT_EMAIL)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    month_start = get_target_month()
    log.info("Target month: %s", month_start.strftime("%B %Y"))

    log.info("Connecting to mailbox %s …", EMAIL_ADDRESS)
    account = build_account()

    log.info("Navigating to subfolder: %s", " / ".join(SUBFOLDER_PATH))
    folder = find_subfolder(account, SUBFOLDER_PATH)

    start_dt, end_dt = month_range(month_start)
    log.info("Filtering emails between %s and %s", start_dt, end_dt)

    # Query emails within the target month that match the subject suffix
    emails = folder.filter(
        datetime_received__gte=start_dt,
        datetime_received__lt=end_dt,
        subject__icontains=SUBJECT_SUFFIX,
    ).order_by("datetime_received")

    out_dir = output_dir(month_start)
    collected_csvs: list[tuple[str, bytes]] = []
    skipped = 0
    processed = 0

    for email in emails:
        subject = email.subject or ""
        log.info("Examining: %s", subject)

        # Extract plain-text body (fall back to HTML body stripped of tags)
        body_text = ""
        if email.text_body:
            body_text = email.text_body
        elif email.body:
            body_text = re.sub(r"<[^>]+>", " ", email.body)

        # Skip emails with 0 records
        if SKIP_PATTERN.search(body_text):
            log.info("  → Skipping (Total records 0 rows)")
            skipped += 1
            continue

        # Extract Excel attachments
        excel_found = False
        for attachment in email.attachments:
            name = attachment.name or "attachment"
            if not name.lower().endswith((".xlsx", ".xls")):
                continue

            excel_found = True
            log.info("  → Processing attachment: %s", name)
            excel_data = attachment.content

            # Derive CSV filename: strip extension, add .csv
            stem = Path(name).stem
            csv_name = f"{stem}.csv"

            csv_data = excel_bytes_to_csv_bytes(excel_data)

            # Save to repository
            csv_path = out_dir / csv_name
            csv_path.write_bytes(csv_data)
            log.info("     Saved: %s", csv_path)

            collected_csvs.append((csv_name, csv_data))
            processed += 1

        if not excel_found:
            log.warning("  → No Excel attachment found in: %s", subject)

    log.info(
        "Done. Processed=%d  Skipped=%d  CSVs saved=%d",
        processed + skipped,
        skipped,
        len(collected_csvs),
    )

    if collected_csvs:
        send_summary_email(collected_csvs, month_start)
    else:
        log.info("No CSVs to send – summary email not sent.")


if __name__ == "__main__":
    main()
