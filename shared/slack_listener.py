#!/usr/bin/env python3
"""Slack Socket Mode listener — routes messages to AI-powered Zipster agent personas with real data."""

import json
import logging
import os
import re
import sqlite3
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

# ── Config ─────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parents[1]
SECRETS_ENV = BASE_DIR / "shared" / "config" / "secrets.env"
LOG_PATH = BASE_DIR / "logs" / "slack-listener.log"
FINANCE_DB = BASE_DIR / "data" / "finance-manager.db"

LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("slack-listener")

load_dotenv(SECRETS_ENV)
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
SHOPIFY_API_KEY = os.getenv("SHOPIFY_API_KEY")
SHOPIFY_STORE = os.getenv("SHOPIFY_STORE", "eu-zipsterbaby")

claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

app = App(token=SLACK_BOT_TOKEN)


# ══════════════════════════════════════════════════════════════════════════════
# AGENT PERSONAS
# ══════════════════════════════════════════════════════════════════════════════
AGENTS = {
    "finance-manager": {
        "name": "Monty",
        "title": "Finance Manager",
        "emoji": ":money_with_wings:",
        "system_prompt": (
            "You are Monty, the Finance Manager at Zipster. You have dry wit, you're precise, "
            "and you speak in numbers. You have slightly old-school banker energy — think pinstripe "
            "suit, fountain pen, knows the exact balance to the cent. You lead with the figures, "
            "keep it tight, and occasionally drop a wry observation. When presenting financial data, "
            "use clear formatting with bullet points. If amounts are in different currencies, note that. "
            "You care about cash flow discipline and keeping suppliers paid on time.\n\n"
            "CAPABILITIES:\n"
            "- You can report on overdue invoices, unpaid invoices, cash flow summary\n"
            "- You can mark invoices as paid when instructed — respond with confirmation and the "
            "  exact invoice number you've marked. Include MARK_PAID::<invoice_number> in your response "
            "  (this will be parsed and executed automatically, then stripped from the message)\n"
            "- You can provide cash flow summaries showing total outstanding, overdue, and due soon\n\n"
            "Always sign off with '— Monty'."
        ),
    },
    "shopify-ops": {
        "name": "Otto",
        "title": "Shopify Ops",
        "emoji": ":package:",
        "system_prompt": (
            "You are Otto, Shopify Ops Manager at Zipster. You are calm, methodical, and "
            "detail-obsessed — you never miss a product field, a SKU, or a shipping label. "
            "Very Dutch, very precise. You speak in clear, structured language and take pride in "
            "operational excellence. Everything has a process, and the process is sacred.\n\n"
            "When you have Shopify data, present it clearly with counts, percentages, and specific "
            "product names. Flag issues precisely — missing descriptions, missing tags, draft products "
            "that should be live. When Shopify API is not connected, state exactly what's needed: "
            "a Shopify Admin API access token with read_products, read_orders scopes, stored as "
            "SHOPIFY_API_KEY in the config.\n\n"
            "Always sign off with '— Otto'."
        ),
    },
    "marketing": {
        "name": "Don",
        "title": "Marketing Signal",
        "emoji": ":chart_with_upwards_trend:",
        "system_prompt": (
            "You are Don, Marketing Signal Lead at Zipster. You're confident, direct, and think "
            "like a media buyer. You always talk in terms of MER (Marketing Efficiency Ratio = "
            "Revenue / Total Ad Spend), ROAS, and efficiency. Every euro spent needs to earn its place. "
            "You cut through vanity metrics and focus on what actually moves revenue. You're not rude, "
            "but you don't sugarcoat poor performance either.\n\n"
            "When you have Shopify revenue data, calculate and present: revenue, order count, AOV, "
            "and MER if ad spend is available. When data is missing, state exactly what's needed.\n\n"
            "Always sign off with '— Don'."
        ),
    },
    "content": {
        "name": "Connie",
        "title": "Content",
        "emoji": ":art:",
        "system_prompt": (
            "You are Connie, Content Lead at Zipster. You're warm, creative, and brand-obsessed. "
            "You think about Zipster's voice in everything — every word, every image, every story.\n\n"
            "ZIPSTER BRAND CONTEXT:\n"
            "- Premium bamboo baby sleepwear brand, founded in Amsterdam\n"
            "- GOTS certified organic bamboo fabric — incredibly soft, temperature-regulating\n"
            "- Key differentiator: two-way zipper (easy nappy changes without fully undressing baby)\n"
            "- Markets: Netherlands (home), United Kingdom, Switzerland\n"
            "- Brand tone: playful but sophisticated, parent-friendly, quality-first\n"
            "- Target audience: design-conscious parents who want the best for their baby\n"
            "- Product range: sleepsuits, sleeping bags, pyjamas — all bamboo\n"
            "- Competitors: Little Butterfly London, Snuggle Hunny, Kyte Baby\n"
            "- USPs: European design, GOTS certified, two-way zip, gifting-ready packaging\n\n"
            "Reference actual Zipster products, brand values, and positioning in every response. "
            "Think strategically about content calendars, campaign concepts, shoot briefs, "
            "social media strategy, email flows, and brand storytelling.\n\n"
            "Always sign off with '— Connie'."
        ),
    },
    "zipster-command": {
        "name": "Nero",
        "title": "Zipster Command",
        "emoji": ":zap:",
        "system_prompt": (
            "You are Nero, the orchestrator at Zipster Command. You're sharp, decisive, and see "
            "the full picture. Your job is to analyse every message and route it to the right agent.\n\n"
            "YOUR TEAM:\n"
            "- *Monty* (finance-manager) — invoices, payments, cash flow, AP/AR, supplier management\n"
            "- *Otto* (shopify-ops) — orders, inventory, fulfillment, product catalog, store health\n"
            "- *Don* (marketing) — ads, ROAS, MER, campaigns, revenue, performance, today vs yesterday\n"
            "- *Connie* (content) — brand voice, social media, content calendar, creative briefs\n\n"
            "ROUTING RULES:\n"
            "- ALWAYS route to the specialist agent. They have live data and will answer with real numbers.\n"
            "- Do NOT answer on behalf of another agent or describe what they can do — route to them.\n"
            "- Revenue, performance, marketing queries → ROUTE::marketing (Don has live Shopify data)\n"
            "- Invoice, payment, cash flow queries → ROUTE::finance-manager\n"
            "- Product, inventory, fulfillment queries → ROUTE::shopify-ops\n"
            "- Content, brand, social queries → ROUTE::content\n"
            "- Only answer directly if it's a general business/strategy question not covered above\n\n"
            "RESPONSE FORMAT:\n"
            "- Write 1-2 sentences explaining who you're routing to and why\n"
            "- End with ROUTE::<agent_id> on its own line (e.g., ROUTE::marketing)\n"
            "- The routed agent will respond immediately after you with real data\n\n"
            "Always sign off with '— Nero'."
        ),
    },
}

CHANNEL_ROUTES = {
    "finance-manager": "finance-manager",
    "finance": "finance-manager",
    "shopify": "shopify-ops",
    "marketing": "marketing",
    "content": "content",
    "command": "zipster-command",
}

_channel_cache: dict[str, str] = {}


def get_channel_name(channel_id: str) -> str:
    if channel_id in _channel_cache:
        return _channel_cache[channel_id]
    try:
        resp = app.client.conversations_info(channel=channel_id)
        name = resp["channel"]["name"]
        _channel_cache[channel_id] = name
        return name
    except Exception as e:
        log.warning("Could not resolve channel %s: %s", channel_id, e)
        return ""


def route_to_agent(channel_name: str, text: str, channel_type: str = "") -> str:
    # DMs always go to Nero for routing
    if channel_type in ("im", "mpim"):
        return "zipster-command"
    for keyword, agent in CHANNEL_ROUTES.items():
        if keyword in channel_name.lower():
            return agent
    return "zipster-command"


# ══════════════════════════════════════════════════════════════════════════════
# SHOPIFY API
# ══════════════════════════════════════════════════════════════════════════════
def shopify_api(endpoint: str) -> dict | None:
    """Make a Shopify Admin API request. Returns None if not configured."""
    if not SHOPIFY_API_KEY:
        return None
    url = f"https://{SHOPIFY_STORE}.myshopify.com/admin/api/2024-01/{endpoint}"
    req = urllib.request.Request(url, headers={
        "X-Shopify-Access-Token": SHOPIFY_API_KEY,
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as e:
        log.error("Shopify API error (%s): %s", endpoint, e)
        return None


def shopify_api_paginated(endpoint: str, resource_key: str) -> list:
    """Fetch all pages of a Shopify Admin API list endpoint."""
    if not SHOPIFY_API_KEY:
        return []
    url = f"https://{SHOPIFY_STORE}.myshopify.com/admin/api/2024-01/{endpoint}"
    all_items = []
    while url:
        req = urllib.request.Request(url, headers={
            "X-Shopify-Access-Token": SHOPIFY_API_KEY,
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
                all_items.extend(data.get(resource_key, []))
                link = resp.headers.get("Link", "")
                url = None
                if 'rel="next"' in link:
                    for part in link.split(","):
                        if 'rel="next"' in part:
                            url = part.split("<")[1].split(">")[0]
        except Exception as e:
            log.error("Shopify API paginated error (%s): %s", endpoint, e)
            break
    return all_items


def get_shopify_revenue_context() -> str:
    """Pull last 7 days revenue data from Shopify for Don, with today vs yesterday comparison."""
    if not SHOPIFY_API_KEY:
        return (
            "SHOPIFY API NOT CONNECTED.\n"
            "To connect, I need a Shopify Admin API access token with read_orders and read_products "
            "scopes. Store it as SHOPIFY_API_KEY in secrets.env. The store handle is 'eu-zipsterbaby'.\n"
            "Once connected, I can pull: revenue, order count, AOV, and calculate MER."
        )

    seven_days_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00Z")
    orders = shopify_api_paginated(
        f"orders.json?status=any&created_at_min={seven_days_ago}&limit=250", "orders"
    )
    if not orders:
        return "Failed to fetch Shopify order data."
    total_revenue = sum(float(o.get("total_price", 0)) for o in orders)
    total_orders = len(orders)
    aov = total_revenue / total_orders if total_orders > 0 else 0
    currency = orders[0].get("currency", "EUR") if orders else "EUR"

    # Build daily breakdown
    daily = {}
    for o in orders:
        day = o["created_at"][:10]
        daily.setdefault(day, {"revenue": 0, "orders": 0})
        daily[day]["revenue"] += float(o.get("total_price", 0))
        daily[day]["orders"] += 1

    # Today vs yesterday comparison
    today_str = datetime.now().strftime("%Y-%m-%d")
    yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    today_data = daily.get(today_str, {"revenue": 0, "orders": 0})
    yesterday_data = daily.get(yesterday_str, {"revenue": 0, "orders": 0})

    today_aov = today_data["revenue"] / today_data["orders"] if today_data["orders"] > 0 else 0
    yesterday_aov = yesterday_data["revenue"] / yesterday_data["orders"] if yesterday_data["orders"] > 0 else 0

    # Percentage changes
    def pct_change(current, previous):
        if previous == 0:
            return "+100%" if current > 0 else "0%"
        change = ((current - previous) / previous) * 100
        return f"{change:+.1f}%"

    rev_change = pct_change(today_data["revenue"], yesterday_data["revenue"])
    order_change = pct_change(today_data["orders"], yesterday_data["orders"])
    aov_change = pct_change(today_aov, yesterday_aov)

    # Current hour for context on partial-day comparison
    current_hour = datetime.now().strftime("%H:%M")

    lines = [
        f"=== TODAY vs YESTERDAY (as of {current_hour}) ===",
        f"TODAY ({today_str}):",
        f"  Revenue: {currency} {today_data['revenue']:,.2f} ({rev_change} vs yesterday)",
        f"  Orders: {today_data['orders']} ({order_change} vs yesterday)",
        f"  AOV: {currency} {today_aov:,.2f} ({aov_change} vs yesterday)",
        f"YESTERDAY ({yesterday_str}):",
        f"  Revenue: {currency} {yesterday_data['revenue']:,.2f}",
        f"  Orders: {yesterday_data['orders']}",
        f"  AOV: {currency} {yesterday_aov:,.2f}",
        "",
        f"=== LAST 7 DAYS TOTAL ===",
        f"Total Revenue: {currency} {total_revenue:,.2f}",
        f"Total Orders: {total_orders}",
        f"7-day AOV: {currency} {aov:,.2f}",
        "",
        "DAILY BREAKDOWN:",
    ]
    for day in sorted(daily.keys(), reverse=True):
        d = daily[day]
        day_aov = d["revenue"] / d["orders"] if d["orders"] > 0 else 0
        lines.append(f"  {day}: {currency} {d['revenue']:,.2f} ({d['orders']} orders, AOV {currency} {day_aov:,.2f})")

    lines.append("\nAD SPEND DATA: Not yet connected. To calculate MER, I need ad spend data "
                 "from Meta Ads and Google Ads APIs. MER = Revenue / Total Ad Spend.")

    return "\n".join(lines)


def get_shopify_products_context() -> str:
    """Pull product catalog data from Shopify for Otto."""
    if not SHOPIFY_API_KEY:
        return (
            "SHOPIFY API NOT CONNECTED.\n"
            "To connect, I need a Shopify Admin API access token with read_products and read_orders "
            "scopes. Store it as SHOPIFY_API_KEY in secrets.env. The store handle is 'eu-zipsterbaby'.\n"
            "Once connected, I can audit: total products, published vs draft, missing metafields, "
            "missing tags, missing descriptions, and flag products needing attention."
        )

    products = shopify_api_paginated("products.json?limit=250", "products")
    if not products:
        return "Failed to fetch Shopify product data."
    total = len(products)
    active = sum(1 for p in products if p.get("status") == "active")
    draft = sum(1 for p in products if p.get("status") == "draft")
    archived = sum(1 for p in products if p.get("status") == "archived")

    missing_description = []
    missing_tags = []
    missing_images = []
    low_inventory = []

    for p in products:
        name = p.get("title", "Unknown")
        if not p.get("body_html") or len(p.get("body_html", "")) < 20:
            missing_description.append(name)
        if not p.get("tags"):
            missing_tags.append(name)
        if not p.get("images"):
            missing_images.append(name)
        for v in p.get("variants", []):
            inv = v.get("inventory_quantity", 0)
            if inv is not None and 0 < inv <= 5 and p.get("status") == "active":
                low_inventory.append(f"{name} ({v.get('title', 'Default')}): {inv} left")

    lines = [
        f"=== SHOPIFY PRODUCT CATALOG ===",
        f"Total Products: {total}",
        f"  Active: {active}",
        f"  Draft: {draft}",
        f"  Archived: {archived}",
        "",
        "ISSUES FOUND:",
        f"  Missing/short description: {len(missing_description)}",
        f"  Missing tags: {len(missing_tags)}",
        f"  Missing images: {len(missing_images)}",
        f"  Low inventory (≤5 units): {len(low_inventory)}",
    ]

    if missing_description[:10]:
        lines.append("\nTOP PRODUCTS NEEDING DESCRIPTIONS:")
        for name in missing_description[:10]:
            lines.append(f"  • {name}")

    if missing_tags[:10]:
        lines.append("\nTOP PRODUCTS NEEDING TAGS:")
        for name in missing_tags[:10]:
            lines.append(f"  • {name}")

    if low_inventory[:10]:
        lines.append("\nLOW INVENTORY ALERTS:")
        for item in low_inventory[:10]:
            lines.append(f"  • {item}")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# FINANCE DATA
# ══════════════════════════════════════════════════════════════════════════════
def get_finance_context() -> str:
    """Query finance DB and return a context string for Monty."""
    if not FINANCE_DB.exists():
        return "Finance database not available yet."

    conn = sqlite3.connect(FINANCE_DB)
    today = datetime.now().date()
    seven_days = (today + timedelta(days=7)).isoformat()
    thirty_days = (today + timedelta(days=30)).isoformat()
    today_str = today.isoformat()

    total_invoices = conn.execute(
        "SELECT COUNT(*) FROM invoices WHERE status NOT IN ('skipped', 'duplicate', 'reminder') "
        "AND supplier != '_not_invoice'"
    ).fetchone()[0]

    overdue = conn.execute(
        "SELECT supplier, amount, currency, due_date, invoice_number, reminder_count FROM invoices "
        "WHERE status = 'overdue' AND supplier != '_not_invoice' "
        "AND amount > 0 GROUP BY invoice_number ORDER BY due_date ASC"
    ).fetchall()

    unpaid = conn.execute(
        "SELECT supplier, amount, currency, due_date, invoice_number, reminder_count FROM invoices "
        "WHERE status = 'unpaid' AND supplier != '_not_invoice' "
        "AND amount > 0 GROUP BY invoice_number ORDER BY due_date ASC"
    ).fetchall()

    due_soon = conn.execute(
        "SELECT supplier, amount, currency, due_date, invoice_number FROM invoices "
        "WHERE status = 'unpaid' AND due_date != 'unknown' AND due_date <= ? AND due_date >= ? "
        "AND supplier != '_not_invoice' AND amount > 0 GROUP BY invoice_number",
        (seven_days, today_str),
    ).fetchall()

    due_30d = conn.execute(
        "SELECT supplier, amount, currency, due_date, invoice_number FROM invoices "
        "WHERE status = 'unpaid' AND due_date != 'unknown' AND due_date <= ? AND due_date >= ? "
        "AND supplier != '_not_invoice' AND amount > 0 GROUP BY invoice_number",
        (thirty_days, today_str),
    ).fetchall()

    paid_count = conn.execute("SELECT COUNT(*) FROM invoices WHERE status = 'paid'").fetchone()[0]

    overdue_eur = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM invoices WHERE status = 'overdue' AND currency = 'EUR' "
        "AND supplier != '_not_invoice' AND amount > 0"
    ).fetchone()[0]
    unpaid_eur = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM invoices WHERE status = 'unpaid' AND currency = 'EUR' "
        "AND supplier != '_not_invoice' AND amount > 0"
    ).fetchone()[0]
    overdue_usd = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM invoices WHERE status = 'overdue' AND currency = 'USD' "
        "AND supplier != '_not_invoice' AND amount > 0"
    ).fetchone()[0]
    overdue_chf = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM invoices WHERE status = 'overdue' AND currency = 'CHF' "
        "AND supplier != '_not_invoice' AND amount > 0"
    ).fetchone()[0]

    top_suppliers = conn.execute(
        "SELECT supplier, SUM(amount) as total, currency FROM invoices "
        "WHERE status IN ('unpaid', 'overdue') AND supplier != '_not_invoice' AND amount > 0 "
        "GROUP BY supplier, currency ORDER BY total DESC LIMIT 10"
    ).fetchall()

    recent_paid = conn.execute(
        "SELECT supplier, amount, currency, invoice_number FROM invoices "
        "WHERE status = 'paid' AND extracted_date >= ? AND amount > 0 "
        "AND supplier != '_not_invoice' GROUP BY invoice_number ORDER BY amount DESC LIMIT 5",
        ((today - timedelta(days=7)).isoformat(),),
    ).fetchall()

    conn.close()

    lines = [
        f"=== FINANCE SNAPSHOT — {today_str} ===",
        f"Total invoices tracked: {total_invoices} | Paid: {paid_count}",
        "",
        "CASH POSITION — OUTSTANDING:",
        f"  Overdue: €{overdue_eur:,.2f} EUR",
    ]
    if overdue_usd > 0:
        lines.append(f"  Overdue: ${overdue_usd:,.2f} USD")
    if overdue_chf > 0:
        lines.append(f"  Overdue: CHF {overdue_chf:,.2f}")
    lines.append(f"  Unpaid (not yet due): €{unpaid_eur:,.2f} EUR")
    lines.append(f"  Total outstanding EUR: €{overdue_eur + unpaid_eur:,.2f}")
    lines.append(f"  Due within 7 days: {len(due_soon)} invoices")
    lines.append(f"  Due within 30 days: {len(due_30d)} invoices")

    lines.append("\nTOP SUPPLIERS BY OUTSTANDING:")
    for supplier, total, currency in top_suppliers:
        try:
            lines.append(f"  • {supplier}: {currency} {float(total):,.2f}")
        except (ValueError, TypeError):
            lines.append(f"  • {supplier}: {currency} {total}")

    if overdue:
        lines.append(f"\nOVERDUE ({len(overdue)}):")
        for supplier, amount, currency, due, inv_no, reminders in overdue:
            try:
                amt = f"{float(amount):,.2f}"
            except (ValueError, TypeError):
                amt = str(amount)
            r = f" ⚠️ {reminders} reminder(s)" if reminders else ""
            lines.append(f"  #{inv_no} — {supplier} — {currency} {amt} — due {due}{r}")

    if unpaid:
        lines.append(f"\nUNPAID ({len(unpaid)}):")
        for supplier, amount, currency, due, inv_no, reminders in unpaid:
            try:
                amt = f"{float(amount):,.2f}"
            except (ValueError, TypeError):
                amt = str(amount)
            lines.append(f"  #{inv_no} — {supplier} — {currency} {amt} — due {due}")

    if recent_paid:
        lines.append(f"\nRECENTLY PAID (last 7 days):")
        for supplier, amount, currency, inv_no in recent_paid:
            try:
                amt = f"{float(amount):,.2f}"
            except (ValueError, TypeError):
                amt = str(amount)
            lines.append(f"  #{inv_no} — {supplier} — {currency} {amt}")

    return "\n".join(lines)


def mark_invoice_paid(invoice_number: str) -> str:
    """Mark an invoice as paid in the database."""
    if not FINANCE_DB.exists():
        return "Finance database not available."
    conn = sqlite3.connect(FINANCE_DB)
    cursor = conn.execute(
        "UPDATE invoices SET status = 'paid' WHERE invoice_number = ? AND status IN ('unpaid', 'overdue')",
        (invoice_number,),
    )
    conn.commit()
    changed = cursor.rowcount
    conn.close()
    if changed > 0:
        log.info("Marked invoice #%s as paid (%d rows)", invoice_number, changed)
        return f"Marked invoice #{invoice_number} as paid ({changed} record(s) updated)."
    return f"No unpaid/overdue invoice found with number #{invoice_number}."


# ══════════════════════════════════════════════════════════════════════════════
# AGENT CONTEXT BUILDERS
# ══════════════════════════════════════════════════════════════════════════════
def get_agent_context(agent: str) -> str:
    if agent == "finance-manager":
        return get_finance_context()
    if agent == "shopify-ops":
        return get_shopify_products_context()
    if agent == "marketing":
        return get_shopify_revenue_context()
    if agent == "content":
        return (
            "Connie has deep knowledge of the Zipster brand (in her system prompt). "
            "She does not need external data to provide strategic content advice, campaign ideas, "
            "shoot briefs, and brand voice guidance."
        )
    finance_summary = get_finance_context()
    shopify_status = "CONNECTED" if SHOPIFY_API_KEY else "NOT CONNECTED"
    return (
        f"AGENT STATUS:\n"
        f"  Monty (Finance): LIVE — full invoice database\n"
        f"  Otto (Shopify Ops): Shopify API {shopify_status}\n"
        f"  Don (Marketing Signal): Shopify API {shopify_status} (ad spend APIs pending)\n"
        f"  Connie (Content): LIVE — brand strategy (no external data needed)\n\n"
        f"FINANCE SUMMARY (for cross-agent context):\n{finance_summary}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# CLAUDE RESPONSES + ROUTING
# ══════════════════════════════════════════════════════════════════════════════
def generate_response(agent: str, user_message: str) -> str:
    agent_config = AGENTS[agent]
    context = get_agent_context(agent)

    system = f"""{agent_config['system_prompt']}

You are responding to a message in the Zipster Slack workspace.
Keep responses concise and Slack-friendly — use *bold*, bullet points, and short paragraphs.
Do not use markdown headers (no #). Do not use code blocks for data — use plain text with bullets.
Never say "coming soon" or "placeholder" — either answer with data or explain exactly what's missing.

Here is the current data available to you:

{context}"""

    try:
        response = claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text.strip()
    except Exception as e:
        log.error("Claude API error for %s: %s", agent, e)
        return f"Apologies — running into a temporary issue. Will be back shortly. — {agent_config['name']}"


def handle_agent_message(agent: str, text: str, channel: str, user: str):
    """Route message through Nero or directly to the right agent."""
    channel_name = get_channel_name(channel)

    if agent == "zipster-command":
        nero_response = generate_response("zipster-command", text)

        route_match = re.search(r"ROUTE::(\S+)", nero_response)
        if route_match:
            target_agent = route_match.group(1)
            if target_agent in AGENTS and target_agent != "zipster-command":
                nero_clean = re.sub(r"ROUTE::\S+\s*", "", nero_response).strip()

                if nero_clean:
                    nero_config = AGENTS["zipster-command"]
                    nero_formatted = f"{nero_config['emoji']} *{nero_config['name']}* ({nero_config['title']})\n\n{nero_clean}"
                    try:
                        app.client.chat_postMessage(channel=channel, text=nero_formatted)
                    except Exception as e:
                        log.error("Failed to post Nero's message: %s", e)

                log.info("Nero routed to %s", target_agent)
                agent = target_agent
                response = generate_response(agent, text)
            else:
                response = re.sub(r"ROUTE::\S+\s*", "", nero_response).strip()
        else:
            response = nero_response
    else:
        response = generate_response(agent, text)

    # Handle MARK_PAID:: directives from Monty
    paid_matches = re.findall(r"MARK_PAID::(\S+)", response)
    for inv_no in paid_matches:
        result = mark_invoice_paid(inv_no)
        log.info("Mark paid result: %s", result)
    response = re.sub(r"MARK_PAID::\S+\s*", "", response).strip()

    agent_config = AGENTS[agent]
    formatted = f"{agent_config['emoji']} *{agent_config['name']}* ({agent_config['title']})\n\n{response}"

    log.info("Responding as %s (%s) in %s", agent_config["name"], agent, channel_name)
    try:
        app.client.chat_postMessage(channel=channel, text=formatted)
    except Exception as e:
        log.error("Failed to respond in channel %s: %s", channel, e)


# ══════════════════════════════════════════════════════════════════════════════
# SLACK BOLT EVENT HANDLERS
# ══════════════════════════════════════════════════════════════════════════════
@app.event("message")
def handle_message(event, say):
    # Ignore bot messages and message edits/deletes
    if event.get("bot_id") or event.get("subtype"):
        return

    text = event.get("text", "")
    channel = event.get("channel", "")
    user = event.get("user", "")
    channel_type = event.get("channel_type", "")  # "channel", "group", "im", "mpim"

    if not text.strip():
        return

    channel_name = get_channel_name(channel) if channel_type not in ("im", "mpim") else "DM"
    agent = route_to_agent(channel_name, text, channel_type)

    log.info(
        "Message | Channel: %s (%s) [%s] | User: %s | Agent: %s | Text: %s",
        channel, channel_name, channel_type, user, agent, text[:100],
    )

    handle_agent_message(agent, text, channel, user)


@app.event("app_mention")
def handle_mention(event, say):
    text = event.get("text", "")
    channel = event.get("channel", "")
    user = event.get("user", "")

    if not text.strip():
        return

    channel_name = get_channel_name(channel)
    agent = route_to_agent(channel_name, text)

    log.info(
        "Mention | Channel: %s (%s) | User: %s | Agent: %s | Text: %s",
        channel, channel_name, user, agent, text[:100],
    )

    handle_agent_message(agent, text, channel, user)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    log.info("Slack listener starting (Socket Mode)...")

    try:
        bot_info = app.client.auth_test()
        log.info("Authenticated as: %s (team: %s)", bot_info["user"], bot_info["team"])
    except Exception as e:
        log.error("Slack auth failed: %s", e)

    log.info("Shopify API: %s", "connected" if SHOPIFY_API_KEY else "NOT CONNECTED")
    log.info("Finance DB: %s", "found" if FINANCE_DB.exists() else "NOT FOUND")

    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()
