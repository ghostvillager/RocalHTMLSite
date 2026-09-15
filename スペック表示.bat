@echo off
chcp 932 >nul
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "REPORT=%~dp0site-speed-report.txt"
echo Site speed check > "%REPORT%"
echo Date %date% %time%>> "%REPORT%"
echo.

echo ==== PC ====
echo ==== PC ====>> "%REPORT%"
systeminfo | findstr /C:"OS" /C:"System Type" /C:"System Model" /C:"System Manufacturer" /C:"Processor" /C:"Total Physical Memory" /C:"OS Name" /C:"OS Version" /C:"OS Version" /C:"Processor" /C:"Total Physical Memory"
systeminfo | findstr /I /C:"OS" /C:"System Type" /C:"System Model" /C:"System Manufacturer" /C:"Processor" /C:"Total Physical Memory" /C:"Physical Memory" >> "%REPORT%"
echo.
wmic cpu get Name,NumberOfCores,NumberOfLogicalProcessors /format:list 2>nul
wmic cpu get Name,NumberOfCores,NumberOfLogicalProcessors /format:list 2>nul >> "%REPORT%"

echo.
echo ==== Physical disks ====
echo ==== Physical disks ====>> "%REPORT%"
wmic diskdrive get Model,InterfaceType,MediaType,Size,Status /format:table 2>nul
wmic diskdrive get Model,InterfaceType,MediaType,Size,Status /format:table 2>nul >> "%REPORT%"
echo SSD/HDD may be blank in MediaType. Check model name.

echo.
echo ==== Logical drives ====
echo ==== Logical drives ====>> "%REPORT%"
wmic logicaldisk where "DriveType=3" get DeviceID,FileSystem,FreeSpace,Size /format:table 2>nul
wmic logicaldisk where "DriveType=3" get DeviceID,FileSystem,FreeSpace,Size /format:table 2>nul >> "%REPORT%"

set "VIDEO=mp4"
set "MANGA=ƒGƒ–Ÿ‰æ"
set "ASMR=ASMR"
if exist "config.json" (
  echo.
  echo config.json found
  findstr /I "VIDEO_ROOT MANGA_ROOT ASMR_ROOT" config.json
)

echo.
echo ==== Site folders ====
echo ==== Site folders ====>> "%REPORT%"
call :FolderInfo "HTML_ROOT" "%cd%"
call :FolderInfo "VIDEO" "%cd%\%VIDEO%"
call :FolderInfo "MANGA" "%cd%\%MANGA%"
call :FolderInfo "ASMR" "%cd%\%ASMR%"

echo.
echo ==== Simple disk speed 256MB ====
echo ==== Simple disk speed 256MB ====>> "%REPORT%"
set "DONE="
call :MaybeSpeed "%cd%"
call :MaybeSpeed "%cd%\%VIDEO%"
call :MaybeSpeed "%cd%\%MANGA%"
call :MaybeSpeed "%cd%\%ASMR%"

echo.
echo ==== Dir timing close to HTML ====
echo ==== Dir timing close to HTML ====>> "%REPORT%"
call :TimeDir "HTML_ROOT" "%cd%"
if exist "%cd%\%MANGA%" call :TimeDir "MANGA_ROOT" "%cd%\%MANGA%"

echo.
echo ==== Tips ====
echo ==== Tips ====>> "%REPORT%"
echo 1. If listing takes seconds, disk is likely HDD. Move data to SSD.
echo 2. Put cover.jpg or thumb.gif in each manga folder.
echo 3. Reduce folders directly under manga root.
echo 4. Do not generate video thumbs from frames.
echo 5. If RAM is under 8GB, keep parallel loads low.
echo.
echo Report: %REPORT%
echo.
pause
exit /b 0

:FolderInfo
set "TITLE=%~1"
set "P=%~2"
echo.
echo [%TITLE%]
echo [%TITLE%]>> "%REPORT%"
echo   path: %P%
echo   path: %P%>> "%REPORT%"
if not exist "%P%" (
  echo   exists: NO
  echo   exists: NO>> "%REPORT%"
  exit /b 0
)
echo   exists: YES
echo   exists: YES>> "%REPORT%"
for %%I in ("%P%") do echo   drive: %%~dI
for %%I in ("%P%") do echo   drive: %%~dI>> "%REPORT%"
echo   listing...
set "T1=%time%"
dir /a /-c "%P%" >nul
set "T1B=%time%"
echo   top dir %T1% - %T1B%
echo   top dir %T1% - %T1B%>> "%REPORT%"
set "T2=%time%"
dir /s /a /-c "%P%" | findstr /C:"File(s)" /C:"Dir(s)" /C:"file(s)" /C:"dir(s)"
set "T2B=%time%"
echo   recurse %T2% - %T2B%
echo   recurse %T2% - %T2B%>> "%REPORT%"
dir /s /a /-c "%P%" | findstr /C:"File(s)" /C:"Dir(s)" /C:"file(s)" /C:"dir(s)" >> "%REPORT%"
exit /b 0

:TimeDir
set "TITLE=%~1"
set "P=%~2"
set "T1=%time%"
dir /a /-c "%P%" >nul
set "T2=%time%"
echo %TITLE% top listing  %T1% - %T2%
echo %TITLE% top listing  %T1% - %T2%>> "%REPORT%"
exit /b 0

:MaybeSpeed
set "P=%~1"
if not exist "%P%" exit /b 0
for %%I in ("%P%") do set "DRV=%%~dI"
echo !DONE! | find /I "!DRV!" >nul && exit /b 0
set "DONE=!DONE! !DRV!"
call :SpeedTest !DRV!
exit /b 0

:SpeedTest
set "DRV=%~1"
set "PROBE=%TEMP%\site-speed-probe.bin"
echo.
echo  %DRV%  256MB create then read
echo  %DRV%  256MB>> "%REPORT%"
if exist "%PROBE%" del /f /q "%PROBE%" >nul 2>&1
set "T1=%time%"
fsutil file createnew "%PROBE%" 268435456 >nul
set "T2=%time%"
echo   create %T1% - %T2%
echo   create %T1% - %T2%>> "%REPORT%"
set "T3=%time%"
copy /y /b "%PROBE%" nul >nul
set "T4=%time%"
echo   read   %T3% - %T4%
echo   read   %T3% - %T4%>> "%REPORT%"
del /f /q "%PROBE%" >nul 2>&1
echo   hint: about 1 sec = SSD, several sec = HDD-like
exit /b 0
