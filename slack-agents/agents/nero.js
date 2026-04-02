const { App } = require('@slack/bolt');
const config = require('../lib/config');
const claude = require('../lib/claude');
const personas = require('../lib/personas');
const { getFinanceContext, markInvoicePaid } = require('../lib/finance-db');
const { getRevenueContext, getProductsContext } = require('../lib/shopify');

const persona = personas.nero;

// Lazy-loaded references to other agents' respond functions
let agents = {};

function setAgents(agentMap) {
  agents = agentMap;
}

async function getAgentContext(agentId) {
  switch (agentId) {
    case 'finance-manager': return getFinanceContext();
    case 'marketing': return await getRevenueContext();
    case 'shopify-ops': return await getProductsContext();
    case 'content': return 'Connie has deep knowledge of the Zipster brand. She provides strategic content advice, campaign ideas, shoot briefs, and brand voice guidance.';
    default: return '';
  }
}

const AGENT_PERSONA_MAP = {
  'finance-manager': 'monty',
  'marketing': 'don',
  'shopify-ops': 'otto',
  'content': 'connie',
};

function create() {
  const app = new App({
    token: config.NERO_BOT_TOKEN,
    appToken: config.NERO_APP_TOKEN,
    socketMode: true,
  });

  app.event('message', async ({ event }) => {
    if (event.bot_id || event.subtype) return;
    if (!event.text || !event.text.trim()) return;
    await handleMessage(app, event.channel, event.text);
  });

  app.event('app_mention', async ({ event }) => {
    if (!event.text || !event.text.trim()) return;
    await handleMessage(app, event.channel, event.text);
  });

  return app;
}

async function handleMessage(neroApp, channel, text) {
  // Get Nero's routing decision
  const neroContext = getNeroContext();
  const neroSystem = `${persona.system}\n\n${neroContext}`;

  let neroResponse;
  try {
    neroResponse = await claude.ask(neroSystem, text);
  } catch (err) {
    console.error(`[Nero] Claude error: ${err.message}`);
    await postAsNero(neroApp, channel, 'Apologies \u2014 running into a temporary issue. Will be back shortly. \u2014 Nero');
    return;
  }

  // Check if Nero wants to route
  const routeMatch = neroResponse.match(/ROUTE::(\S+)/);
  if (routeMatch) {
    const targetAgentId = routeMatch[1];
    const personaKey = AGENT_PERSONA_MAP[targetAgentId];

    if (personaKey && personas[personaKey]) {
      // Post Nero's routing message (stripped of ROUTE:: directive)
      const neroClean = neroResponse.replace(/ROUTE::\S+\s*/g, '').trim();
      if (neroClean) {
        await postAsNero(neroApp, channel, neroClean);
      }

      // Generate the target agent's response with their persona + live data
      console.log(`[Nero] Routing to ${targetAgentId}`);
      const targetPersona = personas[personaKey];

      let context;
      try {
        context = await getAgentContext(targetAgentId);
      } catch (err) {
        console.error(`[Nero] Context error for ${targetAgentId}: ${err.message}`);
        context = 'Data temporarily unavailable.';
      }

      const targetSystem = `${targetPersona.system}\n\nHere is the current data available to you:\n\n${context}`;

      let agentResponse;
      try {
        agentResponse = await claude.ask(targetSystem, text);
      } catch (err) {
        console.error(`[Nero] Claude error for ${personaKey}: ${err.message}`);
        agentResponse = `Apologies \u2014 running into a temporary issue. Will be back shortly. \u2014 ${targetPersona.name}`;
      }

      // Handle MARK_PAID:: from Monty
      if (targetAgentId === 'finance-manager') {
        const paidMatches = agentResponse.match(/MARK_PAID::(\S+)/g) || [];
        for (const match of paidMatches) {
          const invNo = match.replace('MARK_PAID::', '');
          const result = markInvoicePaid(invNo);
          console.log(`[Nero/Monty] ${result}`);
        }
        agentResponse = agentResponse.replace(/MARK_PAID::\S+\s*/g, '').trim();
      }

      // Post as the target agent (using their app if available, otherwise Nero's app)
      if (agents[personaKey]) {
        const formatted = `${targetPersona.emoji} *${targetPersona.name}* (${targetPersona.title})\n\n${agentResponse}`;
        try {
          await agents[personaKey].app.client.chat.postMessage({ channel, text: formatted });
          console.log(`[Nero] ${targetPersona.name} responded in ${channel}`);
        } catch (err) {
          console.error(`[Nero] Failed to post as ${targetPersona.name}: ${err.message}`);
          // Fallback: post via Nero's app
          await postAsAgent(neroApp, channel, targetPersona, agentResponse);
        }
      } else {
        await postAsAgent(neroApp, channel, targetPersona, agentResponse);
      }
    } else {
      // Unknown route target, post Nero's response as-is
      const clean = neroResponse.replace(/ROUTE::\S+\s*/g, '').trim();
      await postAsNero(neroApp, channel, clean);
    }
  } else {
    // Nero answers directly
    await postAsNero(neroApp, channel, neroResponse);
  }
}

function getNeroContext() {
  const finSummary = getFinanceContext();
  const shopifyStatus = config.SHOPIFY_API_KEY ? 'CONNECTED' : 'NOT CONNECTED';
  return [
    'AGENT STATUS:',
    '  Monty (Finance): LIVE \u2014 full invoice database',
    `  Otto (Shopify Ops): Shopify API ${shopifyStatus}`,
    `  Don (Marketing Signal): Shopify API ${shopifyStatus} (ad spend APIs pending)`,
    '  Connie (Content): LIVE \u2014 brand strategy (no external data needed)',
    '',
    `FINANCE SUMMARY (for cross-agent context):\n${finSummary}`,
  ].join('\n');
}

async function postAsNero(app, channel, text) {
  const formatted = `${persona.emoji} *${persona.name}* (${persona.title})\n\n${text}`;
  try {
    await app.client.chat.postMessage({ channel, text: formatted });
  } catch (err) {
    console.error(`[Nero] Slack error: ${err.message}`);
  }
}

async function postAsAgent(app, channel, agentPersona, text) {
  const formatted = `${agentPersona.emoji} *${agentPersona.name}* (${agentPersona.title})\n\n${text}`;
  try {
    await app.client.chat.postMessage({ channel, text: formatted });
  } catch (err) {
    console.error(`[Nero] Slack error posting as ${agentPersona.name}: ${err.message}`);
  }
}

module.exports = { create, setAgents, persona };
