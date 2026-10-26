@echo off
chcp 65001 >nul
setlocal EnableExtensions

:: このbatがあるフォルダへ移動（UNCでも pushd で一時ドライブ割り当て）
pushd "%~dp0"
if errorlevel 1 (
    echo.
    echo [エラー] フォルダにアクセスできません。
    echo ネットワークドライブが切断されていないか確認してください。
    echo.
    pause
    exit /b 1
)

echo ========================================
echo   Github 更新スクリプトを起動します
echo ========================================
echo.

where pythonw >nul 2>&1
if %errorlevel%==0 (
    pythonw "Github更新.pyw"
    set "ERR=%errorlevel%"
) else (
    where python >nul 2>&1
    if %errorlevel%==0 (
        python "Github更新.pyw"
        set "ERR=%errorlevel%"
    ) else (
        echo [エラー] Python が見つかりません。
        echo Python をインストールし、PATH に追加してください。
        echo.
        popd
        pause
        exit /b 1
    )
)

popd
endlocal
exit /b %ERR%
