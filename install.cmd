@echo off
setlocal

echo === Claude Code Model Switcher - Install ===
echo.

set "BIN_DIR=%USERPROFILE%\.local\bin"
set "LIB_DIR=%USERPROFILE%\.local\lib\claude-code-model-switcher"

if not exist "%BIN_DIR%" mkdir "%BIN_DIR%" && echo [+] Created %BIN_DIR%
if not exist "%LIB_DIR%" mkdir "%LIB_DIR%" && echo [+] Created %LIB_DIR%

copy /Y "%~dp0claude-code-model-switcher.py" "%LIB_DIR%\claude-code-model-switcher.py" >nul
echo [+] Installed claude-code-model-switcher.py -^> %LIB_DIR%

:: Write launcher cmd to bin dir
echo @echo off> "%BIN_DIR%\claude-code-model-switcher.cmd"
echo call python "%%%%~dp0..\lib\claude-code-model-switcher\claude-code-model-switcher.py" %%%%*>> "%BIN_DIR%\claude-code-model-switcher.cmd"
echo [+] Installed launcher -^> %BIN_DIR%\claude-code-model-switcher.cmd

echo.
echo %PATH% | findstr /i ".local\\bin" >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] %BIN_DIR% is in PATH
) else (
    echo [!] %BIN_DIR% is NOT in PATH
    echo [!] Add to your system PATH: %BIN_DIR%
)

echo.
echo === Done ===
echo Usage: claude-code-model-switcher [--help ^| --env ^| --get-sk ^| ...]
