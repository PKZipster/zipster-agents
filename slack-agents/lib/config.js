const path = require('path');
require('dotenv').config({ path: path.resolve(__dirname, '../../shared/config/secrets.env') });

module.exports = {
  ANTHROPIC_API_KEY: process.env.ANTHROPIC_API_KEY,
  SHOPIFY_API_KEY: process.env.SHOPIFY_API_KEY,
  SHOPIFY_STORE: process.env.SHOPIFY_STORE || 'eu-zipsterbaby',
  FINANCE_DB_PATH: path.resolve(__dirname, '../../data/finance-manager.db'),

  NERO_BOT_TOKEN: process.env.NERO_BOT_TOKEN,
  NERO_APP_TOKEN: process.env.NERO_APP_TOKEN,
  MONTY_BOT_TOKEN: process.env.MONTY_BOT_TOKEN,
  MONTY_APP_TOKEN: process.env.MONTY_APP_TOKEN,
  DON_BOT_TOKEN: process.env.DON_BOT_TOKEN,
  DON_APP_TOKEN: process.env.DON_APP_TOKEN,
  OTTO_BOT_TOKEN: process.env.OTTO_BOT_TOKEN,
  OTTO_APP_TOKEN: process.env.OTTO_APP_TOKEN,
  CONNIE_BOT_TOKEN: process.env.CONNIE_BOT_TOKEN,
  CONNIE_APP_TOKEN: process.env.CONNIE_APP_TOKEN,
};
