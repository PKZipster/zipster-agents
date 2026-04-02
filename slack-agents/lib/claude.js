const axios = require('axios');
const { ANTHROPIC_API_KEY } = require('./config');

async function ask(systemPrompt, userMessage, maxTokens = 1500) {
  const res = await axios.post('https://api.anthropic.com/v1/messages', {
    model: 'claude-sonnet-4-20250514',
    max_tokens: maxTokens,
    system: systemPrompt,
    messages: [{ role: 'user', content: userMessage }],
  }, {
    headers: {
      'x-api-key': ANTHROPIC_API_KEY,
      'anthropic-version': '2023-06-01',
      'content-type': 'application/json',
    },
    timeout: 60000,
  });
  return res.data.content[0].text.trim();
}

module.exports = { ask };
