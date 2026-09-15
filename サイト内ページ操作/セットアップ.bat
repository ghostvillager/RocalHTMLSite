@echo off
chcp 932 >nul
echo ========================================
echo  埋め込み型検索エンジン セットアップ
echo ========================================
echo.

:: 現在のフォルダに移動
cd /d "%~dp0"

:: Node.js が入っているか確認
where node >nul 2>&1
if %errorlevel% neq 0 (
    echo [エラー] Node.js がインストールされていません。
    echo.
    echo 以下のサイトから Node.js をインストールしてください:
    echo https://nodejs.org/
    echo.
    echo インストール後、この bat を再実行してください。
    pause
    exit /b 1
)

echo [OK] Node.js が見つかりました
node -v
echo.

:: package.json があるか確認
if not exist "package.json" (
    echo [エラー] package.json が見つかりません。
    echo 正しいフォルダで実行してください。
    pause
    exit /b 1
)

echo [1/3] npm パッケージをインストール中...
call npm install
if %errorlevel% neq 0 (
    echo [エラー] npm install に失敗しました。
    pause
    exit /b 1
)
echo [OK] npm install 完了
echo.

echo [2/3] Playwright のブラウザをインストール中...
echo （時間がかかることがあります）
call npx playwright install chromium
if %errorlevel% neq 0 (
    echo [エラー] playwright install に失敗しました。
    pause
    exit /b 1
)
echo [OK] Playwright ブラウザのインストール完了
echo.

echo [3/3] 動作確認...
echo.
echo ========================================
echo  セットアップが完了しました！
echo ========================================
echo.
echo これからサーバーを起動します。
echo 終了するときはこのウィンドウで Ctrl + C を押してください。
echo.
pause

:: サーバー起動
call npm start