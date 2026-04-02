const fs = require('fs');
const path = require('path');
const axios = require('axios');
require('dotenv').config({ path: path.resolve(__dirname, '../shared/config/secrets.env') });

const SECRETS_PATH = path.resolve(__dirname, '../shared/config/secrets.env');
const SHOP = 'eu-zipsterbaby.myshopify.com';

const log = (msg) => console.log(`[${new Date().toISOString()}] ${msg}`);

async function refreshShopifyToken() {
  const clientId = process.env.SHOPIFY_CLIENT_ID;
  const clientSecret = process.env.SHOPIFY_CLIENT_SECRET;

  if (!clientId || !clientSecret) {
    log('SHOPIFY: Skipping — no client credentials configured.');
    return;
  }

  log('SHOPIFY: Requesting new access token...');
  try {
    const res = await axios.post(`https://${SHOP}/admin/oauth/access_token`, {
      client_id: clientId,
      client_secret: clientSecret,
      grant_type: 'client_credentials',
    }, { timeout: 15000 });

    const newToken = res.data.access_token;
    if (!newToken) {
      log('SHOPIFY: No access_token in response.');
      return;
    }

    log(`SHOPIFY: Got new token: ${newToken.slice(0, 12)}...`);

    // Update secrets.env
    let content = fs.readFileSync(SECRETS_PATH, 'utf-8');
    if (content.includes('SHOPIFY_API_KEY=')) {
      content = content.replace(/SHOPIFY_API_KEY=.*/, `SHOPIFY_API_KEY=${newToken}`);
    } else {
      content += `\nSHOPIFY_API_KEY=${newToken}\n`;
    }
    fs.writeFileSync(SECRETS_PATH, content);
    log('SHOPIFY: Updated secrets.env.');

    // Verify
    const verify = await axios.get(`https://${SHOP}/admin/api/2024-01/shop.json`, {
      headers: { 'X-Shopify-Access-Token': newToken },
      timeout: 10000,
    });
    log(`SHOPIFY: Verified — API returned ${verify.status}.`);
  } catch (err) {
    log(`SHOPIFY: Error — ${err.message}`);
  }
}

async function refreshClaudeCodeAuth() {
  // Keep Claude Code OAuth session alive by making a minimal API call
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    log('CLAUDE: Skipping — no API key configured.');
    return;
  }

  log('CLAUDE: Pinging API to keep OAuth token alive...');
  try {
    const res = await axios.post('https://api.anthropic.com/v1/messages', {
      model: 'claude-haiku-4-5-20251001',
      max_tokens: 1,
      messages: [{ role: 'user', content: 'ping' }],
    }, {
      headers: {
        'x-api-key': apiKey,
        'anthropic-version': '2023-06-01',
        'content-type': 'application/json',
      },
      timeout: 15000,
    });
    log(`CLAUDE: API responded ${res.status} — token alive.`);
  } catch (err) {
    if (err.response) {
      log(`CLAUDE: API returned ${err.response.status} — ${err.response.data?.error?.message || 'unknown error'}`);
    } else {
      log(`CLAUDE: Error — ${err.message}`);
    }
  }
}

(async () => {
  log('=== Auth refresh starting ===');
  await refreshShopifyToken();
  await refreshClaudeCodeAuth();
  log('=== Auth refresh complete ===\n');
})();
