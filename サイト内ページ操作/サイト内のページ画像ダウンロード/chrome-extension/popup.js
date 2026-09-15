document.getElementById('btn').addEventListener('click', async () => {
  const btn = document.getElementById('btn');
  const status = document.getElementById('status');

  btn.disabled = true;
  status.textContent = '起動中...';

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const url = tab.url;

    if (!url || url.startsWith('chrome://') || url.startsWith('edge://')) {
      status.textContent = 'このページはダウンロードできません';
      btn.disabled = false;
      return;
    }

    const port = chrome.runtime.connectNative('com.doujin.downloader');

    port.onMessage.addListener((msg) => {
      if (msg.status === 'started') {
        status.textContent = 'Electronを起動しました\n' + url;
      } else {
        status.textContent = 'エラー: ' + (msg.message || JSON.stringify(msg));
      }
      btn.disabled = false;
    });

    port.onDisconnect.addListener(() => {
      if (chrome.runtime.lastError) {
        status.textContent = '接続失敗: ' + chrome.runtime.lastError.message;
      }
      btn.disabled = false;
    });

    port.postMessage({ url });
  } catch (e) {
    status.textContent = 'エラー: ' + e.message;
    btn.disabled = false;
  }
});