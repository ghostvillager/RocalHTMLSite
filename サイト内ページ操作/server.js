const express = require('express');
const http = require('http');
const { WebSocketServer } = require('ws');
const { chromium } = require('playwright');
const path = require('path');
const { exec } = require('child_process');

const app = express();
const server = http.createServer(app);
const wss = new WebSocketServer({ server });

app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));
app.use((req, res, next) => {
  res.header('Access-Control-Allow-Origin', '*');
  res.header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.header('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.sendStatus(200);
  next();
});

let browser, page;
let isStreaming = false;
let viewport = { width: 1280, height: 720 };
let busy = Promise.resolve();
let latestMove = null;
let moveTimer = null;
let lastCaret = null;

function withPage(fn) {
  const run = busy.then(fn, fn);
  busy = run.then(() => {}, () => {});
  return run;
}

function broadcast(obj) {
  const message = JSON.stringify(obj);
  wss.clients.forEach(client => {
    if (client.readyState === 1) client.send(message);
  });
}

async function startBrowser() {
  browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--force-device-scale-factor=1']
  });
  const context = await browser.newContext({ viewport, deviceScaleFactor: 1 });
  page = await context.newPage();
  await page.goto('https://duckduckgo.com', { waitUntil: 'domcontentloaded', timeout: 30000 });
  startStreaming();
}

async function readCaret() {
  if (!page) return null;
  return page.evaluate(() => {
    const el = document.activeElement;
    if (!el || el === document.body || el === document.documentElement) return null;
    const field = el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable;
    if (!field) return null;
    const r = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    const fontSize = parseFloat(style.fontSize) || 16;
    const lineHeight = parseFloat(style.lineHeight) || fontSize * 1.4;
    const padL = parseFloat(style.paddingLeft) || 0;
    const padT = parseFloat(style.paddingTop) || 0;
    let x = r.left + padL + 2;
    let y = r.top + Math.max(2, (r.height - lineHeight) / 2);
    let h = Math.max(16, Math.min(lineHeight, r.height - 4));
    if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
      const start = el.selectionStart == null ? (el.value || '').length : el.selectionStart;
      const probe = document.createElement('canvas').getContext('2d');
      probe.font = style.fontStyle + ' ' + style.fontWeight + ' ' + style.fontSize + ' ' + style.fontFamily;
      const before = (el.value || '').slice(0, start);
      x = r.left + padL + probe.measureText(before.split('\n').pop()).width - (el.scrollLeft || 0) + 1;
      if (el.tagName === 'TEXTAREA') {
        y = r.top + padT + (before.split('\n').length - 1) * lineHeight - (el.scrollTop || 0);
      }
    }
    return { x: Math.round(x), y: Math.round(y), h: Math.round(h) };
  }).catch(() => null);
}

async function paintCaret(caret, on) {
  if (!page) return;
  await page.evaluate(({ caret, on }) => {
    let el = document.getElementById('__localCaret');
    if (!caret || !on) {
      if (el) el.style.display = 'none';
      return;
    }
    if (!el) {
      el = document.createElement('div');
      el.id = '__localCaret';
      el.style.cssText = 'position:fixed;width:2px;background:#111;z-index:2147483647;pointer-events:none;box-shadow:0 0 0 1px #fff;';
      document.documentElement.appendChild(el);
    }
    el.style.display = 'block';
    el.style.left = caret.x + 'px';
    el.style.top = caret.y + 'px';
    el.style.height = caret.h + 'px';
  }, { caret, on }).catch(() => {});
}

async function startStreaming() {
  if (isStreaming) return;
  isStreaming = true;
  let tick = 0;
  while (isStreaming) {
    try {
      if (wss.clients.size > 0 && page) {
        await withPage(async () => {
          if (latestMove) {
            const m = latestMove;
            latestMove = null;
            await page.mouse.move(m.x, m.y);
          }
          tick++;
          const show = tick % 8 < 5;
          if (lastCaret) await paintCaret(lastCaret, show);
          const buffer = await page.screenshot({ type: 'jpeg', quality: 55, timeout: 4000 });
          broadcast({ type: 'frame', data: buffer.toString('base64'), w: viewport.width, h: viewport.height, caret: show ? lastCaret : null });
        });
      }
    } catch (e) {}
    await new Promise(r => setTimeout(r, 70));
  }
}

function clamp(n, max) {
  return Math.max(0, Math.min(max - 1, Math.round(n || 0)));
}

async function applyClick(x, y, button) {
  x = clamp(x, viewport.width);
  y = clamp(y, viewport.height);
  await page.mouse.move(x, y);
  await page.mouse.down({ button: button || 'left' });
  await page.mouse.up({ button: button || 'left' });
  lastCaret = await readCaret();
  if (!lastCaret) lastCaret = { x: x, y: y - 10, h: 18 };
  await paintCaret(lastCaret, true);
  broadcast({ type: 'caret', caret: lastCaret });
}

wss.on('connection', (ws) => {
  ws.send(JSON.stringify({ type: 'hello', w: viewport.width, h: viewport.height, caret: lastCaret }));
  ws.on('message', (msg) => {
    let data;
    try { data = JSON.parse(msg.toString()); } catch (e) { return; }
    if (data.type === 'viewport') {
      withPage(async () => {
        const width = Math.max(640, Math.min(1920, Math.round(data.width || 1280)));
        const height = Math.max(400, Math.min(1400, Math.round(data.height || 720)));
        if (width === viewport.width && height === viewport.height) return;
        viewport = { width, height };
        await page.setViewportSize(viewport);
      });
      return;
    }
    if (data.type === 'mouse') {
      const x = clamp(data.x, viewport.width);
      const y = clamp(data.y, viewport.height);
      if (data.action === 'move') {
        latestMove = { x, y };
        return;
      }
      if (data.action === 'click' || data.action === 'down') {
        latestMove = null;
        withPage(() => applyClick(x, y, data.button || 'left'));
        return;
      }
      if (data.action === 'wheel') {
        withPage(async () => {
          await page.mouse.move(x, y);
          await page.mouse.wheel(0, data.deltaY || 0);
        });
        return;
      }
      if (data.action === 'back') withPage(() => page.goBack({ timeout: 4000 }).catch(() => {}));
      if (data.action === 'forward') withPage(() => page.goForward({ timeout: 4000 }).catch(() => {}));
      return;
    }
    if (data.type === 'keyboard') {
      withPage(async () => {
        if (data.action === 'down') await page.keyboard.down(data.key);
        else if (data.action === 'up') await page.keyboard.up(data.key);
        lastCaret = await readCaret();
        if (lastCaret) {
          await paintCaret(lastCaret, true);
          broadcast({ type: 'caret', caret: lastCaret });
        }
      });
      return;
    }
    if (data.type === 'type') {
      withPage(async () => {
        if (data.text) await page.keyboard.insertText(data.text);
        lastCaret = await readCaret();
        if (lastCaret) {
          await paintCaret(lastCaret, true);
          broadcast({ type: 'caret', caret: lastCaret });
        }
      });
    }
  });
});

app.get('/api/url', async (req, res) => {
  try { res.json({ url: page ? page.url() : '' }); }
  catch (e) { res.status(500).json({ error: e.message }); }
});
app.post('/api/back', (req, res) => { withPage(() => page.goBack({ timeout: 4000 }).catch(() => {})); res.json({ ok: true }); });
app.post('/api/forward', (req, res) => { withPage(() => page.goForward({ timeout: 4000 }).catch(() => {})); res.json({ ok: true }); });
app.post('/api/scroll', (req, res) => {
  const { direction, amount = 300 } = req.body || {};
  withPage(() => page.mouse.wheel(0, direction === 'up' ? -amount : amount));
  res.json({ ok: true });
});
app.post('/api/type', (req, res) => {
  const { text } = req.body || {};
  if (text) withPage(async () => { await page.keyboard.insertText(text); lastCaret = await readCaret(); broadcast({ type: 'caret', caret: lastCaret }); });
  res.json({ ok: true });
});
app.post('/api/viewport', (req, res) => {
  const { width, height } = req.body || {};
  withPage(async () => {
    viewport = { width: Math.max(640, Math.min(1920, Math.round(width || 1280))), height: Math.max(400, Math.min(1400, Math.round(height || 720))) };
    await page.setViewportSize(viewport);
  });
  res.json({ ok: true });
});
app.post('/api/download', (req, res) => {
  const { url } = req.body || {};
  if (!url) return res.status(400).json({ error: 'URLがありません' });
  const targetDir = 'C:\\RocalHTMLSite\\サイト内ページ操作\\サイト内のページ画像ダウンロード';
  const cmdPath = process.env.ComSpec || 'C:\\Windows\\System32\\cmd.exe';
  exec(`"${cmdPath}" /c npm start -- --download="${url}"`, { cwd: targetDir, windowsHide: false, env: process.env });
  res.json({ ok: true, url });
});

const PORT = 3000;
server.listen(PORT, '0.0.0.0', async () => {
  console.log('サーバー起動: http://localhost:' + PORT);
  try { await startBrowser(); console.log('準備完了'); }
  catch (err) { console.error('ブラウザ起動エラー:', err); }
});
