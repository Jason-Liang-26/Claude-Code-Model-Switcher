#!/bin/bash
# CCMS — Claude Code Model Switcher Linux/macOS Installer
set -euo pipefail

BIN_DIR="${HOME}/.local/bin"
LIB_DIR="${HOME}/.local/lib/claude-code-model-switcher"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Claude Code Model Switcher - Install ==="
echo ""

# ── 前置依赖检查 ──
missing_deps=()
if ! command -v python3 >/dev/null 2>&1; then
    missing_deps+=("python3")
fi

# Linux 下检查 secret-tool (libsecret)
plat="$(uname -s)"
if [ "$plat" = "Linux" ] && ! command -v secret-tool >/dev/null 2>&1; then
    missing_deps+=("secret-tool (libsecret-tools)")
fi

if [ ${#missing_deps[@]} -gt 0 ]; then
    echo "[!] Missing dependencies: ${missing_deps[*]}" >&2
    if [ "$plat" = "Linux" ]; then
        echo "[!] Install libsecret-tools:" >&2
        echo "    Debian/Ubuntu: sudo apt install libsecret-tools" >&2
        echo "    Fedora:        sudo dnf install libsecret-tools" >&2
        echo "    Arch:          sudo pacman -S libsecret" >&2
    fi
    echo ""
fi

# ── 创建目录 ──
mkdir -p "$BIN_DIR" "$LIB_DIR"
echo "[+] Created $BIN_DIR"
echo "[+] Created $LIB_DIR"

# ── 复制主脚本 ──
cp "$SCRIPT_DIR/claude-code-model-switcher.py" "$LIB_DIR/"
echo "[+] Installed claude-code-model-switcher.py -> $LIB_DIR"

# ── 生成启动器 ──
cat > "$BIN_DIR/claude-code-model-switcher" << 'EOF'
#!/bin/bash
exec python3 "$HOME/.local/lib/claude-code-model-switcher/claude-code-model-switcher.py" "$@"
EOF
chmod +x "$BIN_DIR/claude-code-model-switcher"
echo "[+] Installed launcher -> $BIN_DIR/claude-code-model-switcher"

# ── 检查 PATH ──
echo ""
if [[ ":$PATH:" == *":$BIN_DIR:"* ]]; then
    echo "[OK] $BIN_DIR is in PATH"
else
    echo "[!] $BIN_DIR is NOT in PATH"
    echo "[!] Add this to your shell profile (~/.bashrc, ~/.zshrc, etc.):"
    echo '    export PATH="$HOME/.local/bin:$PATH"'
fi

echo ""
echo "=== Done ==="
echo "Usage: claude-code-model-switcher [--help | --env | --get-sk | ...]"
