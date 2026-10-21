// Example for the existing kayo PM2 account. Preserve your current storage paths on upgrades.
const path = require('node:path');
const os = require('node:os');
const checkout = '/home/kayo/apps/pocket';
const storage = path.join(os.homedir(), '.local', 'share', 'pocket');
module.exports = {
  apps: [{
    name: 'pocket',
    cwd: checkout,
    script: path.join(checkout, '.venv', 'bin', 'gunicorn'),
    interpreter: 'none',
    args: '--bind 127.0.0.1:8768 --workers 1 --threads 4 --timeout 30 --graceful-timeout 5 --error-logfile - production:create_app()',
    exec_mode: 'fork', instances: 1, autorestart: true, watch: false,
    restart_delay: 3000, kill_timeout: 10000,
    env: {
      POCKET_DOMAIN: 'pocket.mrox.in',
      POCKET_DATA_DIR: path.join(storage, 'data'),
      POCKET_AUTH_FILE: path.join(storage, 'auth.json'),
      POCKET_VAULT: path.join(storage, 'vault-export')
    }
  }]
};
