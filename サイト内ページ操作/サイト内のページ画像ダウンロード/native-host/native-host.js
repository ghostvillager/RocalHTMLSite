const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

const logPath = path.join(__dirname, 'native-log.txt');

function log(msg) {
  const line = new Date().toISOString() + ' ' + msg + '\n';
  try {
    fs.appendFileSync(logPath, line);
  } catch (e) {}
  // コンソールにも出しておく（デバッグ用）
  console.error(line);
}

function sendMessage(msg) {
  try {
    const json = JSON.stringify(msg);
    const buffer = Buffer.from(json, 'utf8');
    const header = Buffer.alloc(4);
    header.writeUInt32LE(buffer.length, 0);
    process.stdout.write(header);
    process.stdout.write(buffer);
    log('sent: ' + json);
  } catch (e) {
    log('sendMessage error: ' + e.message);
  }
}

log('===== native-host.js started =====');
log('argv: ' + JSON.stringify(process.argv));
log('cwd: ' + process.cwd());

let inputBuffer = Buffer.alloc(0);

process.stdin.on('data', (chunk) => {
  log('received chunk size: ' + chunk.length);
  inputBuffer = Buffer.concat([inputBuffer, chunk]);

  while (inputBuffer.length >= 4) {
    const msgLength = inputBuffer.readUInt32LE(0);
    log('message length: ' + msgLength);

    if (inputBuffer.length < 4 + msgLength) {
      log('waiting for more data...');
      break;
    }

    const msgBody = inputBuffer.slice(4, 4 + msgLength).toString('utf8');
    inputBuffer = inputBuffer.slice(4 + msgLength);

    log('message body: ' + msgBody);

    try {
      const msg = JSON.parse(msgBody);
      const url = msg.url;

      if (!url) {
        sendMessage({ status: 'error', message: 'urlがありません' });
        continue;
      }

      log('url: ' + url);

      const appDir = path.resolve(__dirname, '..');
      const electronPath = path.join(appDir, 'node_modules', 'electron', 'dist', 'electron.exe');

      log('appDir: ' + appDir);
      log('electronPath: ' + electronPath);
      log('electron exists: ' + fs.existsSync(electronPath));

      if (!fs.existsSync(electronPath)) {
        sendMessage({ status: 'error', message: 'electron.exeが見つかりません: ' + electronPath });
        continue;
      }

      const child = spawn('npx', ['electron', '.', `--download=${url}`], {
        detached: true,
        stdio: 'ignore',
        cwd: appDir,
        shell: true,          // 重要
        windowsHide: false    // いったん false に
      });

      child.unref();
      log('spawned electron, pid: ' + child.pid);

      sendMessage({ status: 'started', url: url });
    } catch (e) {
      log('error: ' + e.message);
      sendMessage({ status: 'error', message: e.message });
    }
  }
});

process.stdin.on('end', () => {
  log('stdin end');
});

process.stdin.on('error', (err) => {
  log('stdin error: ' + err.message);
});

// 念のためプロセスがすぐ終了しないようにする
process.stdin.resume();