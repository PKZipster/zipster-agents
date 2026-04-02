const { App } = require('@slack/bolt');
const config = require('../lib/config');
const claude = require('../lib/claude');
const personas = require('../lib/personas');
const { getFinanceContext, markInvoicePaid } = require('../lib/finance-db');

const persona = personas.monty;

function create() {
  const app = new App({
    token: config.MONTY_BOT_TOKEN,
    appToken: config.MONTY_APP_TOKEN,
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
  const context = getFinanceContext();
  const system = `${persona.system}\n\nHere is the current data available to you:\n\n${context}`;

  let response;
  try {
    response = await claude.ask(system, text);
  } catch (err) {
    console.error(`[Monty] Claude error: ${err.message}`);
    response = 'Apologies \u2014 running into a temporary issue. Will be back shortly. \u2014 Monty';
  }

  // Handle MARK_PAID:: directives
  const paidMatches = response.match(/MARK_PAID::(\S+)/g) || [];
  for (const match of paidMatches) {
    const invNo = match.replace('MARK_PAID::', '');
    const result = markInvoicePaid(invNo);
    console.log(`[Monty] ${result}`);
  }
  response = response.replace(/MARK_PAID::\S+\s*/g, '').trim();

  const formatted = `${persona.emoji} *${persona.name}* (${persona.title})\n\n${response}`;
  try {
    await app.client.chat.postMessage({ channel, text: formatted });
    console.log(`[Monty] Responded in ${channel}`);
  } catch (err) {
    console.error(`[Monty] Slack error: ${err.message}`);
  }
}

module.exports = { create, respond, persona };
