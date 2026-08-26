// pm2 start ecosystem.config.cjs   (then: pm2 save && pm2 startup)
module.exports = {
  apps: [
    {
      name: 'log-brain',
      script: '.venv/bin/python',
      args: '-m app.main',
      interpreter: 'none',
      cwd: __dirname,
      autorestart: true,
      max_restarts: 20,
      restart_delay: 3000,
    },
    {
      name: 'log-bridge',
      script: 'bridge/index.js',
      cwd: __dirname,
      autorestart: true,
      max_restarts: 20,
      restart_delay: 3000,
    },
  ],
}
