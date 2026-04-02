const { App } = require('@slack/bolt');
const config = require('../lib/config');
const claude = require('../lib/claude');
const personas = require('../lib/personas');
const { getProductsContext } = require('../lib/shopify');

const persona = personas.otto;

function create() {
  const app = new App({
    token: config.OTTO_BOT_TOKEN,
    appToken: config.OTTO_APP_TOKEN,
    socketMode: true,
  });

  app.event('message', async ({ event }) => {
    if (event.bot_id || event.subtype) return;
    if (!event.text || !event.text.trim()) return;
    await respond(app, event.channel, event.text);
  });

  app.event('app_mention', async ({ event }) => {
    if (!event.text || !event.text.trim()) return;
    await respond(app, event.channel, event.text);
  });

  return app;
}

async function respond(app, channel, text) {
  let context;
  try {
    context = await getProductsContext();
  } catch (err) {
    console.error(`[Otto] Shopify error: ${err.message}`);
    context = 'Failed to fetch Shopify product data. API may be temporarily unavailable.';
  }

  const system = `${persona.system}\n\nHere is the current data available to you:\n\n${context}`;

  let response;
  try {
    response = await claude.ask(system, text);
  } catch (err) {
    console.error(`[Otto] Claude error: ${err.message}`);
    response = 'Apologies \u2014 running into a temporary issue. Will be back shortly. \u2014 Otto';
  }

  const formatted = `${persona.emoji} *${persona.name}* (${persona.title})\n\n${response}`;
  try {
    await app.client.chat.postMessage({ channel, text: formatted });
    console.log(`[Otto] Responded in ${channel}`);
  } catch (err) {
    console.error(`[Otto] Slack error: ${err.message}`);
  }
}

module.exports = { create, respond, persona };
