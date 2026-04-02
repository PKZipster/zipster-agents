#!/usr/bin/env python3
"""Refresh Shopify Admin API access token (client_credentials grant) and update secrets.env."""

import json
import logging
import os
import re
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]
SECRETS_ENV = BASE_DIR / "shared" / "config" / "secrets.env"
LOG_PATH = BASE_DIR / "logs" / "shopify-token-refresh.log"

LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("shopify-token-refresh")

load_dotenv(SECRETS_ENV)
CLIENT_ID = os.getenv("SHOPIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SHOPIFY_CLIENT_SECRET")
SHOP = "eu-zipsterbaby.myshopify.com"


def refresh_token():
    log.info("Requesting new Shopify access token...")

    payload = json.dumps({
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "client_credentials",
    }).encode("utf-8")

    req = urllib.request.Request(
        f"https://{SHOP}/admin/oauth/access_token",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        log.error("Token request failed: %s", e)
        return False

    new_token = data.get("access_token")
    if not new_token:
        log.error("No access_token in response: %s", data)
        return False

    log.info("Got new token: %s...", new_token[:12])

    # Update secrets.env in place
    content = SECRETS_ENV.read_text()
    if "SHOPIFY_API_KEY=" in content:
        content = re.sub(r"SHOPIFY_API_KEY=.*", f"SHOPIFY_API_KEY={new_token}", content)
    else:
        content += f"\nSHOPIFY_API_KEY={new_token}\n"
    SECRETS_ENV.write_text(content)

    log.info("Updated secrets.env with new token.")

    # Verify the token works
    verify_req = urllib.request.Request(
        f"https://{SHOP}/admin/api/2024-01/shop.json",
        headers={
            "X-Shopify-Access-Token": new_token,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(verify_req, timeout=10) as resp:
            if resp.status == 200:
                log.info("Token verified — API responding 200.")
                return True
    except Exception as e:
        log.error("Token verification failed: %s", e)
        return False


if __name__ == "__main__":
    refresh_token()
