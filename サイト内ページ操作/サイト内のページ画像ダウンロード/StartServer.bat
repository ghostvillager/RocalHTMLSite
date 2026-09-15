@echo off
setlocal EnableDelayedExpansion

REM ===== 設定 =====
set PORT=8000
set API_PORT=3456
set BIND_IP=0.0.0.0
set INFO_TXT=server_url.txt
set OPEN_URL=OPEN_index.url

pushd "%~dp0"
if errorlevel 1 (
    echo Failed to access directory.
    pause
    exit /b 1
)

REM ===== IPv4アドレス取得 =====
set LOCAL_IP=
for /f "tokens=2 delims=:" %%A in ('ipconfig ^| findstr /R /C:"IPv4 Address" /C:"IPv4 アドレス"') do (
    set LOCAL_IP=%%A
    goto :FOUND
)
:FOUND
set LOCAL_IP=%LOCAL_IP: =%

set IP_URL=http://%LOCAL_IP%:%PORT%/index.html
set LOCAL_URL=http://localhost:%PORT%/index.html

REM ===== 情報ファイル作成 =====
if exist "%INFO_TXT%" del "%INFO_TXT%"
(
echo ローカル動画ギャラリー 接続先
echo.
echo ■ 同一PC用
echo %LOCAL_URL%
echo.
echo ■ LAN内アクセス用
echo %IP_URL%
echo.
echo ■ ダウンロードAPI用ポート
echo http://localhost:%API_PORT%
) > "%INFO_TXT%"

if exist "%OPEN_URL%" del "%OPEN_URL%"
(
echo [InternetShortcut]
echo URL=%IP_URL%
) > "%OPEN_URL%"

REM ===== RangeHTTPServer チェック =====
pip show rangehttpserver >nul 2>&1
if errorlevel 1 (
    echo rangehttpserver をインストールします...
    pip install rangehttpserver
)

echo ======================================
echo サーバー起動中...
echo ======================================

REM 1. Pythonサーバー起動（ポート8000）
start "RangeHTTPServer" python -m RangeHTTPServer %PORT% --bind %BIND_IP%

timeout /t 2 >nul

REM 2. Cloudflare Tunnel（公開HTML用・ポート8000）
start "CF-Tunnel-8000" cloudflared tunnel --url http://localhost:%PORT%

REM 3. Cloudflare Tunnel（API用・ポート3456）
start "CF-Tunnel-3456" cloudflared tunnel --url http://localhost:%API_PORT%

REM 4. Electronアプリ起動
echo Electronアプリを起動します...
start "DoujinDownloader" cmd /c "npm start"

echo.
echo ======================================
echo 起動完了
echo.
echo ・公開HTML用トンネル（8000）と
echo ・API用トンネル（3456）が別々に起動しています
echo.
echo API用の https://xxxx.trycloudflare.com を
echo 公開HTMLの「APIの公開URL」欄に貼ってください
echo ======================================
echo.
pause

popd
endlocal