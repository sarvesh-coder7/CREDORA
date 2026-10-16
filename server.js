import http from 'http';
import { createApp } from './src/app.js';
import config from './src/config/index.js';

const app = createApp();
const desiredPort = config.PORT || 8000;

function startServer(port) {
  const server = http.createServer(app);

  server.listen(port, '0.0.0.0', () => {
    console.log(`
╔══════════════════════════════════════════════════════════════════════╗
║                   CREDORA BACKEND REST API ONLINE                    ║
║    Autonomous AI Underwriting & Loan Document Processing Engine      ║
╠══════════════════════════════════════════════════════════════════════╣
║  • Local Server URL   : http://localhost:${port}                       ║
║  • Health Check       : http://localhost:${port}/api/health             ║
║  • API Documentation  : http://localhost:${port}/api                    ║
║  • Frontend Web UI    : http://localhost:${port}/index.html             ║
║  • Database Engine    : Supabase PostgreSQL                          ║
║  • AI Document Engine : Gemini 2.0 Flash / Fallback Financial Engine ║
╚══════════════════════════════════════════════════════════════════════╝
    `);
  });

  server.on('error', (err) => {
    if (err.code === 'EADDRINUSE') {
      console.warn(`[CREDORA Server] Port ${port} is in use. Trying fallback port ${port === 8000 ? 3000 : port + 1}...`);
      startServer(port === 8000 ? 3000 : port + 1);
    } else {
      console.error('[CREDORA Server] Unexpected server error:', err);
      process.exit(1);
    }
  });

  const shutdown = () => {
    console.log('\n[CREDORA Server] Gracefully shutting down HTTP server...');
    server.close(() => {
      console.log('[CREDORA Server] Closed.');
      process.exit(0);
    });
  };

  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);

  return server;
}

startServer(desiredPort);
