/**
 * whatsapp_service/server.js
 * ─────────────────────────
 * Tiny HTTP server (port 3001) that manages a WhatsApp Web session.
 * The Python FastAPI backend proxies to this.
 *
 * Endpoints:
 *   GET  /status   → { state: "qr"|"authenticated"|"disconnected", qr?: base64png }
 *   POST /start    → initialise / re-initialise the session
 *   POST /send     → { to: "...", message: "..." }
 *   POST /logout   → destroy session
 */

const http       = require('http');
const { Client, LocalAuth } = require('whatsapp-web.js');
const QRCode     = require('qrcode');
const path       = require('path');
const fs         = require('fs');

const PORT       = process.env.WA_PORT || 3001;
const SESSION_DIR = path.join(__dirname, '.wwebjs_auth');

// ── State ─────────────────────────────────────────────────────────────────────
let state       = 'idle';   // idle | qr | authenticated | disconnected
let currentQR   = null;     // base64 PNG of the latest QR code
let client      = null;

// ── WhatsApp client factory ───────────────────────────────────────────────────
function createClient() {
  if (client) {
    try { client.destroy(); } catch (_) {}
    client = null;
  }

  client = new Client({
    authStrategy: new LocalAuth({ dataPath: SESSION_DIR }),
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    puppeteer: {
      headless: true,
      protocolTimeout: 0,
      args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--disable-gpu',
      ]
    }
  });

  client.on('qr', async (qr) => {
    console.log('[WA] QR received');
    state     = 'qr';
    currentQR = await QRCode.toDataURL(qr);   // base64 PNG
  });

  client.on('authenticated', () => {
    console.log('[WA] Authenticated ✓');
    state     = 'authenticated';
    currentQR = null;
  });

  client.on('ready', () => {
    console.log('[WA] Client ready');
    state = 'authenticated';
  });

  client.on('auth_failure', (msg) => {
    console.error('[WA] Auth failure:', msg);
    state     = 'disconnected';
    currentQR = null;
  });

  client.on('disconnected', (reason) => {
    console.warn('[WA] Disconnected:', reason);
    state     = 'disconnected';
    currentQR = null;
  });

  state = 'starting';

  // Remove stale Chromium lock files from previous crashes
  const lockFiles = [
    path.join(SESSION_DIR, 'session', 'SingletonLock'),
    path.join(SESSION_DIR, 'session', 'SingletonCookie'),
    path.join(SESSION_DIR, 'session', 'lockfile'),
  ];
  lockFiles.forEach(f => { try { fs.unlinkSync(f); } catch (_) {} });

  client.initialize();
}

// ── HTTP server ───────────────────────────────────────────────────────────────
function sendJSON(res, status, body) {
  res.writeHead(status, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
  res.end(JSON.stringify(body));
}

const server = http.createServer(async (req, res) => {
  // CORS pre-flight
  if (req.method === 'OPTIONS') {
    res.writeHead(204, { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'GET,POST', 'Access-Control-Allow-Headers': 'Content-Type' });
    res.end();
    return;
  }

  // Parse body for POST
  let body = {};
  if (req.method === 'POST') {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    try { body = JSON.parse(Buffer.concat(chunks).toString()); } catch (_) {}
  }

  // ── Routes ──────────────────────────────────────────────────────────────────

  if (req.method === 'GET' && req.url === '/status') {
    return sendJSON(res, 200, { state, qr: currentQR });
  }

  if (req.method === 'POST' && req.url === '/start') {
    if (state === 'authenticated') {
      return sendJSON(res, 200, { ok: true, state });
    }
    createClient();
    return sendJSON(res, 200, { ok: true, state: 'starting' });
  }

  if (req.method === 'POST' && req.url === '/send') {
    const { to, message } = body;
    if (state !== 'authenticated') {
      return sendJSON(res, 400, { error: 'Not authenticated. Scan QR first.' });
    }
    if (!to || !message) {
      return sendJSON(res, 400, { error: 'Missing to or message' });
    }
    try {
      // WhatsApp number format: countrycode+number@c.us (no +)
      const chatId = to.replace(/[^0-9]/g, '') + '@c.us';
      await client.sendMessage(chatId, message);
      return sendJSON(res, 200, { ok: true });
    } catch (e) {
      return sendJSON(res, 500, { error: e.message });
    }
  }

  if (req.method === 'POST' && req.url === '/logout') {
    if (client) {
      try { await client.logout(); } catch (_) {}
      try { client.destroy(); } catch (_) {}
      client  = null;
    }
    // Remove saved session
    if (fs.existsSync(SESSION_DIR)) {
      fs.rmSync(SESSION_DIR, { recursive: true, force: true });
    }
    state     = 'idle';
    currentQR = null;
    return sendJSON(res, 200, { ok: true });
  }

  sendJSON(res, 404, { error: 'Not found' });
});

server.listen(PORT, '127.0.0.1', () => {
  console.log(`[WA] WhatsApp service listening on port ${PORT}`);
  // Auto-start so a saved session is restored immediately
  createClient();
});

process.on('SIGTERM', () => { try { client?.destroy(); } catch(_) {} process.exit(0); });
process.on('SIGINT',  () => { try { client?.destroy(); } catch(_) {} process.exit(0); });
