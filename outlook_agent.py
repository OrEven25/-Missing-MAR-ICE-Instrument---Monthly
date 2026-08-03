"""
Outlook Agent - MAR/ICE Missing Ins Processor (Microsoft Graph API edition)
Runs on the last Friday of each month via Windows Task Scheduler.
Authenticates via Azure app credentials — no Outlook desktop app required.
"""

import re
import os
import sys
import base64
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from calendar import monthcalendar, FRIDAY
from datetime import datetime, date, timezone
from pathlib import Path

import msal
import requests
import pandas as pd

from graph_config import TENANT_ID, CLIENT_ID, USER_EMAIL

TOKEN_CACHE_FILE = r"C:\Users\or.even\outlook_agent_token_cache.bin"
SCOPES = [
    "https://graph.microsoft.com/Mail.Read",
    "https://graph.microsoft.com/Mail.Send",
]


RECIPIENTS = ["InterfacesTeam@cubelogic.com", "or.even@cubelogic.com", "abinash.barik@cubelogic.com"]
TARGET_SUBFOLDER = "MAR/ICE Missing Ins"
SUBJECT_PATTERN = re.compile(r"^.+-Fortnightly Missing Mapping Check$", re.IGNORECASE)
SKIP_BODY_PHRASE = "Total records 0 rows"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
SUPPORTED_EXTENSIONS = (".xlsx", ".xls", ".xlsm", ".csv")

ICE_HEADERS = [
    "tag308 - UnderlyingSecurityExchange",
    "tag9061 - ProductID",
    "tag9062 - ProductName",
    "tag9301 - HubName",
    "tag9302 - HubAlias",
    "tag311/55 - UnderlyingSymbol",
    "tag309 - UnderlyingSecurityID",
]
MAR_HEADERS = ["InstId", "Instname", "perid", "secondPeriod"]


# ── Authentication ────────────────────────────────────────────────────────────

def get_access_token(allow_interactive=True):
    """Return a valid access token, using cached credentials or device-code login.

    When allow_interactive is False, only a silent (cached) refresh is attempted —
    used for the weekly keepalive runs so an unattended Friday never blocks on a
    device-code prompt nobody is there to complete. Returns None on failure instead
    of raising, so callers can treat it as "refresh didn't happen, try next week."
    """
    cache = msal.SerializableTokenCache()
    if os.path.exists(TOKEN_CACHE_FILE):
        with open(TOKEN_CACHE_FILE, "r") as f:
            cache.deserialize(f.read())

    app = msal.PublicClientApplication(
        CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
        token_cache=cache,
    )

    result = None
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])

    if not result:
        if not allow_interactive:
            return None
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise RuntimeError(f"Device flow failed: {flow}")
        print("\n" + "=" * 60)
        print(flow["message"])
        print("=" * 60 + "\n")
        result = app.acquire_token_by_device_flow(flow)

    if cache.has_state_changed:
        with open(TOKEN_CACHE_FILE, "w") as f:
            f.write(cache.serialize())

    if not result or "access_token" not in result:
        if not allow_interactive:
            return None
        raise RuntimeError(f"Authentication failed: {(result or {}).get('error_description', result)}")

    return result["access_token"]


def graph_get(token, url, params=None):
    """GET a Graph API endpoint, handling pagination automatically."""
    headers = {"Authorization": f"Bearer {token}"}
    items = []
    while url:
        resp = requests.get(url, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()
        items.extend(data.get("value", [data]))
        url = data.get("@odata.nextLink")
        params = None  # nextLink already encodes params
    return items


# ── Folder discovery ──────────────────────────────────────────────────────────

def find_folder(token, target_name):
    """Recursively search mailbox folders for target_name; return its ID."""
    inbox = graph_get(token, f"{GRAPH_BASE}/users/{USER_EMAIL}/mailFolders/Inbox")[0]

    def search(folder_id):
        children = graph_get(token, f"{GRAPH_BASE}/users/{USER_EMAIL}/mailFolders/{folder_id}/childFolders")
        for f in children:
            if f["displayName"] == target_name:
                return f["id"]
            result = search(f["id"])
            if result:
                return result
        return None

    return search(inbox["id"])


# ── Email helpers ─────────────────────────────────────────────────────────────

def get_messages_for_month(token, folder_id, year, month):
    """Return all messages in folder received during the given year/month."""
    start = datetime(year, month, 1, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_year, end_month = (year + 1, 1) if month == 12 else (year, month + 1)
    end = datetime(end_year, end_month, 1, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return graph_get(
        token,
        f"{GRAPH_BASE}/users/{USER_EMAIL}/mailFolders/{folder_id}/messages",
        params={
            "$filter": f"receivedDateTime ge {start} and receivedDateTime lt {end}",
            "$select": "id,subject,body,receivedDateTime",
            "$top": 100,
            "$orderby": "receivedDateTime desc",
        },
    )


def is_qualifying(msg):
    subject = msg.get("subject", "") or ""
    body = (msg.get("body", {}) or {}).get("content", "") or ""
    if not SUBJECT_PATTERN.match(subject.strip()):
        return False
    if SKIP_BODY_PHRASE.lower() in body.lower():
        print(f"  [SKIP] '{subject}' — body contains '{SKIP_BODY_PHRASE}'")
        return False
    return True


def download_attachments(token, msg_id, output_dir):
    """Download all supported attachments; return list of local file paths."""
    attachments = graph_get(token, f"{GRAPH_BASE}/users/{USER_EMAIL}/messages/{msg_id}/attachments")
    saved = []
    for att in attachments:
        name = att.get("name", "")
        if not name.lower().endswith(SUPPORTED_EXTENSIONS):
            continue
        content = base64.b64decode(att["contentBytes"])
        dest = os.path.join(output_dir, name)
        base, ext = os.path.splitext(dest)
        counter = 1
        while os.path.exists(dest):
            dest = f"{base}_{counter}{ext}"
            counter += 1
        with open(dest, "wb") as f:
            f.write(content)
        saved.append(dest)
        print(f"    Downloaded: {name}")
    return saved


def read_attachment_as_df(file_path):
    if file_path.lower().endswith(".csv"):
        return pd.read_csv(file_path, encoding="utf-8")
    xl = pd.ExcelFile(file_path)
    frames = [xl.parse(sheet) for sheet in xl.sheet_names]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# ── Data processing ───────────────────────────────────────────────────────────

def parse_ice_row(orig_ins_type):
    parts = str(orig_ins_type).split("|")
    while len(parts) < 7:
        parts.append("")
    return parts[:7]


def parse_mar_row(orig_ins_type):
    parts = str(orig_ins_type).split("-")
    if len(parts) >= 4:
        return [parts[0], "-".join(parts[1:-2]), parts[-2], parts[-1]]
    if len(parts) == 3:
        return [parts[0], parts[1], parts[2], ""]
    if len(parts) == 2:
        return [parts[0], parts[1], "", ""]
    return [parts[0] if parts else "", "", "", ""]


def build_ice_sheet(df):
    ice = df[df["ORIGIN"].str.contains("ICE", case=False, na=False)].copy()
    expanded = ice["ORIG_INS_TYPE"].apply(lambda x: pd.Series(parse_ice_row(x), index=ICE_HEADERS))
    return pd.concat([ice[["ins_type", "ORIG_INS_TYPE", "ORIGIN", "client"]].reset_index(drop=True),
                      expanded.reset_index(drop=True)], axis=1)


def build_mar_sheet(df):
    mar = df[df["ORIGIN"].str.contains("MAR", case=False, na=False)].copy()
    expanded = mar["ORIG_INS_TYPE"].apply(lambda x: pd.Series(parse_mar_row(x), index=MAR_HEADERS))
    return pd.concat([mar[["ins_type", "ORIG_INS_TYPE", "ORIGIN", "client"]].reset_index(drop=True),
                      expanded.reset_index(drop=True)], axis=1)


# ── Email sending ─────────────────────────────────────────────────────────────

def send_results_email(token, attachment_path, email_count, total_rows, dedup_rows,
                       ice_count, mar_count, year, month):
    month_label = datetime(year, month, 1).strftime("%B %Y")
    filename = os.path.basename(attachment_path)

    with open(attachment_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")

    html_body = (
        f"<p>Hi,</p>"
        f"<p>Please find attached the aggregated and deduplicated report from "
        f"{email_count} qualifying 'Fortnightly Missing Mapping Check' emails "
        f"received in {month_label} from the '{TARGET_SUBFOLDER}' folder.</p>"
        f"<p><b>Data summary:</b><br>"
        f"&nbsp;&nbsp;• Source emails: {email_count}<br>"
        f"&nbsp;&nbsp;• Total rows (raw): {total_rows}<br>"
        f"&nbsp;&nbsp;• Unique records: {dedup_rows} ({total_rows - dedup_rows} duplicates removed)<br>"
        f"&nbsp;&nbsp;• ICE instruments: {ice_count}<br>"
        f"&nbsp;&nbsp;• MAR instruments: {mar_count}<br>"
        f"&nbsp;&nbsp;<i>(Deduplicated on: ins_type + ORIG_INS_TYPE + ORIGIN)</i></p>"
        f"<p><b>The attached Excel file contains three sheets:</b><br>"
        f"&nbsp;&nbsp;• <b>Summary</b> — all {dedup_rows} unique records with client name<br>"
        f"&nbsp;&nbsp;• <b>ICE</b> — {ice_count} ICE instruments with tag field breakdown<br>"
        f"&nbsp;&nbsp;• <b>MAR</b> — {mar_count} MAR instruments with field breakdown</p>"
        f"<p>Generated by Outlook Agent on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}.</p>"
    )

    payload = {
        "message": {
            "subject": f"MAR/ICE Missing Ins — {month_label} Aggregated Report ({email_count} emails)",
            "body": {"contentType": "HTML", "content": html_body},
            "toRecipients": [{"emailAddress": {"address": r}} for r in RECIPIENTS],
            "attachments": [{
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": filename,
                "contentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "contentBytes": encoded,
            }],
        }
    }

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    resp = requests.post(f"{GRAPH_BASE}/users/{USER_EMAIL}/sendMail", json=payload, headers=headers)
    resp.raise_for_status()
    print(f"\n✅ Email sent to: {', '.join(RECIPIENTS)}")


# ── Scheduling helpers ────────────────────────────────────────────────────────

def last_friday_of_month(year, month):
    fridays = [week[FRIDAY] for week in monthcalendar(year, month) if week[FRIDAY] != 0]
    return date(year, month, fridays[-1])


def is_last_friday_today():
    today = date.today()
    return today == last_friday_of_month(today.year, today.month)


# ── Entry point ───────────────────────────────────────────────────────────────

def main(force_year=None, force_month=None):
    today = date.today()
    target_year  = force_year  or today.year
    target_month = force_month or today.month
    month_label  = datetime(target_year, target_month, 1).strftime("%B %Y")

    is_scheduled_run = force_year is None and force_month is None
    is_last = is_last_friday_today() if is_scheduled_run else True

    if is_scheduled_run and not is_last:
        # Every Friday (not just the last one) we still refresh the cached login
        # silently, so the token never goes stale for a full month between real
        # runs. No interactive fallback here — nobody is present to complete one.
        print("🔑 Weekly keepalive: refreshing cached credentials silently...")
        token = get_access_token(allow_interactive=False)
        print("✅ Token refreshed." if token else "⚠ Silent refresh failed (no valid cached session yet).")
        next_run = last_friday_of_month(today.year, today.month)
        print(f"⏭ Today ({today}) is not the last Friday of {month_label}. "
              f"Next scheduled run: {next_run}. Exiting.")
        return

    if is_scheduled_run:
        print(f"📅 Last Friday of {month_label} confirmed ({today}). Running agent...")

    print("🔑 Authenticating with Microsoft Graph...")
    token = get_access_token()
    print("✅ Authenticated.")

    print(f"📁 Locating folder: {TARGET_SUBFOLDER}")
    folder_id = find_folder(token, TARGET_SUBFOLDER)
    if not folder_id:
        print(f"❌ Folder '{TARGET_SUBFOLDER}' not found.")
        return
    print(f"✅ Folder found. Fetching {month_label} emails...")

    messages = get_messages_for_month(token, folder_id, target_year, target_month)
    print(f"   {len(messages)} messages retrieved.\n")

    with tempfile.TemporaryDirectory() as tmp_dir:
        all_frames = []
        processed_count = 0

        for msg in messages:
            subject = msg.get("subject", "<no subject>")
            print(f"  → {subject}")

            if not is_qualifying(msg):
                continue

            client_name = subject.split("-Fortnightly")[0].strip()
            print(f"    ✔ Qualifying — client: {client_name}")

            downloaded = download_attachments(token, msg["id"], tmp_dir)
            if not downloaded:
                print(f"    ⚠ No supported attachments found.")
                continue

            for file_path in downloaded:
                try:
                    df = read_attachment_as_df(file_path)
                    df["client"] = client_name
                    all_frames.append(df)
                except Exception as e:
                    print(f"    ⚠ Could not read {os.path.basename(file_path)}: {e}")

            processed_count += 1

        print(f"\n📊 Summary: {processed_count} qualifying emails, {len(all_frames)} file(s) loaded.")

        if not all_frames:
            print("⚠ No data to send. Exiting.")
            return

        print("\n🔀 Aggregating and deduplicating...")
        dedup_key = ["ins_type", "ORIG_INS_TYPE", "ORIGIN"]
        combined = pd.concat(all_frames, ignore_index=True)
        total_rows = len(combined)
        combined.drop_duplicates(subset=dedup_key, keep="first", inplace=True)
        combined.reset_index(drop=True, inplace=True)
        dedup_rows = len(combined)
        print(f"  Total rows      : {total_rows}")
        print(f"  Unique rows     : {dedup_rows} ({total_rows - dedup_rows} removed)")

        ice_sheet = build_ice_sheet(combined)
        mar_sheet = build_mar_sheet(combined)
        ice_count, mar_count = len(ice_sheet), len(mar_sheet)
        print(f"  ICE instruments : {ice_count}")
        print(f"  MAR instruments : {mar_count}")

        safe_month = datetime(target_year, target_month, 1).strftime("%b%Y")
        output_path = os.path.join(tmp_dir, f"MAR_ICE_Missing_Ins_{safe_month}_Aggregated.xlsx")
        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            combined.to_excel(writer, sheet_name="Summary", index=False)
            ice_sheet.to_excel(writer, sheet_name="ICE", index=False)
            mar_sheet.to_excel(writer, sheet_name="MAR", index=False)
        print(f"  Saved: {os.path.basename(output_path)}")

        print("\n📧 Sending email...")
        send_results_email(token, output_path, processed_count, total_rows, dedup_rows,
                           ice_count, mar_count, target_year, target_month)


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3:
        main(force_year=int(sys.argv[1]), force_month=int(sys.argv[2]))
    else:
        main()
