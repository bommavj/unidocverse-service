/**
 * UniDocVerse Banking Chatbot Widget
 *
 * Drop this on any bank website:
 *
 *   <script
 *     src="https://your-server.com/static/chatbot-widget.js"
 *     data-api="https://your-server.com"
 *     data-title="First Valley Bank Assistant"
 *     data-color="#1a56db"
 *     data-position="bottom-right"
 *   ></script>
 *
 * Attributes:
 *   data-api       — UniDocVerse server URL (required)
 *   data-title     — Chatbot name, e.g. "Bomma" or "Erica" (default: "AI Assistant")
 *   data-subtitle  — Header subtext (default: "Ask questions about your documents")
 *   data-color     — Primary hex color (default: #1a56db)
 *   data-icon      — Emoji icon shown in header (default: 🏦)
 *   data-welcome   — Welcome heading (default: "Hello! How can I help?")
 *   data-hint      — Welcome subtext
 *   data-position  — "bottom-right" | "bottom-left" (default: bottom-right)
 */
(function () {
  const script   = document.currentScript;
  const API      = (script.getAttribute('data-api') || '').replace(/\/$/, '');
  const TITLE    = script.getAttribute('data-title')    || 'AI Assistant';
  const SUBTITLE = script.getAttribute('data-subtitle') || '';
  const COLOR    = script.getAttribute('data-color')    || '#1a56db';
  const ICON     = script.getAttribute('data-icon')     || '🏦';
  const WELCOME  = script.getAttribute('data-welcome')  || '';
  const HINT     = script.getAttribute('data-hint')     || '';
  const POSITION = script.getAttribute('data-position') || 'bottom-right';

  if (!API) { console.error('[UniDocVerse] data-api is required'); return; }

  const isRight = POSITION !== 'bottom-left';
  const side    = isRight ? 'right' : 'left';

  // ── Styles ──────────────────────────────────────────────────────────────────
  const style = document.createElement('style');
  style.textContent = `
    #udv-wrap {
      position: fixed; bottom: 24px; ${side}: 24px; z-index: 99998;
      display: flex; flex-direction: ${isRight ? 'row-reverse' : 'row'};
      align-items: flex-end; gap: 10px;
    }
    #udv-fab {
      width: 60px; height: 60px; border-radius: 50%;
      background: ${COLOR}; color: #fff; border: none;
      box-shadow: 0 4px 16px rgba(0,0,0,.25);
      cursor: pointer; font-size: 26px;
      display: flex; align-items: center; justify-content: center;
      transition: transform .2s, box-shadow .2s;
      position: relative; flex-shrink: 0;
    }
    #udv-fab:hover { transform: scale(1.08); box-shadow: 0 6px 24px rgba(0,0,0,.32); }
    #udv-fab:active { transform: scale(.95); }

    /* Pulse ring — draws the eye */
    #udv-fab::before {
      content: '';
      position: absolute; inset: -4px;
      border-radius: 50%;
      border: 3px solid ${COLOR};
      opacity: 0;
      animation: udv-pulse 2.4s ease-out infinite;
    }
    #udv-fab::after {
      content: '';
      position: absolute; inset: -10px;
      border-radius: 50%;
      border: 2px solid ${COLOR};
      opacity: 0;
      animation: udv-pulse 2.4s ease-out .6s infinite;
    }
    @keyframes udv-pulse {
      0%   { transform: scale(.9);  opacity: .7; }
      70%  { transform: scale(1.35); opacity: 0; }
      100% { transform: scale(1.35); opacity: 0; }
    }

    /* Tooltip bubble */
    #udv-tooltip {
      background: #fff;
      border: 1px solid rgba(0,0,0,.1);
      border-radius: 16px;
      box-shadow: 0 4px 20px rgba(0,0,0,.14);
      padding: 10px 14px;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      font-size: 13px; line-height: 1.4;
      color: #111; max-width: 200px;
      cursor: pointer;
      position: relative;
      animation: udv-fadein .4s ease;
      transition: opacity .3s;
    }
    #udv-tooltip.hide { opacity: 0; pointer-events: none; }
    #udv-tooltip strong { display: block; font-size: 13px; margin-bottom: 2px; color: ${COLOR}; }
    #udv-tooltip span   { font-size: 12px; color: #555; }
    /* Arrow pointing toward FAB */
    #udv-tooltip::after {
      content: '';
      position: absolute; bottom: 14px;
      ${isRight ? 'right: -7px; border-left: 7px solid #fff;' : 'left: -7px; border-right: 7px solid #fff;'}
      border-top: 6px solid transparent;
      border-bottom: 6px solid transparent;
    }
    #udv-tooltip::before {
      content: '';
      position: absolute; bottom: 13px;
      ${isRight ? 'right: -8px; border-left: 8px solid rgba(0,0,0,.1);' : 'left: -8px; border-right: 8px solid rgba(0,0,0,.1);'}
      border-top: 7px solid transparent;
      border-bottom: 7px solid transparent;
    }
    #udv-close-tip {
      position: absolute; top: 6px; ${isRight ? 'left' : 'right'}: 8px;
      background: none; border: none; cursor: pointer;
      font-size: 14px; color: #aaa; line-height: 1;
      padding: 0 2px;
    }
    #udv-close-tip:hover { color: #555; }
    @keyframes udv-fadein {
      from { opacity: 0; transform: translateY(8px); }
      to   { opacity: 1; transform: translateY(0); }
    }

    #udv-panel {
      position: fixed; bottom: 96px; ${side}: 24px; z-index: 99999;
      width: 380px; height: 600px; max-height: calc(100dvh - 116px);
      border-radius: 16px; overflow: hidden;
      box-shadow: 0 8px 40px rgba(0,0,0,.18);
      border: 1px solid rgba(0,0,0,.08);
      display: none; flex-direction: column;
      transform: scale(.92) translateY(12px);
      opacity: 0;
      transition: transform .22s cubic-bezier(.34,1.56,.64,1), opacity .18s ease;
    }
    #udv-panel.open {
      display: flex;
      transform: scale(1) translateY(0);
      opacity: 1;
    }
    #udv-panel iframe { width: 100%; flex: 1; border: none; }
    @media (max-width: 440px) {
      #udv-panel { width: calc(100vw - 16px); ${side}: 8px; bottom: 84px; height: calc(100dvh - 104px); border-radius: 12px; }
      #udv-tooltip { max-width: 160px; }
    }
  `;
  document.head.appendChild(style);

  // ── DOM ─────────────────────────────────────────────────────────────────────
  const wrap = document.createElement('div');
  wrap.id = 'udv-wrap';

  const fab = document.createElement('button');
  fab.id = 'udv-fab';
  fab.title = TITLE;
  fab.textContent = '💬';

  const tip = document.createElement('div');
  tip.id = 'udv-tooltip';
  tip.innerHTML = `
    <button id="udv-close-tip" title="Dismiss">✕</button>
    <strong>👋 Hi there!</strong>
    <span>Ask me anything about your documents.</span>
  `;

  wrap.appendChild(tip);
  wrap.appendChild(fab);

  // ── Panel ───────────────────────────────────────────────────────────────────
  const panel = document.createElement('div');
  panel.id = 'udv-panel';

  const _q = new URLSearchParams({ title: TITLE, color: COLOR, icon: ICON, embedded: '1' });
  if (SUBTITLE) _q.set('subtitle', SUBTITLE);
  if (WELCOME)  _q.set('welcome',  WELCOME);
  if (HINT)     _q.set('hint',     HINT);
  _q.set('v', '2');
  const chatUrl = `${API}/chatbot?${_q}`;
  const iframe  = document.createElement('iframe');
  iframe.src    = chatUrl;
  iframe.title  = TITLE;
  iframe.allow  = 'clipboard-write';
  panel.appendChild(iframe);

  document.body.appendChild(panel);
  document.body.appendChild(wrap);

  // ── Tooltip auto-dismiss ──────────────────────────────────────────────────
  let tipDismissed = false;
  function hideTip() {
    tip.classList.add('hide');
    tipDismissed = true;
  }
  setTimeout(() => { if (!tipDismissed) hideTip(); }, 6000);
  document.getElementById('udv-close-tip').addEventListener('click', (e) => {
    e.stopPropagation(); hideTip();
  });
  tip.addEventListener('click', () => { hideTip(); openChat(); });

  // ── Toggle ──────────────────────────────────────────────────────────────────
  let open = false;

  function openChat() {
    open = true;
    fab.textContent = '✕';
    hideTip();
    panel.style.display = 'flex';
    requestAnimationFrame(() => panel.classList.add('open'));
  }
  function closeChat() {
    open = false;
    fab.textContent = '💬';
    panel.classList.remove('open');
    setTimeout(() => { panel.style.display = 'none'; }, 220);
  }

  fab.addEventListener('click', () => open ? closeChat() : openChat());

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && open) closeChat();
  });
})();
