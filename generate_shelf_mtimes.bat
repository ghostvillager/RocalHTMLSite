@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo === Shelf エロ漫画 更新日時インデックス生成 ===
echo.

REM 第1引数があればそれをルートに使う。無ければよくある場所を自動検出
if not "%~1"=="" (
  python "%~dp0generate_shelf_mtimes.py" "%~1"
) else (
  python "%~dp0generate_shelf_mtimes.py"
)

if errorlevel 1 (
  echo.
  echo Python が見つからない場合は py を試します...
  if not "%~1"=="" (
    py -3 "%~dp0generate_shelf_mtimes.py" "%~1"
  ) else (
    py -3 "%~dp0generate_shelf_mtimes.py"
  )
)

echo.
pause
