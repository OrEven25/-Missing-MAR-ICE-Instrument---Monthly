# Missing MAR/ICE Instrument – Monthly Processor

Automatically extracts missing instrument mapping data from Fortnightly
Missing Mapping Check emails in Outlook, converts the Excel attachments to
CSV, drops them in this repository, and emails a summary to the mailbox owner.

---

## How it works

1. Connects to `or.even@cubelogic.com` via Microsoft Exchange (autodiscover or a
   fixed server).
2. Opens the **MAR/ICE Missing Ins** sub-folder inside the Inbox.
3. Finds all emails whose subject contains **`-Fortnightly Missing Mapping Check`**
   sent during the target calendar month.
4. Skips any email where the body says *Total records 0 rows*.
5. For every remaining email it downloads the Excel attachment and converts it
   to CSV.
6. Saves all CSVs to a `YYYY-MM/` folder in this repository.
7. Sends a summary email to `or.even@cubelogic.com` with all CSVs attached.

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Create a `.env` file

Copy the example and fill in your credentials:

```bash
cp .env.example .env
```

Edit `.env`:

```
EMAIL_ADDRESS=or.even@cubelogic.com
EMAIL_PASSWORD=<your Outlook password>
# Optional – leave blank to use autodiscover
# EXCHANGE_SERVER=outlook.office365.com
RECIPIENT_EMAIL=or.even@cubelogic.com
# Optional – defaults to the previous calendar month
# TARGET_MONTH=2026-05
```

> **Security note**: `.env` is listed in `.gitignore` so your credentials are
> never committed to the repository.

---

## Usage

### Process the previous calendar month (default)

```bash
python process_missing_instruments.py
```

### Process a specific month

```bash
python process_missing_instruments.py 2026-05
```

Or set `TARGET_MONTH=2026-05` in your `.env` file.

---

## Output

CSVs are saved under a `YYYY-MM/` directory, e.g.:

```
2026-05/
  BKW_Missing_Mapping_20260501.csv
  ABC_Missing_Mapping_20260515.csv
```

Commit and push the folder to record the results in the repository.

---

## Requirements

| Package | Purpose |
|---------|---------|
| `exchangelib` | Connect to Exchange / Office 365 mailbox |
| `openpyxl` | Read `.xlsx` attachments |
| `python-dotenv` | Load credentials from `.env` |
