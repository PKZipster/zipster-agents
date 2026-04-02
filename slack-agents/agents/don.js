const { App } = require('@slack/bolt');
const config = require('../lib/config');
const claude = require('../lib/claude');
const personas = require('../lib/personas');
const { getRevenueContext } = require('../lib/shopify');

const persona = personas.don;

function create() {
  const app = new App({
    token: config.DON_BOT_TOKEN,
    appToken: config.DON_APP_TOKEN,
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
    context = await getRevenueContext();
  } catch (err) {
    console.error(`[Don] Shopify error: ${err.message}`);
    context = 'Failed to fetch Shopify data. API may be temporarily unavailable.';
  }

  const system = `${persona.system}\n\nHere is the current data available to you:\n\n${context}`;

  let response;
  try {
    response = await claude.ask(system, text);
  } catch (err) {
    console.error(`[Don] Claude error: ${err.message}`);
    response = 'Apologies \u2014 running into a temporary issue. Will be back shortly. \u2014 Don';
  }

  const formatted = `${persona.emoji} *${persona.name}* (${persona.title})\n\n${response}`;
  try {
    await app.client.chat.postMessage({ channel, text: formatted });
    console.log(`[Don] Responded in ${channel}`);
  } catch (err) {
    console.error(`[Don] Slack error: ${err.message}`);
  }
}

module.exports = { create, respond, persona };
