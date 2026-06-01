@echo off
setlocal EnableExtensions

cd /d "%~dp0"

set "CHANNEL=stable"
set "ZED_DIR=zed"

if not exist "%ZED_DIR%\Cargo.toml" (
    echo [error] Zed source directory not found: %ZED_DIR%
    exit /b 1
)

echo [1/3] Setting release channel: %CHANNEL%
pushd "%ZED_DIR%"
if errorlevel 1 exit /b %errorlevel%

<nul set /p "=%CHANNEL%" > crates\zed\RELEASE_CHANNEL
set "RELEASE_CHANNEL=%CHANNEL%"
set "ZED_RELEASE_CHANNEL=%CHANNEL%"

echo [2/3] Cleaning build cache for channel-sensitive crates...
cargo clean -p zed -p cli -p auto_update_helper -p windows_resources
if errorlevel 1 goto :fail_in_zed

echo [3/3] Building Zed...
cargo build --release --locked
if errorlevel 1 goto :fail_in_zed

cargo build --release --package cli --locked
if errorlevel 1 goto :fail_in_zed

popd
echo Done.
exit /b 0

:fail_in_zed
set "EXIT_CODE=%errorlevel%"
popd
exit /b %EXIT_CODE%