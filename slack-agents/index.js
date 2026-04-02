const nero = require('./agents/nero');
const monty = require('./agents/monty');
const don = require('./agents/don');
const otto = require('./agents/otto');
const connie = require('./agents/connie');

async function main() {
  console.log('Starting Zipster Slack agents...\n');

  // Create all apps
  const neroApp = nero.create();
  const montyApp = monty.create();
  const donApp = don.create();
  const ottoApp = otto.create();
  const connieApp = connie.create();

  // Give Nero references to other agents so he can post as them
  nero.setAgents({
    monty: { app: montyApp },
    don: { app: donApp },
    otto: { app: ottoApp },
    connie: { app: connieApp },
  });

  // Start all Socket Mode connections in parallel
  const agents = [
    { name: 'Nero',   app: neroApp },
    { name: 'Monty',  app: montyApp },
    { name: 'Don',    app: donApp },
    { name: 'Otto',   app: ottoApp },
    { name: 'Connie', app: connieApp },
  ];

  await Promise.all(agents.map(async ({ name, app }) => {
    try {
      await app.start();
      console.log(`  [${name}] connected`);
    } catch (err) {
      console.error(`  [${name}] FAILED: ${err.message}`);
    }
  }));

  console.log('\nAll agents running.\n');
}

main().catch(err => {
  console.error('Fatal error:', err);
  process.exit(1);
});
