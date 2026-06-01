@echo off
setlocal EnableExtensions

cd /d "%~dp0"

set "CHANNEL=stable"
set "ZED_DIR=zed"
set "INNO_SETUP_EXE="
set "MAKEAPPX_EXE="
set "DO_BUILD=1"
set "DO_SETUP=1"
set "MODE="

:parse_args
if "%~1"=="" goto :args_done
if /i "%~1"=="--setup" (
    if defined MODE goto :usage
    set "MODE=setup"
    set "DO_BUILD=0"
    set "DO_SETUP=1"
    shift
    goto :parse_args
)
if /i "%~1"=="--build" (
    if defined MODE goto :usage
    set "MODE=build"
    set "DO_BUILD=1"
    set "DO_SETUP=0"
    shift
    goto :parse_args
)
goto :usage

:usage
echo Usage: build-zed.cmd [--build ^| --setup]
echo   no args  Build Zed and create the Inno Setup installer.
echo   --build  Build Zed only, without creating the installer.
echo   --setup  Create the installer only, reusing existing target\release outputs.
exit /b 1

:args_done

if not exist "%ZED_DIR%\Cargo.toml" (
    echo [error] Zed source directory not found: %ZED_DIR%
    exit /b 1
)

echo [1/4] Setting release channel: %CHANNEL%
pushd "%ZED_DIR%"
if errorlevel 1 exit /b %errorlevel%

set "CURRENT_CHANNEL="
if exist crates\zed\RELEASE_CHANNEL set /p CURRENT_CHANNEL=<crates\zed\RELEASE_CHANNEL
if /i not "%CURRENT_CHANNEL%"=="%CHANNEL%" (
    <nul set /p "=%CHANNEL%" > crates\zed\RELEASE_CHANNEL
)
set "RELEASE_CHANNEL=%CHANNEL%"
set "ZED_RELEASE_CHANNEL=%CHANNEL%"

if "%DO_BUILD%"=="1" (
    echo [2/4] Building Zed...
    cargo build --release --locked --package zed --package cli --package auto_update_helper
    if errorlevel 1 goto :fail_in_zed

    cargo build --release --locked --features stable --no-default-features --package explorer_command_injector
    if errorlevel 1 goto :fail_in_zed
) else (
    echo [2/4] Skipping cargo build...
)

if "%DO_SETUP%"=="0" (
    popd
    echo Done. Build only.
    exit /b 0
)

echo [3/4] Preparing Inno Setup resources...
set "CARGO_OUT_DIR=%CD%\target\release"
set "INNO_DIR=%CD%\inno\lite"
set "TARGET_DIR=%CD%\target"
set "APP_EXE_NAME=Zed"
set "APP_NAME=Zed"
set "APP_DISPLAY_NAME=Zed"
set "APP_SETUP_NAME=Zed-x64"
set "APP_ICON_NAME=app-icon"
set "APP_MUTEX=Zed-Stable-Instance-Mutex"
set "REG_VALUE_NAME=Zed"
set "APP_USER_ID=ZedIndustries.Zed"
set "SHELL_NAME_SHORT=Z^&ed"
set "APP_APPX_FULL_NAME=ZedIndustries.Zed_1.0.0.0_neutral__japxn1gcva8rg"
set "APP_ID={{2DB0DA96-CA55-49BB-AF4F-64AF36A86712}"

findstr /c:"ZedG.exe" crates\zed\resources\windows\zed.iss >nul 2>nul
if not errorlevel 1 (
    set "APP_EXE_NAME=ZedG"
    set "APP_NAME=ZedG"
    set "APP_DISPLAY_NAME=ZedG"
    set "APP_SETUP_NAME=ZedG-x64"
    set "REG_VALUE_NAME=ZedG"
    set "APP_USER_ID=ZedIndustries.ZedG"
    set "SHELL_NAME_SHORT=Z^&edG"
    set "APP_APPX_FULL_NAME=ZedIndustries.ZedG_1.0.0.0_neutral__japxn1gcva8rg"
)

for /f "tokens=3 delims= " %%V in ('findstr /b /c:"version = " crates\zed\Cargo.toml') do set "APP_VERSION=%%~V"
if not defined APP_VERSION set "APP_VERSION=1.0.0"

echo     App version: %APP_VERSION%
echo     Cleaning Inno workspace...
if exist "%INNO_DIR%" rmdir /s /q "%INNO_DIR%"
if not exist "%TARGET_DIR%" mkdir "%TARGET_DIR%" || goto :fail_in_zed
mkdir "%INNO_DIR%" "%INNO_DIR%\bin" "%INNO_DIR%\tools" "%INNO_DIR%\appx" "%INNO_DIR%\make_appx" "%INNO_DIR%\messages" || goto :fail_in_zed

echo     Copying Windows installer resources...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Copy-Item -Path 'crates/zed/resources/windows/*' -Destination '%INNO_DIR%' -Recurse -Force"
if errorlevel 1 goto :fail_in_zed

echo     Copying Zed.exe...
if not exist "%CARGO_OUT_DIR%\zed.exe" (
    echo [error] Missing build output: %CARGO_OUT_DIR%\zed.exe
    goto :fail_in_zed
)
copy /y "%CARGO_OUT_DIR%\zed.exe" "%INNO_DIR%\%APP_EXE_NAME%.exe" >nul
if errorlevel 1 goto :fail_in_zed

echo     Copying CLI...
if not exist "%CARGO_OUT_DIR%\cli.exe" (
    echo [error] Missing build output: %CARGO_OUT_DIR%\cli.exe
    goto :fail_in_zed
)
copy /y "%CARGO_OUT_DIR%\cli.exe" "%INNO_DIR%\bin\zed.exe" >nul
if errorlevel 1 goto :fail_in_zed

echo     Copying auto update helper...
if not exist "%CARGO_OUT_DIR%\auto_update_helper.exe" (
    echo [error] Missing build output: %CARGO_OUT_DIR%\auto_update_helper.exe
    goto :fail_in_zed
)
copy /y "%CARGO_OUT_DIR%\auto_update_helper.exe" "%INNO_DIR%\tools\auto_update_helper.exe" >nul
if errorlevel 1 goto :fail_in_zed

echo     Copying conpty.dll...
if not exist "%CARGO_OUT_DIR%\conpty.dll" (
    echo [error] Missing build output: %CARGO_OUT_DIR%\conpty.dll
    goto :fail_in_zed
)
copy /y "%CARGO_OUT_DIR%\conpty.dll" "%INNO_DIR%\conpty.dll" >nul
if errorlevel 1 goto :fail_in_zed

if exist "%CARGO_OUT_DIR%\OpenConsole.exe" (
    echo     Copying OpenConsole.exe...
    mkdir "%INNO_DIR%\x64" >nul 2>nul
    copy /y "%CARGO_OUT_DIR%\OpenConsole.exe" "%INNO_DIR%\x64\OpenConsole.exe" >nul
    if errorlevel 1 goto :fail_in_zed
)

echo     Copying explorer command injector...
if not exist "%CARGO_OUT_DIR%\explorer_command_injector.dll" (
    echo [error] Missing build output: %CARGO_OUT_DIR%\explorer_command_injector.dll
    goto :fail_in_zed
)
copy /y "%CARGO_OUT_DIR%\explorer_command_injector.dll" "%INNO_DIR%\zed_explorer_command_injector.dll" >nul
if errorlevel 1 goto :fail_in_zed

echo     Preparing AppX manifest...
copy /y crates\explorer_command_injector\AppxManifest.xml "%INNO_DIR%\make_appx\AppxManifest.xml" >nul
if errorlevel 1 goto :fail_in_zed

echo     Locating makeAppx.exe...
if exist "C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x64\makeAppx.exe" set "MAKEAPPX_EXE=C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x64\makeAppx.exe"
if not defined MAKEAPPX_EXE for /f "delims=" %%M in ('where makeAppx.exe 2^>nul') do if not defined MAKEAPPX_EXE set "MAKEAPPX_EXE=%%M"
if not defined MAKEAPPX_EXE (
    echo [error] makeAppx.exe not found. Install Windows SDK or add makeAppx.exe to PATH.
    goto :fail_in_zed
)

echo     Building AppX package...
"%MAKEAPPX_EXE%" pack /d "%INNO_DIR%\make_appx" /p "%INNO_DIR%\zed_explorer_command_injector.appx" /nv
if errorlevel 1 goto :fail_in_zed

echo     Moving AppX files...
move /y "%INNO_DIR%\zed_explorer_command_injector.appx" "%INNO_DIR%\appx\zed_explorer_command_injector.appx" >nul
if errorlevel 1 goto :fail_in_zed
move /y "%INNO_DIR%\zed_explorer_command_injector.dll" "%INNO_DIR%\appx\zed_explorer_command_injector.dll" >nul
if errorlevel 1 goto :fail_in_zed

echo     Locating ISCC.exe...
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set "INNO_SETUP_EXE=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not defined INNO_SETUP_EXE for /f "delims=" %%I in ('where ISCC.exe 2^>nul') do if not defined INNO_SETUP_EXE set "INNO_SETUP_EXE=%%I"
if not defined INNO_SETUP_EXE (
    echo [error] Inno Setup ISCC.exe not found. Install Inno Setup 6 or add ISCC.exe to PATH.
    goto :fail_in_zed
)

echo [4/4] Building Inno Setup installer...
set "CI="
"%INNO_SETUP_EXE%" "%INNO_DIR%\zed.iss" "/dAppId=%APP_ID%" "/dAppIconName=%APP_ICON_NAME%" "/dOutputDir=%TARGET_DIR%" "/dAppSetupName=%APP_SETUP_NAME%" "/dAppName=%APP_NAME%" "/dAppDisplayName=%APP_DISPLAY_NAME%" "/dRegValueName=%REG_VALUE_NAME%" "/dAppMutex=%APP_MUTEX%" "/dAppExeName=%APP_EXE_NAME%" "/dResourcesDir=%INNO_DIR%" "/dShellNameShort=%SHELL_NAME_SHORT%" "/dAppUserId=%APP_USER_ID%" "/dVersion=%APP_VERSION%" "/dSourceDir=%CD%" "/dAppxFullName=%APP_APPX_FULL_NAME%"
if errorlevel 1 goto :fail_in_zed

popd
echo Done. Installer: %ZED_DIR%\target\%APP_SETUP_NAME%.exe
exit /b 0

:fail_in_zed
set "EXIT_CODE=%errorlevel%"
popd
exit /b %EXIT_CODE%