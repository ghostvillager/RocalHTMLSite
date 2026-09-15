@echo off
setlocal EnableDelayedExpansion
REM ===== 設定 =====
set PORT=8000
set BIND_IP=0.0.0.0
set INFO_TXT=server_url.txt
set OPEN_URL=OPEN_index.url
REM ===== このBATがあるフォルダへ移動 =====
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
if "%LOCAL_IP%"=="" (
    echo IPv4アドレスを取得できませんでした。
    pause
    exit /b 1
)
set IP_URL=http://%LOCAL_IP%:%PORT%/index.html
set LOCAL_URL=http://localhost:%PORT%/index.html
REM ===== txt作り直し =====
if exist "%INFO_TXT%" del "%INFO_TXT%"
(
echo ローカル動画ギャラリー 接続先
echo.
echo ■ 同一PC用
echo %LOCAL_URL%
echo.
echo ■ LAN内アクセス用
echo %IP_URL%
) > "%INFO_TXT%"
REM ===== .url 作り直し =====
if exist "%OPEN_URL%" del "%OPEN_URL%"
(
echo [InternetShortcut]
echo URL=%IP_URL%
) > "%OPEN_URL%"
REM ===== RangeHTTPServerのインストールチェックとインストール =====
echo Checking if rangehttpserver is installed...
pip show rangehttpserver >nul 2>&1
if errorlevel 1 (
    echo rangehttpserver is not installed. Installing now...
    pip install rangehttpserver
    if errorlevel 1 (
        echo Failed to install rangehttpserver. Please install it manually and try again.
        pause
        exit /b 1
    )
    echo rangehttpserver installed successfully.
)
REM ===== 表示 =====
echo ======================================
echo Python RangeHTTPServer 起動
echo Folder : %cd%
echo Bind : %BIND_IP%
echo Port : %PORT%
echo.
echo Access URLs:
echo %LOCAL_URL%
echo %IP_URL%
echo.
echo 作成ファイル:
echo %INFO_TXT%
echo %OPEN_URL%
echo ======================================
echo.
REM ===== サーバー起動（別プロセス）=====
start "" python -m RangeHTTPServer %PORT% --bind %BIND_IP%

REM 少し待つ（サーバー起動待ち）
timeout /t 2 >nul

REM ===== Cloudflare Tunnel 起動 =====
start "" cloudflared tunnel --url http://localhost:%PORT%
REM ===== Node.js サーバー起動 =====
start "" cmd /c "cd /d C:\RocalHTMLSite\サイト内ページ操作 && npm start"
REM ===== 後始末 =====
popd
endlocal