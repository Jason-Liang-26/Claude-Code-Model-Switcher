<#
Claude Model Switcher - Install Script
#>
Write-Host "=== Claude Model Switcher - Install ===" -ForegroundColor Cyan
Write-Host ""

$BinDir  = "$env:USERPROFILE\.local\bin"
$LibDir  = "$env:USERPROFILE\.local\lib\claude-code-model-switcher"
$Script  = Split-Path -Parent $MyInvocation.MyCommand.Path

# 创建目录
if (-not (Test-Path $BinDir)) { New-Item -ItemType Directory -Path $BinDir -Force | Out-Null; Write-Host "[+] Created $BinDir" -ForegroundColor Green }
if (-not (Test-Path $LibDir)) { New-Item -ItemType Directory -Path $LibDir -Force | Out-Null; Write-Host "[+] Created $LibDir" -ForegroundColor Green }

# 复制 Python 脚本
Copy-Item -Path "$Script\claude-code-model-switcher.py" -Destination "$LibDir\claude-code-model-switcher.py" -Force
Write-Host "[+] Installed claude-code-model-switcher.py -> $LibDir" -ForegroundColor Green

# 生成启动器 cmd
@"
@echo off
call python "%~dp0..\lib\claude-code-model-switcher\claude-code-model-switcher.py" %*
"@ | Out-File -FilePath "$BinDir\claude-code-model-switcher.cmd" -Encoding ASCII
Write-Host "[+] Installed launcher -> $BinDir\claude-code-model-switcher.cmd" -ForegroundColor Green

# 检查 PATH
Write-Host ""
if ($env:PATH -like "*.local\bin*") {
    Write-Host "[OK] $BinDir is in PATH" -ForegroundColor Green
} else {
    Write-Host "[!] $BinDir is NOT in PATH" -ForegroundColor Yellow
    Write-Host "[!] Run this command to add it (admin):" -ForegroundColor Yellow
    Write-Host "    [Environment]::SetEnvironmentVariable('PATH', `$env:PATH + ';$BinDir', 'User')" -ForegroundColor White
}

Write-Host ""
Write-Host "=== Done ===" -ForegroundColor Cyan
Write-Host "Usage: claude-code-model-switcher [--help | --env | --get-sk | ...]"
Read-Host "Press Enter to exit"
