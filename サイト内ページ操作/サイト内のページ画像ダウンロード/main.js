const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const path = require('path');
const fs = require('fs');
const https = require('https');
const http = require('http');
const { URL } = require('url');
const sharp = require('sharp');

let progressWindow = null;
const CONFIG_PATH = path.join(__dirname, 'config.json');

// 起動引数からURLを取得
const downloadArg = process.argv.find(arg => arg.startsWith('--download='));
const targetUrl = downloadArg ? downloadArg.replace('--download=', '') : null;

function loadConfig() {
  try {
    if (fs.existsSync(CONFIG_PATH)) {
      return JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
    }
  } catch (e) {}
  return {};
}

function saveConfig(config) {
  try {
    fs.writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2), 'utf8');
  } catch (e) {}
}

function sanitizeFolderName(title) {
  if (!title) return 'untitled';
  return title
    .replace(/[\\/:*?"<>|]/g, '_')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 120);
}

function fetchImageBuffer(url) {
  return new Promise((resolve, reject) => {
    try {
      const parsed = new URL(url);
      const client = parsed.protocol === 'https:' ? https : http;
      const request = client.get(url, {
        headers: {
          'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
          'Referer': parsed.origin
        }
      }, (res) => {
        if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
          fetchImageBuffer(res.headers.location).then(resolve).catch(reject);
          return;
        }
        if (res.statusCode !== 200) {
          reject(new Error(`HTTP ${res.statusCode}`));
          return;
        }
        const chunks = [];
        res.on('data', chunk => chunks.push(chunk));
        res.on('end', () => resolve(Buffer.concat(chunks)));
        res.on('error', reject);
      });
      request.on('error', reject);
      request.setTimeout(30000, () => {
        request.destroy();
        reject(new Error('タイムアウト'));
      });
    } catch (err) {
      reject(err);
    }
  });
}

async function downloadAndConvertToPng(url, savePath) {
  const buffer = await fetchImageBuffer(url);
  await sharp(buffer)
    .png({ quality: 100, compressionLevel: 6 })
    .toFile(savePath);
}

function createProgressWindow() {
  progressWindow = new BrowserWindow({
    width: 420,
    height: 180,
    resizable: false,
    maximizable: false,
    title: 'ダウンロード中',
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false
    }
  });

  progressWindow.setMenuBarVisibility(false);
  progressWindow.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(`
    <!DOCTYPE html>
    <html>
    <head>
      <style>
        body { font-family: "Segoe UI", Meiryo, sans-serif; background:#1e1e1e; color:#eee; margin:0; padding:20px; }
        h3 { margin:0 0 12px; font-size:15px; }
        #status { font-size:13px; color:#aaa; margin-bottom:12px; word-break:break-all; }
        #bar-bg { background:#333; border-radius:4px; height:10px; overflow:hidden; }
        #bar { background:#0a84ff; height:100%; width:0%; transition:width 0.2s; }
        #count { margin-top:10px; font-size:13px; }
      </style>
    </head>
    <body>
      <h3>画像をダウンロードしています...</h3>
      <div id="status">準備中</div>
      <div id="bar-bg"><div id="bar"></div></div>
      <div id="count"></div>
      <script>
        window.updateProgress = (current, total, text) => {
          document.getElementById('status').textContent = text || '';
          const pct = total ? Math.round((current / total) * 100) : 0;
          document.getElementById('bar').style.width = pct + '%';
          document.getElementById('count').textContent = total ? (current + ' / ' + total) : '';
        };
      </script>
    </body>
    </html>
  `));
}

function updateProgress(current, total, text) {
  if (progressWindow && !progressWindow.isDestroyed()) {
    progressWindow.webContents.executeJavaScript(
      `window.updateProgress(${current}, ${total}, ${JSON.stringify(text || '')})`
    );
  }
}

async function performDownload(pageUrl) {
  const config = loadConfig();
  const baseDir = config.saveBaseDir;

  if (!baseDir) {
    updateProgress(0, 0, '保存先フォルダが設定されていません。先に通常起動してフォルダを選択してください。');
    setTimeout(() => app.quit(), 4000);
    return;
  }

  const tempWin = new BrowserWindow({
    show: false,
    webPreferences: { nodeIntegration: false, contextIsolation: true, webSecurity: false }
  });

  try {
    updateProgress(0, 0, 'ページを読み込み中...');
    await tempWin.loadURL(pageUrl);
    await new Promise(r => setTimeout(r, 3000));

    const result = await tempWin.webContents.executeJavaScript(`
      (function() {
        const title = document.title || 'untitled';
        const imgs = Array.from(document.querySelectorAll('img'));
        const urls = [];
        for (const img of imgs) {
          let src = img.currentSrc || img.src || img.dataset.src || img.getAttribute('data-original');
          if (!src) continue;
          try { src = new URL(src, location.href).href; } catch(e) { continue; }
          if (img.naturalWidth > 0 && img.naturalWidth < 80) continue;
          if (img.naturalHeight > 0 && img.naturalHeight < 80) continue;
          if (src.startsWith('data:') || src.startsWith('about:')) continue;
          if (!urls.includes(src)) urls.push(src);
        }
        return { title, imageUrls: urls };
      })();
    `);

    tempWin.close();

    if (!result || !result.imageUrls || result.imageUrls.length === 0) {
      updateProgress(0, 0, '画像が見つかりませんでした');
      setTimeout(() => app.quit(), 3000);
      return;
    }

    const folderName = sanitizeFolderName(result.title);
    const targetDir = path.join(baseDir, folderName);
    if (!fs.existsSync(targetDir)) fs.mkdirSync(targetDir, { recursive: true });

    const total = result.imageUrls.length;
    let success = 0;

    for (let i = 0; i < total; i++) {
      const fileName = String(i + 1).padStart(3, '0') + '.png';
      const savePath = path.join(targetDir, fileName);
      updateProgress(i + 1, total, fileName);

      try {
        await downloadAndConvertToPng(result.imageUrls[i], savePath);
        success++;
      } catch (e) {
        console.error(e);
      }
    }

    updateProgress(total, total, `完了: ${success}/${total} 枚 → ${targetDir}`);
    setTimeout(() => app.quit(), 2500);
  } catch (err) {
    tempWin.close();
    updateProgress(0, 0, 'エラー: ' + err.message);
    setTimeout(() => app.quit(), 4000);
  }
}

// 通常起動用のウィンドウ（フォルダ設定用）
function createNormalWindow() {
  const win = new BrowserWindow({
    width: 500,
    height: 300,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false
    }
  });
  win.setMenuBarVisibility(false);
  win.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(`
    <!DOCTYPE html>
    <html>
    <head>
      <style>
        body { font-family: "Segoe UI", Meiryo, sans-serif; background:#1e1e1e; color:#eee; padding:24px; }
        button { padding:10px 18px; background:#0a84ff; color:white; border:none; border-radius:6px; cursor:pointer; }
        #path { margin-top:16px; font-size:13px; color:#8f8; word-break:break-all; }
      </style>
    </head>
    <body>
      <h3>保存先フォルダ設定</h3>
      <p>拡張機能から使う前に、ここで保存先を選んでください。</p>
      <button id="btn">フォルダを選択</button>
      <div id="path"></div>
      <script>
        document.getElementById('btn').onclick = async () => {
          const folder = await window.electronAPI.selectFolder();
          if (folder) document.getElementById('path').textContent = folder;
        };
        window.electronAPI.getConfig().then(c => {
          if (c.saveBaseDir) document.getElementById('path').textContent = c.saveBaseDir;
        });
      </script>
    </body>
    </html>
  `));
}

ipcMain.handle('select-folder', async () => {
  const result = await dialog.showOpenDialog({ properties: ['openDirectory'] });
  if (result.canceled || !result.filePaths.length) return null;
  const selected = result.filePaths[0];
  const config = loadConfig();
  config.saveBaseDir = selected;
  saveConfig(config);
  return selected;
});

ipcMain.handle('get-config', async () => loadConfig());

app.whenReady().then(() => {
  if (targetUrl) {
    // 拡張機能から起動された場合
    createProgressWindow();
    performDownload(targetUrl);
  } else {
    // 普通に起動された場合（フォルダ設定用）
    createNormalWindow();
  }
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});