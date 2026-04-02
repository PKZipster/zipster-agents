#!/usr/bin/env python3
"""Finance Manager Agent — scans Gmail for invoices, extracts data via Claude, stores in SQLite, alerts via Slack."""

import base64
import json
import logging
import os
import re
import sqlite3
import urllib.request
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

import anthropic

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parents[2]
SECRETS_ENV = BASE_DIR / "shared" / "config" / "secrets.env"
SERVICE_ACCOUNT_JSON = BASE_DIR / "shared" / "config" / "google-service-account.json"
DB_PATH = BASE_DIR / "data" / "finance-manager.db"
LOG_PATH = BASE_DIR / "logs" / "finance-manager.log"

DELEGATED_USER = "Agents@zipsterbaby.com"
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

# ── Logging ────────────────────────────────────────────────────────────────────
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("finance-manager")

# ── Load secrets ───────────────────────────────────────────────────────────────
load_dotenv(SECRETS_ENV)
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_FINANCE_MANAGER") or os.getenv("SLACK_WEBHOOK_URL")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")


# ── Database ───────────────────────────────────────────────────────────────────
def init_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier        TEXT,
            amount          REAL,
            currency        TEXT,
            due_date        TEXT,
            invoice_number  TEXT,
            category        TEXT,
            status          TEXT DEFAULT 'unpaid',
            email_id        TEXT UNIQUE,
            extracted_date  TEXT,
            reminder_count  INTEGER DEFAULT 0,
            parent_invoice_id INTEGER REFERENCES invoices(id)
        )
    """)
    # Migrate: add columns if they don't exist yet
    existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(invoices)").fetchall()}
    if "reminder_count" not in existing_cols:
        conn.execute("ALTER TABLE invoices ADD COLUMN reminder_count INTEGER DEFAULT 0")
    if "parent_invoice_id" not in existing_cols:
        conn.execute("ALTER TABLE invoices ADD COLUMN parent_invoice_id INTEGER REFERENCES invoices(id)")
    conn.commit()
    return conn


# ── Gmail ──────────────────────────────────────────────────────────────────────
def get_gmail_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_JSON, scopes=GMAIL_SCOPES
    )
    delegated = creds.with_subject(DELEGATED_USER)
    return build("gmail", "v1", credentials=delegated)


def fetch_message_ids(service, query: str = "to:accounts@zipsterbaby.com") -> list[str]:
    """Return all message IDs matching query, paginating through all results."""
    all_ids = []
    page_token = None

    while True:
        kwargs = {"userId": "me", "q": query, "maxResults": 100}
        if page_token:
            kwargs["pageToken"] = page_token
        results = service.users().messages().list(**kwargs).execute()

        for msg in results.get("messages", []):
            all_ids.append(msg["id"])
        log.info("Fetched %d message IDs so far...", len(all_ids))

        page_token = results.get("nextPageToken")
        if not page_token:
            break

    return all_ids


def fetch_email(service, message_id: str) -> dict:
    """Fetch full details for a single message."""
    msg = service.users().messages().get(
        userId="me", id=message_id, format="full"
    ).execute()

    headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
    body_text = _extract_body(msg["payload"])

    return {
        "id": message_id,
        "subject": headers.get("Subject", ""),
        "from": headers.get("From", ""),
        "date": headers.get("Date", ""),
        "body": body_text,
    }


def _extract_body(payload: dict) -> str:
    """Recursively extract plain-text body from a Gmail message payload."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    for part in payload.get("parts", []):
        text = _extract_body(part)
        if text:
            return text

    # Fallback: decode whatever body data exists
    if payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
    return ""


def mark_as_read(service, message_id: str):
    service.users().messages().modify(
        userId="me",
        id=message_id,
        body={"removeLabelIds": ["UNREAD"]},
    ).execute()


# ── Claude extraction ──────────────────────────────────────────────────────────
def extract_invoice_data(email: dict) -> dict | None:
    """Use Claude to decide if an email is an invoice and extract structured data."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = f"""Analyze the following email and determine if it contains or references an invoice.
If it does NOT contain an invoice, respond with exactly: NOT_AN_INVOICE
IMPORTANT: Shopify Capital daily remittance/repayment emails are NOT invoices — they are automatic loan repayments taken as a percentage of sales. Respond NOT_AN_INVOICE for these.
If it DOES contain an invoice or payment reminder, respond with ONLY a JSON object (no markdown) with these fields:
- supplier: string (company/person name)
- amount: number
- currency: string (3-letter ISO code, e.g. USD, GBP, EUR)
- due_date: string (YYYY-MM-DD format, or "unknown" if not specified)
- invoice_number: string (or "unknown")
- category: one of "inventory", "marketing", "software", "logistics", "other"
- is_reminder: boolean (true if this is a payment reminder, follow-up, second/third notice, overdue notice, or "herinnering" for a previously sent invoice; false if this is the original invoice)

Email subject: {email['subject']}
Email from: {email['from']}
Email date: {email['date']}
Email body:
{email['body'][:4000]}"""

    import time
    for attempt in range(5):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            break
        except anthropic.APIStatusError as e:
            if e.status_code in (429, 529) and attempt < 4:
                wait = 2 ** (attempt + 1)
                log.warning("API %d, retrying in %ds (attempt %d/5)...", e.status_code, wait, attempt + 1)
                time.sleep(wait)
            else:
                raise

    text = response.content[0].text.strip()

    if "NOT_AN_INVOICE" in text:
        return None

    # Extract JSON from response
    json_match = re.search(r"\{.*\}", text, re.DOTALL)
    if not json_match:
        log.warning("Could not parse Claude response for email %s: %s", email["id"], text[:200])
        return None

    try:
        return json.loads(json_match.group())
    except json.JSONDecodeError:
        log.warning("Invalid JSON from Claude for email %s: %s", email["id"], text[:200])
        return None


# ── Status logic ───────────────────────────────────────────────────────────────
def compute_status(due_date_str: str) -> str:
    if due_date_str == "unknown":
        return "unpaid"
    try:
        due = datetime.strptime(due_date_str, "%Y-%m-%d").date()
    except ValueError:
        return "unpaid"
    if due < datetime.now().date():
        return "overdue"
    return "unpaid"


# ── Slack ──────────────────────────────────────────────────────────────────────
def send_slack_summary(conn: sqlite3.Connection):
    today = datetime.now().date()
    seven_days = (today + timedelta(days=7)).isoformat()
    today_str = today.isoformat()

    due_soon = conn.execute(
        "SELECT supplier, amount, currency, due_date, invoice_number, reminder_count FROM invoices "
        "WHERE status = 'unpaid' AND due_date != 'unknown' AND due_date <= ? AND due_date >= ?",
        (seven_days, today_str),
    ).fetchall()

    overdue = conn.execute(
        "SELECT supplier, amount, currency, due_date, invoice_number, reminder_count FROM invoices "
        "WHERE status = 'overdue'",
    ).fetchall()

    if not due_soon and not overdue:
        log.info("No urgent invoices to report via Slack.")
        return

    lines = ["*💰 Finance Manager — Invoice Alert*\n"]

    if overdue:
        lines.append(f"*🚨 Overdue ({len(overdue)}):*")
        for supplier, amount, currency, due, inv_no, reminders in overdue:
            reminder_tag = f" ⚠️ {reminders} reminder(s)" if reminders else ""
            try:
                amt_str = f"{float(amount):,.2f}"
            except (ValueError, TypeError):
                amt_str = str(amount)
            lines.append(f"  • {supplier} — {currency} {amt_str} (#{inv_no}, due {due}){reminder_tag}")

    if due_soon:
        lines.append(f"\n*⏰ Due within 7 days ({len(due_soon)}):*")
        for supplier, amount, currency, due, inv_no, reminders in due_soon:
            reminder_tag = f" ⚠️ {reminders} reminder(s)" if reminders else ""
            try:
                amt_str = f"{float(amount):,.2f}"
            except (ValueError, TypeError):
                amt_str = str(amount)
            lines.append(f"  • {supplier} — {currency} {amt_str} (#{inv_no}, due {due}){reminder_tag}")

    message = "\n".join(lines)
    _post_slack(message)


def _post_slack(text: str):
    if not SLACK_WEBHOOK_URL:
        log.warning("SLACK_WEBHOOK_URL not set — skipping Slack notification.")
        return
    data = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        SLACK_WEBHOOK_URL,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            log.info("Slack notification sent (%s).", resp.status)
    except Exception as e:
        log.error("Failed to send Slack message: %s", e)


# ── Deduplication ──────────────────────────────────────────────────────────────
def find_existing_invoice(conn: sqlite3.Connection, invoice_number: str,
                          supplier: str, amount: float, due_date: str) -> tuple | None:
    """Find an existing invoice by number, or by supplier+amount+date proximity.

    Returns (id, reminder_count) of the matching invoice, or None.
    """
    # 1. Match by invoice number (if known)
    if invoice_number and invoice_number != "unknown":
        row = conn.execute(
            "SELECT id, reminder_count FROM invoices "
            "WHERE invoice_number = ? AND status NOT IN ('skipped', 'duplicate', 'reminder') "
            "ORDER BY id ASC LIMIT 1",
            (invoice_number,),
        ).fetchone()
        if row:
            return row

    # 2. Match by supplier + amount within 30 days
    if supplier and supplier != "Unknown" and amount and amount > 0:
        candidates = conn.execute(
            "SELECT id, reminder_count, due_date FROM invoices "
            "WHERE supplier = ? AND amount = ? AND status NOT IN ('skipped', 'duplicate', 'reminder') "
            "ORDER BY id ASC",
            (supplier, amount),
        ).fetchall()

        for cand_id, cand_reminder_count, cand_due in candidates:
            if _dates_within_30_days(due_date, cand_due):
                return (cand_id, cand_reminder_count)

    return None


def _dates_within_30_days(date_a: str, date_b: str) -> bool:
    """Check if two date strings are within 30 days of each other. Unknown dates always match."""
    if date_a == "unknown" or date_b == "unknown":
        return True
    try:
        a = datetime.strptime(date_a, "%Y-%m-%d").date()
        b = datetime.strptime(date_b, "%Y-%m-%d").date()
        return abs((a - b).days) <= 30
    except ValueError:
        return True


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    log.info("Finance Manager agent starting.")

    conn = init_db()
    log.info("Database ready at %s", DB_PATH)

    service = get_gmail_service()
    log.info("Connected to Gmail as %s", DELEGATED_USER)

    # Broad search: multiple strategies to catch all invoices
    queries = [
        "to:accounts@zipsterbaby.com newer_than:60d",
        "subject:(invoice OR factuur OR receipt OR herinnering OR reminder OR rekening OR betaling OR payment OR bon OR creditnota) newer_than:60d",
        "(from:monta OR from:flexport OR from:manify OR from:klaviyo OR from:shopify OR from:vodafone OR from:nmbrs OR from:ogoship OR from:deepl OR from:meta OR from:google OR from:anthropic OR from:stripe OR from:swap OR from:purplefire OR from:apple OR from:amazon OR from:fedex OR from:kpn OR from:mindspace OR from:triple OR from:weconnect OR from:jambear OR from:kokomi OR from:ikea OR from:jysk) newer_than:60d",
        "from:evie@zipsterbaby.com to:accounts@zipsterbaby.com newer_than:60d",
    ]
    seen = set()
    message_ids = []
    for q in queries:
        for mid in fetch_message_ids(service, query=q):
            if mid not in seen:
                seen.add(mid)
                message_ids.append(mid)
    log.info("Found %d unique email(s) total from %d queries.", len(message_ids), len(queries))

    # Filter out already-processed emails
    processed = {row[0] for row in conn.execute("SELECT email_id FROM invoices").fetchall()}
    new_ids = [mid for mid in message_ids if mid not in processed]
    log.info("%d already processed, %d to analyze.", len(message_ids) - len(new_ids), len(new_ids))

    invoices_found = 0
    reminders_found = 0
    for i, msg_id in enumerate(new_ids, 1):
        try:
            email = fetch_email(service, msg_id)
        except Exception as e:
            log.warning("Failed to fetch email %s: %s — skipping.", msg_id, e)
            continue

        log.info("[%d/%d] Analyzing: %s (from %s)", i, len(new_ids), email["subject"], email["from"])
        invoice = extract_invoice_data(email)

        if invoice is None:
            log.info("Email %s is not an invoice — skipping.", email["id"])
            conn.execute(
                "INSERT OR IGNORE INTO invoices (supplier, amount, currency, due_date, invoice_number, "
                "category, status, email_id, extracted_date, reminder_count, parent_invoice_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("_not_invoice", 0, "", "", "", "", "skipped", email["id"],
                 datetime.now().isoformat(), 0, None),
            )
            conn.commit()
            continue

        is_reminder = invoice.get("is_reminder", False)
        inv_number = invoice.get("invoice_number", "unknown")
        supplier = invoice.get("supplier", "Unknown")
        try:
            amount = float(invoice.get("amount", 0))
        except (ValueError, TypeError):
            amount = 0.0
        currency = invoice.get("currency", "USD")
        due_date = invoice.get("due_date", "unknown")
        category = invoice.get("category", "other")

        # Try to find existing invoice
        existing = find_existing_invoice(conn, inv_number, supplier, amount, due_date)

        if is_reminder and existing:
            # Link reminder to existing invoice, bump reminder count
            parent_id, old_count = existing[0], existing[1]
            new_count = old_count + 1
            conn.execute("UPDATE invoices SET reminder_count = ? WHERE id = ?", (new_count, parent_id))
            # Update status to overdue if it was just unpaid
            conn.execute(
                "UPDATE invoices SET status = 'overdue' WHERE id = ? AND status = 'unpaid'",
                (parent_id,),
            )
            conn.execute(
                "INSERT OR IGNORE INTO invoices (supplier, amount, currency, due_date, invoice_number, "
                "category, status, email_id, extracted_date, reminder_count, parent_invoice_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (supplier, amount, currency, due_date, inv_number, category,
                 "reminder", email["id"], datetime.now().isoformat(), 0, parent_id),
            )
            conn.commit()
            reminders_found += 1
            log.info(
                "Linked reminder to invoice #%s (id=%d, reminder_count now %d)",
                inv_number, parent_id, new_count,
            )
        elif is_reminder and not existing:
            # Reminder but no parent found — store as new invoice marked overdue
            status = "overdue"
            conn.execute(
                "INSERT OR IGNORE INTO invoices (supplier, amount, currency, due_date, invoice_number, "
                "category, status, email_id, extracted_date, reminder_count, parent_invoice_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (supplier, amount, currency, due_date, inv_number, category,
                 status, email["id"], datetime.now().isoformat(), 1, None),
            )
            conn.commit()
            invoices_found += 1
            reminders_found += 1
            log.info(
                "Stored new invoice #%s from %s (from reminder, no parent found) — %s %s",
                inv_number, supplier, currency, amount,
            )
        elif not is_reminder and existing:
            # Duplicate invoice — update status on existing
            parent_id = existing[0]
            status = compute_status(due_date)
            conn.execute(
                "UPDATE invoices SET status = ?, due_date = CASE WHEN due_date = 'unknown' THEN ? ELSE due_date END "
                "WHERE id = ?",
                (status, due_date, parent_id),
            )
            # Track this email as linked to the parent
            conn.execute(
                "INSERT OR IGNORE INTO invoices (supplier, amount, currency, due_date, invoice_number, "
                "category, status, email_id, extracted_date, reminder_count, parent_invoice_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (supplier, amount, currency, due_date, inv_number, category,
                 "duplicate", email["id"], datetime.now().isoformat(), 0, parent_id),
            )
            conn.commit()
            log.info(
                "Duplicate invoice #%s from %s — updated existing (id=%d)",
                inv_number, supplier, parent_id,
            )
        else:
            # New invoice
            status = compute_status(due_date)
            conn.execute(
                "INSERT OR IGNORE INTO invoices (supplier, amount, currency, due_date, invoice_number, "
                "category, status, email_id, extracted_date, reminder_count, parent_invoice_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (supplier, amount, currency, due_date, inv_number, category,
                 status, email["id"], datetime.now().isoformat(), 0, None),
            )
            conn.commit()
            invoices_found += 1
            log.info(
                "Stored invoice #%s from %s — %s %s (status: %s)",
                inv_number, supplier, currency, amount, status,
            )

    log.info(
        "Processing complete. %d new invoice(s), %d reminder(s) from %d email(s).",
        invoices_found, reminders_found, len(new_ids),
    )

    send_slack_summary(conn)
    conn.close()
    log.info("Finance Manager agent finished.")


if __name__ == "__main__":
    main()
