@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
title 同人画像ダウンローダー 初期セットアップ

echo ========================================
echo  同人画像ダウンローダー 初期セットアップ
echo  まっさらなPC向け
echo ========================================
echo.

REM ===== このBATがあるフォルダへ移動 =====
cd /d "%~dp0"
if errorlevel 1 (
    echo [エラー] フォルダに移動できませんでした。
    pause
    exit /b 1
)
echo 作業フォルダ: %cd%
echo.

REM ===== 1. Node.js の確認・インストール案内 =====
echo [1/5] Node.js を確認しています...
where node >nul 2>&1
if errorlevel 1 (
    echo Node.js が見つかりません。
    echo.
    echo winget でインストールを試みます...
    where winget >nul 2>&1
    if errorlevel 1 (
        echo winget も見つかりませんでした。
        echo.
        echo 手動で Node.js をインストールしてください。
        echo https://nodejs.org/ から LTS 版をダウンロードしてインストール後、
        echo このBATを再実行してください。
        echo.
        start https://nodejs.org/
        pause
        exit /b 1
    )
    echo Node.js LTS をインストール中...（少し時間がかかります）
    winget install OpenJS.NodeJS.LTS --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo [エラー] Node.js のインストールに失敗しました。
        echo https://nodejs.org/ から手動インストールしてください。
        start https://nodejs.org/
        pause
        exit /b 1
    )
    echo Node.js のインストールが完了しました。
    echo 新しいコマンドプロンプトでPATHが有効になるため、
    echo 一度このウィンドウを閉じて、もう一度 setup.bat を実行してください。
    pause
    exit /b 0
) else (
    for /f "tokens=*" %%v in ('node -v') do echo Node.js 検出: %%v
)
echo.

REM ===== 2. npm の確認 =====
echo [2/5] npm を確認しています...
where npm >nul 2>&1
if errorlevel 1 (
    echo [エラー] npm が見つかりません。Node.js を再インストールしてください。
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('npm -v') do echo npm 検出: %%v
echo.

REM ===== 3. 依存パッケージのインストール =====
echo [3/5] npm パッケージをインストールしています...
echo （electron / sharp など。数分かかることがあります）
echo.
if not exist "package.json" (
    echo [エラー] package.json が見つかりません。
    echo このBATをプロジェクトのルートフォルダに置いて実行してください。
    pause
    exit /b 1
)
call npm install
if errorlevel 1 (
    echo [エラー] npm install に失敗しました。
    echo ネット接続と package.json を確認してください。
    pause
    exit /b 1
)
echo npm install 完了。
echo.

REM ===== 4. Native Messaging 用フォルダ確認 =====
echo [4/5] Native Messaging 関連ファイルを確認しています...
if not exist "native-host\native-host.bat" (
    echo [警告] native-host\native-host.bat が見つかりません。
    echo native-host フォルダが揃っているか確認してください。
) else (
    echo native-host を確認しました。
)
if not exist "chrome-extension\manifest.json" (
    echo [警告] chrome-extension\manifest.json が見つかりません。
) else (
    echo chrome-extension を確認しました。
)
echo.

REM ===== 5. レジストリ登録（Native Messaging Host） =====
echo [5/5] Native Messaging Host をレジストリに登録します...
set "JSON_PATH=%cd%\native-host\com.doujin.downloader.json"
set "JSON_PATH=!JSON_PATH:\=\\!"

if not exist "native-host\com.doujin.downloader.json" (
    echo [警告] com.doujin.downloader.json がありません。
    echo 後で手動でレジストリ登録してください。
) else (
    echo 登録するパス: %cd%\native-host\com.doujin.downloader.json
    reg add "HKCU\Software\Google\Chrome\NativeMessagingHosts\com.doujin.downloader" /ve /t REG_SZ /d "%cd%\native-host\com.doujin.downloader.json" /f >nul
    if errorlevel 1 (
        echo [警告] レジストリ登録に失敗しました。管理者権限で再実行するか、手動で .reg を使ってください。
    ) else (
        echo レジストリ登録完了。
    )
)
echo.

REM ===== 完了メッセージ =====
echo ========================================
echo  セットアップ処理が完了しました
echo ========================================
echo.
echo 【このあとやること】
echo.
echo 1. Chrome をインストール（まだの場合）
echo    https://www.google.com/chrome/
echo.
echo 2. Chrome で拡張機能を読み込む
echo    - アドレスバーに chrome://extensions と入力
echo    - 「デベロッパーモード」をオン
echo    - 「パッケージ化されていない拡張機能を読み込む」
echo    - このフォルダの chrome-extension を選択
echo.
echo 3. 拡張機能のIDをコピーする
echo    （chrome://extensions のカードに表示される長い英数字）
echo.
echo 4. native-host\com.doujin.downloader.json を開く
echo    allowed_origins を次の形に書き換える:
echo    "chrome-extension://ここにコピーしたID/"
echo.
echo 5. 一度だけ保存先フォルダを設定する
echo    コマンドプロンプトで:
echo      npm start
echo    開いたウィンドウで「フォルダを選択」を押す
echo.
echo 6. あとは通常のWebページを開き、
echo    拡張機能アイコン →「このページをダウンロード」
echo.
echo ========================================
pause
endlocal