const { App } = require('@slack/bolt');
const config = require('../lib/config');
const claude = require('../lib/claude');
const personas = require('../lib/personas');

const persona = personas.connie;

function create() {
  const app = new App({
    token: config.CONNIE_BOT_TOKEN,
    appToken: config.CONNIE_APP_TOKEN,
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
  const context = 'Connie has deep knowledge of the Zipster brand (in her system prompt). She does not need external data to provide strategic content advice, campaign ideas, shoot briefs, and brand voice guidance.';
  const system = `${persona.system}\n\n${context}`;

  let response;
  try {
    response = await claude.ask(system, text);
  } catch (err) {
    console.error(`[Connie] Claude error: ${err.message}`);
    response = 'Apologies \u2014 running into a temporary issue. Will be back shortly. \u2014 Connie';
  }

  const formatted = `${persona.emoji} *${persona.name}* (${persona.title})\n\n${response}`;
  try {
    await app.client.chat.postMessage({ channel, text: formatted });
    console.log(`[Connie] Responded in ${channel}`);
  } catch (err) {
    console.error(`[Connie] Slack error: ${err.message}`);
  }
}

module.exports = { create, respond, persona };
