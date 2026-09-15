const express = require('express');
const http = require('http');
const { WebSocketServer } = require('ws');
const { chromium } = require('playwright');
const path = require('path');
const { exec } = require('child_process');

const app = express();
const server = http.createServer(app);
const wss = new WebSocketServer({ server });
const { spawn } = require('child_process');

// JSONを受け取れるようにする
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// CORSを許可（file:// や他のオリジンからでもアクセス可能にする）
app.use((req, res, next) => {
  res.header('Access-Control-Allow-Origin', '*');
  res.header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.header('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') {
    return res.sendStatus(200);
  }
  next();
});

let browser, page;
let isStreaming = false;

async function startBrowser() {
  try {
    console.log('ブラウザを起動中...');
    browser = await chromium.launch({
      headless: true,
      args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--window-size=1280,720'
      ]
    });

    const context = await browser.newContext({
      viewport: { width: 1280, height: 720 }
    });

    page = await context.newPage();
    console.log('ページを作成しました');

    await page.goto('https://duckduckgo.com', { waitUntil: 'domcontentloaded', timeout: 30000 });
    console.log('初期ページを開きました:', page.url());

    console.log('準備完了');
    startStreaming();
  } catch (err) {
    console.error('ブラウザ起動エラー:', err);
  }
}

async function startStreaming() {
  if (isStreaming) return;
  isStreaming = true;
  console.log('スクリーンショットストリーミング開始');

  while (isStreaming) {
    try {
      if (wss.clients.size > 0) {
        const buffer = await page.screenshot({
          type: 'jpeg',
          quality: 45,
          timeout: 5000
        });
        const base64 = buffer.toString('base64');
        const message = JSON.stringify({ type: 'frame', data: base64 });

        wss.clients.forEach(client => {
          if (client.readyState === 1) {
            client.send(message);
          }
        });
      }
    } catch (e) {
      // 無視
    }
    await new Promise(r => setTimeout(r, 120));
  }
}

wss.on('connection', (ws) => {
  console.log('クライアントが接続しました (現在の接続数:', wss.clients.size, ')');

  ws.on('message', async (msg) => {
    try {
      const data = JSON.parse(msg.toString());

      if (data.type === 'mouse') {
        if (data.action === 'move') {
          await page.mouse.move(data.x, data.y);
        } else if (data.action === 'down') {
          await page.mouse.down({ button: data.button || 'left' });
        } else if (data.action === 'up') {
          await page.mouse.up({ button: data.button || 'left' });
        } else if (data.action === 'wheel') {
          await page.mouse.wheel(0, data.deltaY || 0);
        } else if (data.action === 'back') {
          await page.goBack({ timeout: 3000 }).catch(() => {});
        } else if (data.action === 'forward') {
          await page.goForward({ timeout: 3000 }).catch(() => {});
        }
      }

      if (data.type === 'keyboard') {
        if (data.action === 'down') {
          await page.keyboard.down(data.key);
        } else if (data.action === 'up') {
          await page.keyboard.up(data.key);
        }
      }

      if (data.type === 'type') {
        await page.keyboard.insertText(data.text);
      }
    } catch (e) {
      console.error('入力エラー:', e.message);
    }
  });

  ws.on('close', () => {
    console.log('クライアントが切断しました');
  });
});

// 現在のURLを取得
app.get('/api/url', async (req, res) => {
  try {
    const url = page ? page.url() : '';
    res.json({ url });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// 戻る
app.post('/api/back', async (req, res) => {
  try {
    await page.goBack({ timeout: 3000 }).catch(() => {});
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// 進む
app.post('/api/forward', async (req, res) => {
  try {
    await page.goForward({ timeout: 3000 }).catch(() => {});
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// スクロール
app.post('/api/scroll', async (req, res) => {
  try {
    const { direction, amount = 300 } = req.body || {};
    if (direction === 'up') {
      await page.mouse.wheel(0, -amount);
    } else {
      await page.mouse.wheel(0, amount);
    }
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// テキスト入力（日本語対応）
app.post('/api/type', async (req, res) => {
  try {
    const { text } = req.body || {};
    if (text) {
      await page.keyboard.insertText(text);
    }
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// エロ漫画ダウンロード指示
app.post('/api/download', async (req, res) => {
  try {
    const { url, downloadDir } = req.body || {};
    if (!url) {
      return res.status(400).json({ error: 'URLがありません' });
    }

    console.log('===== 環境確認 =====');
    console.log('ComSpec:', process.env.ComSpec);
    console.log('SystemRoot:', process.env.SystemRoot);
    console.log('PATH の一部:', (process.env.PATH || '').substring(0, 200));
    console.log('cwd:', process.cwd());
    console.log('====================');

    const targetDir = 'C:\\RocalHTMLSite\\サイト内ページ操作\\サイト内のページ画像ダウンロード';
    console.log('ダウンロード指示を受信:', url);
    console.log('実行フォルダ:', targetDir);

    const { exec } = require('child_process');

    // フルパスで cmd.exe を指定して試す
    const cmdPath = process.env.ComSpec || 'C:\\Windows\\System32\\cmd.exe';
    const fullCmd = `"${cmdPath}" /c npm start -- --download="${url}"`;

    console.log('実行コマンド:', fullCmd);

    exec(fullCmd, {
      cwd: targetDir,
      windowsHide: false,
      env: process.env
    }, (error, stdout, stderr) => {
      if (error) {
        console.error('実行エラー:', error.message);
        return;
      }
      if (stderr) console.error('stderr:', stderr);
      console.log('stdout:', stdout);
    });

    res.json({ ok: true, message: 'ダウンロード指示を送りました', url });
  } catch (e) {
    console.error(e);
    res.status(500).json({ error: e.message });
  }
});

const PORT = 3000;
server.listen(PORT, async () => {
  console.log(`サーバー起動: http://localhost:${PORT}`);
  await startBrowser();
});