#!/usr/bin/env python3
"""
Claude 自定义模型切换器
- 模型配置统一管理在 ~/.claude/custom-models.json
- sk 存入 OS 凭据管理器，配置文件只存 credential 引用
- 生成 apiKeyHelper 脚本供 Claude Code 运行时动态取 sk
- 支持跨平台凭据后端: wincred / macos-keychain / secret-service
"""
from __future__ import annotations

import json
import os
import sys
import shutil
import re
import subprocess
import platform
import textwrap


CUSTOM_MODELS_PATH = os.path.expanduser("~/.claude/custom-models.json")
# 项目级路径基于当前工作目录（CWD），确保从任意位置运行都找到当前项目的 .claude/
def _project_path(subpath: str) -> str:
    return os.path.join(os.getcwd(), ".claude", subpath)

PROJECT_SETTINGS_PATH = _project_path("settings.json")
HELPER_SCRIPT_PATH = _project_path("get-sk.sh")

# ============================================================
# 终端交互
# ============================================================

def _setup_console():
    """设置终端原始模式（逐键读取）"""
    global _WIN_RAW
    # Windows: Win32 API
    try:
        import ctypes
        from ctypes import wintypes
        import atexit
        STD_INPUT_HANDLE = -10
        ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200
        ENABLE_LINE_INPUT = 0x0002
        ENABLE_ECHO_INPUT = 0x0004
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        h = kernel32.GetStdHandle(STD_INPUT_HANDLE)
        if h not in (wintypes.HANDLE(-1).value, None, 0):
            mode = wintypes.DWORD()
            if kernel32.GetConsoleMode(h, ctypes.byref(mode)):
                new_mode = mode.value | ENABLE_VIRTUAL_TERMINAL_INPUT
                new_mode &= ~(ENABLE_LINE_INPUT | ENABLE_ECHO_INPUT)
                if new_mode != mode.value:
                    kernel32.SetConsoleMode(h, new_mode)
                atexit.register(lambda: kernel32.SetConsoleMode(h, mode.value))
                _WIN_RAW = True
                return
    except Exception:
        pass
    # Unix / WSL: termios
    try:
        import termios
        import tty
        import atexit
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        atexit.register(lambda: termios.tcsetattr(fd, termios.TCSADRAIN, old))
        tty.setraw(fd)
        return
    except Exception:
        pass

_WIN_RAW = False

def _getch():
    """跨平台获取单个字符（不回显）"""
    try:
        import msvcrt
        return msvcrt.getch().decode("latin-1")
    except Exception:
        pass
    try:
        return sys.stdin.read(1)
    except Exception:
        return "\x03"

def _clear_lines(n: int):
    for _ in range(n):
        sys.stdout.write("\033[K\033[F")
    sys.stdout.write("\033[K")

def _print_color(text: str, color: str = "", bold: bool = False, dim: bool = False):
    prefix = ""
    if bold: prefix += "\033[1m"
    elif dim: prefix += "\033[2m"
    prefix += color
    sys.stdout.write(f"{prefix}{text}\033[0m")

def select_from_list(items: list[str], title: str = "",
                     prompt: str = "↑↓ 选择 Enter 确认") -> int | None:
    if not items:
        return None
    selected = 0
    n = len(items)
    header_lines = 1 if title else 0
    total_display = header_lines + n + 1

    def render():
        if title:
            _print_color(f"{title}\n", bold=True)
        for i, item in enumerate(items):
            prefix = "\033[7m > \033[0m" if i == selected else "   "
            print(f"{prefix}{item}")
        _print_color(f"{prompt}\n", dim=True)

    render()
    while True:
        ch = _getch()
        if ch == "\xe0":
            ch2 = _getch()
            if ch2 == "H":
                selected = (selected - 1) % n
            elif ch2 == "P":
                selected = (selected + 1) % n
            else:
                continue
        elif ch == "\x1b":
            ch2 = _getch()
            if ch2 == "[":
                ch3 = _getch()
                if ch3 == "A":
                    selected = (selected - 1) % n
                elif ch3 == "B":
                    selected = (selected + 1) % n
                else:
                    continue
            elif ch2 == "\x1b":
                _clear_lines(total_display)
                return None
            else:
                continue
        elif ch in ("\r", "\n"):
            _clear_lines(total_display)
            return selected
        elif ch == "\x03":
            _clear_lines(total_display)
            raise KeyboardInterrupt
        else:
            continue
        _clear_lines(total_display)
        render()

def confirm(text: str, default_no: bool = False) -> bool:
    hint = "[y/N]" if default_no else "[Y/n]"
    sys.stdout.write(f"{text} {hint} ")
    sys.stdout.flush()
    ch = _getch().lower()
    print(ch if ch != "\r" else "")
    if ch in ("\r", "\n"):
        return not default_no
    return ch == "y"

def input_with_prompt(prompt_text: str) -> str:
    sys.stdout.write(prompt_text)
    sys.stdout.flush()
    val = ""
    while True:
        ch = _getch()
        if ch in ("\r", "\n"):
            print()
            if val: return val
            _print_color("（不能为空，请重新输入）\n", dim=True)
            sys.stdout.write(prompt_text)
            sys.stdout.flush()
            continue
        elif ch in ("\x7f", "\x08"):
            if val:
                val = val[:-1]
                sys.stdout.write("\b \b")
                sys.stdout.flush()
        elif ch == "\x03":
            raise KeyboardInterrupt
        elif ch == "\x1b":
            _print_color("（不支持方向键输入）\n", dim=True)
            sys.stdout.write(prompt_text)
            sys.stdout.flush()
            continue
        else:
            val += ch
            sys.stdout.write(ch)
            sys.stdout.flush()


def _press_enter(prompt_text: str = "按 Enter 返回菜单..."):
    """等待 Enter 按键（兼容原始模式）。ESC 返回 None，Ctrl+C 抛出异常。"""
    _print_color(f"{prompt_text}\n", dim=True)
    while True:
        ch = _getch()
        if ch in ("\r", "\n"):
            return
        elif ch == "\x1b":
            return
        elif ch == "\x03":
            raise KeyboardInterrupt


# ============================================================
# 凭据后端 (Credential Backend)
# ============================================================

CRED_WINCRED = "wincred"
CRED_MACOS_KEYCHAIN = "macos-keychain"
CRED_SECRET_SERVICE = "secret-service"

def _detect_platform() -> str:
    """返回当前 OS 标识: windows / macos / linux"""
    s = platform.system().lower()
    if s == "windows": return "windows"
    if s == "darwin": return "macos"
    return "linux"

def cred_available_backends() -> list[str]:
    """返回当前 OS 可用的凭据后端列表"""
    plat = _detect_platform()
    if plat == "windows": return [CRED_WINCRED]
    if plat == "macos":   return [CRED_MACOS_KEYCHAIN]
    # Linux: 检测 secret-tool 是否可用
    try:
        subprocess.run(["secret-tool", "--version"],
                       capture_output=True, timeout=3)
        return [CRED_SECRET_SERVICE]
    except Exception:
        return []

def cred_default_config(model_name: str) -> dict:
    """为当前 OS 生成默认的 credential 配置"""
    plat = _detect_platform()
    if plat == "windows":
        return {"type": CRED_WINCRED, "target": f"claude/{model_name}"}
    elif plat == "macos":
        return {"type": CRED_MACOS_KEYCHAIN, "service": "claude",
                "account": model_name}
    else:
        return {"type": CRED_SECRET_SERVICE, "label": f"claude/{model_name}",
                "key": f"claude-{model_name}"}

def cred_store(cred: dict, sk: str):
    """将 sk 存入凭据后端"""
    t = cred.get("type", "")
    if t == CRED_WINCRED:
        _wincred_store(cred["target"], sk)
    elif t == CRED_MACOS_KEYCHAIN:
        _macos_keychain_store(cred["service"], cred["account"], sk)
    elif t == CRED_SECRET_SERVICE:
        _secret_service_store(cred.get("key"), cred.get("label", ""), sk)
    else:
        raise ValueError(f"未知凭据后端: {t}")

def cred_retrieve(cred: dict) -> str | None:
    """从凭据后端读取 sk"""
    t = cred.get("type", "")
    try:
        if t == CRED_WINCRED:
            return _wincred_retrieve(cred["target"])
        elif t == CRED_MACOS_KEYCHAIN:
            return _macos_keychain_retrieve(cred["service"], cred["account"])
        elif t == CRED_SECRET_SERVICE:
            return _secret_service_retrieve(cred.get("key"))
    except Exception:
        return None
    return None

def cred_delete(cred: dict):
    """从凭据后端删除 sk"""
    t = cred.get("type", "")
    if t == CRED_WINCRED:
        _wincred_delete(cred["target"])
    elif t == CRED_MACOS_KEYCHAIN:
        _macos_keychain_delete(cred["service"], cred["account"])
    elif t == CRED_SECRET_SERVICE:
        _secret_service_delete(cred.get("key"))

# ---- Windows Credential Manager (advapi32) ----

import ctypes
from ctypes import wintypes

_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_ENTERPRISE = 2


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", ctypes.c_wchar_p),
        ("Comment", ctypes.c_wchar_p),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.c_void_p),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", ctypes.c_wchar_p),
        ("UserName", ctypes.c_wchar_p),
    ]


def _wincred_store(target: str, sk: str):
    blob = sk.encode("utf-16-le")
    buf = ctypes.create_string_buffer(blob)
    cred = _CREDENTIALW(
        Flags=0, Type=_CRED_TYPE_GENERIC,
        TargetName=target, Comment=None,
        CredentialBlobSize=len(blob),
        CredentialBlob=ctypes.cast(buf, ctypes.c_void_p),
        Persist=_CRED_PERSIST_ENTERPRISE,
        AttributeCount=0, Attributes=None,
        TargetAlias=None, UserName="claude",
    )
    wincred = ctypes.WinDLL("advapi32", use_last_error=True)
    wincred.CredWriteW(ctypes.byref(cred), 0)


def _wincred_retrieve(target: str) -> str | None:
    pcred = ctypes.c_void_p()
    wincred = ctypes.WinDLL("advapi32", use_last_error=True)
    if not wincred.CredReadW(target, _CRED_TYPE_GENERIC, 0, ctypes.byref(pcred)):
        return None
    try:
        cred = ctypes.cast(pcred, ctypes.POINTER(_CREDENTIALW)).contents
        if cred.CredentialBlobSize > 0:
            raw = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
            return raw.decode("utf-16-le")
        return None
    finally:
        wincred.CredFree(pcred)

def _wincred_delete(target: str):
    wincred = ctypes.WinDLL("advapi32", use_last_error=True)
    wincred.CredDeleteW(target, _CRED_TYPE_GENERIC, 0)

# ---- macOS Keychain ----

def _macos_keychain_store(service: str, account: str, sk: str):
    subprocess.run(["security", "add-generic-password", "-U",
                    "-s", service, "-a", account, "-w", sk],
                   capture_output=True, check=True)

def _macos_keychain_retrieve(service: str, account: str) -> str | None:
    r = subprocess.run(["security", "find-generic-password",
                        "-s", service, "-a", account, "-w"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return r.stdout.strip()

def _macos_keychain_delete(service: str, account: str):
    subprocess.run(["security", "delete-generic-password",
                    "-s", service, "-a", account],
                   capture_output=True)

# ---- Linux secret-service (secret-tool) ----

def _secret_service_store(key: str | None, label: str, sk: str):
    if not key:
        key = label
    subprocess.run(["secret-tool", "store", "--label", label,
                    "key", key],
                   input=sk, text=True, capture_output=True, check=True)

def _secret_service_retrieve(key: str | None) -> str | None:
    if not key:
        return None
    r = subprocess.run(["secret-tool", "lookup", "key", key],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return r.stdout.strip()

def _secret_service_delete(key: str | None):
    if not key:
        return
    subprocess.run(["secret-tool", "clear", "key", key],
                   capture_output=True)

# ============================================================
# 模型数据读写
# ============================================================

def _json_strip_trailing_commas(text: str) -> str:
    text = re.sub(r',\s*}', '}', text)
    text = re.sub(r',\s*]', ']', text)
    return text

def load_custom_models() -> dict:
    if not os.path.isfile(CUSTOM_MODELS_PATH):
        return {}
    raw = open(CUSTOM_MODELS_PATH, "r", encoding="utf-8").read()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        fixed = _json_strip_trailing_commas(raw)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            _print_color(f"错误: {CUSTOM_MODELS_PATH} 格式错误，无法解析\n", color="\033[31m")
            sys.exit(1)

def save_custom_models(models: dict):
    os.makedirs(os.path.dirname(CUSTOM_MODELS_PATH), exist_ok=True)
    with open(CUSTOM_MODELS_PATH, "w", encoding="utf-8") as f:
        json.dump(models, f, indent=2, ensure_ascii=False)
        f.write("\n")

def load_project_settings() -> dict:
    if not os.path.isfile(PROJECT_SETTINGS_PATH):
        return {}
    with open(PROJECT_SETTINGS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_project_settings(settings: dict):
    os.makedirs(os.path.dirname(PROJECT_SETTINGS_PATH), exist_ok=True)
    with open(PROJECT_SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
        f.write("\n")

def load_user_settings() -> dict:
    path = os.path.expanduser("~/.claude/settings.json")
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def resolve_model(target: str, models: dict) -> tuple[str, dict] | None:
    """通过别名或 modelName 查找模型，返回 (alias, config)"""
    if target in models:
        return target, models[target]
    for alias, cfg in models.items():
        if cfg.get("modelName") == target:
            return alias, cfg
    return None


def get_current_alias(models: dict) -> str | None:
    """返回当前项目设置的模型别名。
    优先读 CCMS_MODEL_ALIAS（本工具写入），否则从 ANTHROPIC_MODEL 反查。
    未找到别名的返回 ANTHROPIC_MODEL 原始值（非托管）。"""
    s = load_project_settings()
    env = s.get("env", {})
    current_model = env.get("ANTHROPIC_MODEL", None)
    if not current_model:
        return None
    # 优先读本工具写入的 alias 标记
    alias_tag = env.get("CCMS_MODEL_ALIAS", None)
    if alias_tag and alias_tag in models:
        return alias_tag
    # 反查 modelName
    for alias, cfg in models.items():
        if cfg.get("modelName") == current_model or alias == current_model:
            return alias
    return current_model  # 非托管，返回原始值

# ============================================================
# 模型配置兼容: 旧版 sk 字段 → 迁移到凭据后端
# ============================================================

def migrate_models(models: dict) -> dict:
    """迁移旧格式: 补 modelName 字段 + sk→credential"""
    changed = False
    for alias, cfg in models.items():
        if "modelName" not in cfg:
            cfg["modelName"] = alias
            changed = True
        if "sk" in cfg and "credential" not in cfg:
            sk = cfg.pop("sk")
            if sk:
                cred = cred_default_config(alias)
                cred_store(cred, sk)
                cfg["credential"] = cred
                changed = True
    if changed:
        save_custom_models(models)
    return models

# ============================================================
# 写项目配置 + helper 脚本
# ============================================================

def write_model_to_project(alias: str, model_config: dict):
    """写入项目 .claude/settings.json (env 不含 sk) 并生成 helper 脚本"""
    settings = load_project_settings()
    # 合并 env（保留用户其他环境变量）
    if "env" not in settings:
        settings["env"] = {}
    settings["env"]["ANTHROPIC_BASE_URL"] = model_config.get("url", "")
    settings["env"]["ANTHROPIC_MODEL"] = model_config.get("modelName", alias)
    settings["env"]["CCMS_MODEL_ALIAS"] = alias  # 本工具管理标记
    if _detect_platform() == "windows":
        settings["apiKeyHelper"] = "powershell -NoProfile -Command .claude\\get-sk.ps1"
    else:
        settings["apiKeyHelper"] = ".claude/get-sk.sh"
    save_project_settings(settings)
    _generate_helper_scripts()

def _generate_helper_scripts():
    """生成 helper 脚本（.sh + .ps1），委托给 Python 取凭据"""
    helper_dir = os.path.dirname(HELPER_SCRIPT_PATH)
    os.makedirs(helper_dir, exist_ok=True)

    root = os.getcwd()
    py = sys.executable

    # ── PowerShell 版 (get-sk.ps1) ──
    # 用正斜杠避免 Python -c 参数里的反斜杠被当成转义符
    root_ps1 = root.replace("\\", "/")
    ps1 = textwrap.dedent(f"""\
    # Claude Code apiKeyHelper — 由 claude-code-model-switcher.py 自动维护
    param($ModelArg)
    $root = "{root_ps1}"
    $py = "{py}"
    if (-not $ModelArg) {{
        $ModelArg = & $py -c "
    import json
    s = json.load(open('$root/.claude/settings.json'))
    print(s.get('env', {{}}).get('ANTHROPIC_MODEL', ''))
    " 2>$null
    }}
    if (-not $ModelArg) {{ exit 1 }}
    $sk = & $py "$root/claude-code-model-switcher.py" --get-sk $ModelArg 2>$null
    Write-Output $sk
    """)
    with open(os.path.join(helper_dir, "get-sk.ps1"), "w", encoding="utf-8") as f:
        f.write(ps1)

    # ── Bash 版 (get-sk.sh) ──
    # 生成 WSL 兼容路径
    root_nix = root.replace("\\", "/")
    if len(root_nix) > 1 and root_nix[1] == ":":
        drive = root_nix[0].lower()
        root_nix = f"/mnt/{drive}{root_nix[2:]}"
    py_nix = py.replace("\\", "/")
    if len(py_nix) > 1 and py_nix[1] == ":":
        drive = py_nix[0].lower()
        py_nix = f"/mnt/{drive}{py_nix[2:]}"

    sh = textwrap.dedent(f"""\
    #!/bin/bash
    # Claude Code apiKeyHelper — 由 claude-code-model-switcher.py 自动维护
    set -euo pipefail
    SELF_DIR="{root_nix}"
    _PY=""
    for _p in "{py_nix}" python3 python; do
        if command -v "$_p" >/dev/null 2>&1; then
            _PY="$_p"
            break
        fi
    done
    [ -z "$_PY" ] && exit 1
    MODEL="${{1:-}}"
    if [ -z "$MODEL" ]; then
        MODEL=$("$_PY" -c "
    import json
    s = json.load(open('$SELF_DIR/.claude/settings.json'))
    print(s.get('env', {{}}).get('ANTHROPIC_MODEL', ''))
    " 2>/dev/null || echo "")
    fi
    [ -z "$MODEL" ] && exit 1
    exec "$_PY" "$SELF_DIR/claude-code-model-switcher.py" --get-sk "$MODEL"
    """)
    with open(os.path.join(helper_dir, "get-sk.sh"), "w", encoding="utf-8", newline="\n") as f:
        f.write(sh)
    os.chmod(os.path.join(helper_dir, "get-sk.sh"), 0o700)

# ============================================================
# 导出 / 迁移工具
# ============================================================

def _get_sk(model_name: str, model_config: dict) -> str | None:
    """从凭据后端取 sk"""
    cred = model_config.get("credential", {})
    if not cred:
        return None
    return cred_retrieve(cred)

def print_env_export(alias: str, model_config: dict):
    sk = _get_sk(alias, model_config)
    if not sk:
        return
    print(f"""\n\033[33m⚠  如需在当前终端设置认证凭据，请执行:\033[0m
  \033[1mexport ANTHROPIC_API_KEY="{sk}"
  export ANTHROPIC_AUTH_TOKEN="{sk}"\033[0m
\033[2m或使用 eval 模式: python claude-code-model-switcher.py --env\033[0m
""")

def cmd_env(args: list[str]):
    """--env 模式: 输出 export 命令"""
    models = load_custom_models()
    models = migrate_models(models)
    current = get_current_alias(models)
    target_arg = args[0] if args else current
    if target_arg:
        result = resolve_model(target_arg, models)
        if result:
            _alias, cfg = result
            sk = _get_sk(_alias, cfg)
            if sk:
                print(f'export ANTHROPIC_API_KEY="{sk}"')
                print(f'export ANTHROPIC_AUTH_TOKEN="{sk}"')
            else:
                _print_color(f"错误: 模型 \"{target_arg}\" 凭据不可用\n", color="\033[31m")
                sys.exit(1)
        else:
            _print_color(f"错误: 模型 \"{target_arg}\" 不存在\n", color="\033[31m")
            sys.exit(1)
    else:
        _print_color("错误: 未指定模型，当前项目也未配置模型\n", color="\033[31m")
        print("用法: python claude-code-model-switcher.py --env [模型名/别名]")
        sys.exit(1)

def cmd_get_sk(args: list[str]):
    """--get-sk 模式: 输出原始 sk（供 apiKeyHelper 调用）"""
    models = load_custom_models()
    models = migrate_models(models)
    target = args[0] if args else get_current_alias(models)
    if target:
        result = resolve_model(target, models)
        if result:
            _alias, cfg = result
            sk = _get_sk(_alias, cfg)
            if sk:
                sys.stdout.write(sk)
                return
            print("错误: 模型凭据不可用", file=sys.stderr)
            sys.exit(1)
        else:
            print(f"错误: 模型 \"{target}\" 不存在", file=sys.stderr)
            sys.exit(1)
    else:
        print("错误: 未指定模型", file=sys.stderr)
        sys.exit(1)

def cmd_reveal():
    """--reveal 模式: 展示所有模型凭据状态（用于迁移）"""
    models = load_custom_models()
    models = migrate_models(models)
    if not models:
        _print_color("没有配置任何模型\n", color="\033[33m")
        return
    print(f"平台: {_detect_platform()}")
    print(f"可用凭据后端: {', '.join(cred_available_backends())}")
    print()
    print(f"{'别名':<20} {'modelName':<25} {'凭据后端':<20} {'状态':<10} sk")
    print("-" * 95)
    result = {}
    for alias, cfg in models.items():
        mn = cfg.get("modelName", alias)
        cred = cfg.get("credential", {})
        backend = cred.get("type", "无")
        sk = _get_sk(alias, cfg)
        status = "\033[32m可用\033[0m" if sk else "\033[31m不可用\033[0m"
        sk_preview = (sk[:8] + "...") if sk else "-"
        print(f"{alias:<20} {mn:<25} {backend:<20} {status:<10} {sk_preview}")
        result[alias] = {"modelName": mn, "credential": cred, "sk": sk or ""}
    print()
    if confirm("导出全部 sk 到 stdout（JSON 格式，用于迁移）？", default_no=True):
        out = {}
        for name, cfg in models.items():
            sk = _get_sk(name, cfg)
            out[name] = {"url": cfg.get("url", ""), "credential": cfg.get("credential", {}),
                         "sk": sk or ""}
        print(json.dumps(out, indent=2, ensure_ascii=False))

def cmd_migrate_import():
    """--migrate-import 模式: 从 stdin JSON 批量导入 sk 到当前 OS 凭据后端"""
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        _print_color("错误: stdin 输入不是合法 JSON\n", color="\033[31m")
        sys.exit(1)
    models = load_custom_models()
    imported = 0
    for alias, entry in data.items():
        if not entry.get("sk"):
            continue
        mn = entry.get("modelName", alias)
        if alias in models:
            cred = cred_default_config(alias)
            cred_store(cred, entry["sk"])
            models[alias]["credential"] = cred
            models[alias]["modelName"] = mn
            imported += 1
            print(f"  ✔ {alias} → {mn}")
        else:
            cred = cred_default_config(alias)
            cred_store(cred, entry["sk"])
            models[alias] = {"url": entry.get("url", ""),
                             "modelName": mn,
                             "credential": cred}
            imported += 1
            print(f"  ✔ {alias} → {mn} (新建)")
    save_custom_models(models)
    _print_color(f"成功导入 {imported} 个模型到 {_detect_platform()} 凭据后端\n", color="\033[32m")

# ============================================================
# 冲突检测
# ============================================================

def detect_env_api_key_conflict():
    """检查是否有 settings.json 在 env 中配置了 ANTHROPIC_API_KEY"""
    conflicts = []
    # 项目级
    ps = load_project_settings()
    if ps.get("env", {}).get("ANTHROPIC_API_KEY"):
        conflicts.append(("项目", PROJECT_SETTINGS_PATH))
    # 用户级
    us = load_user_settings()
    if us.get("env", {}).get("ANTHROPIC_API_KEY"):
        conflicts.append(("用户", os.path.expanduser("~/.claude/settings.json")))
    return conflicts

# ============================================================
# 环境信息显示
# ============================================================

def get_env_info_lines() -> list[tuple[str, str]]:
    """返回 (标签, 值, 颜色) 的环境信息行。
    颜色: ''=默认, 'green'=正常, 'yellow'=警告, 'red'=错误"""
    lines = []
    plat = _detect_platform()
    os_name = {"windows": "Windows", "macos": "macOS", "linux": "Linux"}.get(plat, plat)

    # ── 系统信息 ──
    lines.append(("系统", f"{os_name} ({platform.machine()})", "green"))
    backends = cred_available_backends()
    backend_labels = {CRED_WINCRED: "Windows Credential Manager",
                      CRED_MACOS_KEYCHAIN: "macOS Keychain",
                      CRED_SECRET_SERVICE: "secret-service (libsecret)"}
    blabels = [backend_labels.get(b, b) for b in backends]
    backend_status = ", ".join(blabels) if blabels else "无"
    backend_color = "green" if blabels else "red"
    lines.append(("凭据后端", backend_status, backend_color))

    # ── 当前项目 (.claude/) ──
    lines.append(("项目路径", os.getcwd(), ""))
    lines.append(("配置文件", ".claude/settings.json", ""))

    # apiKeyHelper 状态
    helper_dir = os.path.dirname(HELPER_SCRIPT_PATH)
    has_sh = os.path.isfile(os.path.join(helper_dir, "get-sk.sh"))
    has_ps1 = os.path.isfile(os.path.join(helper_dir, "get-sk.ps1"))
    if has_sh or has_ps1:
        names = []
        if has_sh: names.append("get-sk.sh")
        if has_ps1: names.append("get-sk.ps1")
        lines.append(("apiKeyHelper", f"{', '.join(names)}", "green"))
    else:
        lines.append(("apiKeyHelper", "未生成（切换模型后自动创建）", "yellow"))

    # 冲突检测
    conflicts = detect_env_api_key_conflict()
    if conflicts:
        for scope, path in conflicts:
            lines.append(("⚠ 冲突", f"{scope}级 {path}", "red"))
            lines.append(("", "env 中配置了 ANTHROPIC_API_KEY，将覆盖 apiKeyHelper", "red"))

    # ── 全局 (~/.claude/) ──
    models = load_custom_models()
    names = list(models.keys())
    if names:
        lines.append(("全局模型", f"~/.claude/custom-models.json — {len(names)} 个模型", ""))
    else:
        lines.append(("全局模型", "~/.claude/custom-models.json — 未配置", "yellow"))

    return lines

# ============================================================
# 主菜单
# ============================================================

def main():
    _setup_console()

    models = load_custom_models()
    models = migrate_models(models)

    current = get_current_alias(models)

    while True:
        print("\033[2J\033[H", end="")
        width = shutil.get_terminal_size().columns
        _print_color(f"{' Claude 模型切换器 ':=^{width}}\n", bold=True)

        # 当前目录
        cwd = os.getcwd()
        _print_color("  📁 当前目录: ", dim=True)
        _print_color(f"{cwd}\n\n", dim=True)

        # 渲染环境信息，按分区展示
        info = get_env_info_lines()
        last_section = None
        sections = {
            "系统": "系统信息 (System)",
            "凭据后端": None,
            "项目路径": "当前项目 (.claude/settings.json)",
            "配置文件": None,
            "apiKeyHelper": None,
            "⚠ 冲突": None,
            "全局模型": "全局配置 (~/.claude/custom-models.json)",
        }
        for label, value, color in info:
            if label in sections and sections[label] is not None:
                _print_color(f"  ── {sections[label]} ", dim=True)
                print("─" * max(1, width - 6 - len(sections[label])))
            # 渲染行
            if not label:
                # 无标签行（续行），多缩进
                _print_color("     ", dim=True)
            elif label == "⚠ 冲突":
                _print_color("  ⚠ ", dim=True)
            else:
                _print_color(f"  {label}: ", dim=True)
            # 值着色
            col = ""
            if color == "green": col = "\033[32m"
            elif color == "yellow": col = "\033[33m"
            elif color == "red": col = "\033[31m"
            _print_color(f"{value}\n", color=col)

        print()
        # 检测托管状态
        ps = load_project_settings()
        managed_alias = ps.get("env", {}).get("CCMS_MODEL_ALIAS", None)
        if managed_alias:
            _print_color(f"  ★ 模型: ", dim=True)
            _print_color(f"{managed_alias}", bold=True)
            mn = ps.get("env", {}).get("ANTHROPIC_MODEL", "")
            if mn and mn != managed_alias:
                _print_color(f" → {mn}", dim=True)
            _print_color("\n", dim=True)
        elif current:
            an_model = ps.get("env", {}).get("ANTHROPIC_MODEL", "?")
            _print_color(f"  ⚠ 未托管 model-switcher", color="\033[33m")
            _print_color(f"，当前 ANTHROPIC_MODEL: {an_model}\n", dim=True)
        else:
            _print_color(f"  ⚠ 未配置 ANTHROPIC_MODEL\n", dim=True)
        print()

        names = list(models.keys())
        menu_items = []
        if names: menu_items.append("切换模型")
        menu_items.append("添加模型")
        if names: menu_items.append("删除模型")
        menu_items.append("查看凭据状态")
        menu_items.append("退出")

        idx = select_from_list(menu_items, prompt="↑↓ 选择 Enter 确认  ESC 退出")
        if idx is None:
            break

        choice = menu_items[idx]

        # ---- 切换 ----
        if choice == "切换模型":
            sel = select_from_list(names, title="选择要切换的模型")
            if sel is None: continue
            alias = names[sel]
            cfg = models[alias]
            sk = _get_sk(alias, cfg)
            if not sk:
                _print_color(f"⚠  模型 \"{alias}\" 的凭据不可用\n", color="\033[33m")
                if confirm("重新设置 API Key？"):
                    new_sk = input_with_prompt("API Key (sk-...): ")
                    cred = cfg.get("credential", cred_default_config(alias))
                    cred_store(cred, new_sk)
                    cfg["credential"] = cred
                    models[alias] = cfg
                    save_custom_models(models)
                else:
                    continue
            write_model_to_project(alias, cfg)
            current = alias
            mn = cfg.get("modelName", alias)
            _print_color(f"✔ 已切换至: {alias} (modelName: {mn})\n", color="\033[32m")
            print_env_export(alias, cfg)
            _press_enter()
            continue

        # ---- 添加 ----
        elif choice == "添加模型":
            print("\n\033[1m添加新模型\033[0m")
            alias = input_with_prompt("别名: ")
            if not alias: continue
            mn = input_with_prompt("模型名 (modelName): ")
            if not mn: continue
            url = input_with_prompt("API URL: ")
            sk = input_with_prompt("API Key (sk-...): ")

            if alias in models:
                if not confirm(f"别名 \"{alias}\" 已存在，覆盖吗？", default_no=True):
                    continue

            cred = cred_default_config(alias)
            cred_store(cred, sk)
            models[alias] = {"url": url, "modelName": mn, "credential": cred}
            save_custom_models(models)
            _print_color(f"✔ 模型 \"{alias}\" → {mn} 已保存\n", color="\033[32m")

            if confirm("立即切换到该模型吗？"):
                write_model_to_project(alias, models[alias])
                current = alias
                _print_color(f"✔ 已切换至: {alias} (modelName: {mn})\n", color="\033[32m")
                print_env_export(alias, models[alias])

            _press_enter()
            continue

        # ---- 删除 ----
        elif choice == "删除模型":
            if not names:
                _press_enter("没有模型可删除。按 Enter 返回...")
                continue
            sel = select_from_list(names, title="选择要删除的模型")
            if sel is None: continue
            alias = names[sel]
            _print_color(f"\n⚠  确认删除模型: {alias}\n", color="\033[31m")
            if confirm(f"确定要删除 \"{alias}\" 吗？(同时清理凭据)", default_no=True):
                cfg = models[alias]
                cred = cfg.get("credential", {})
                if cred:
                    try:
                        cred_delete(cred)
                        _print_color(f"  ✔ 凭据已清理\n", color="\033[32m")
                    except Exception as e:
                        _print_color(f"  ⚠ 凭据清理失败: {e}\n", color="\033[33m")
                del models[alias]
                save_custom_models(models)
                if current == alias:
                    current = None
                _print_color(f"✔ 已删除: {alias}\n", color="\033[32m")
            else:
                print("已取消删除")
            _press_enter()
            continue

        # ---- 查看凭据状态 ----
        elif choice == "查看凭据状态":
            print(f"\n{'别名':<20} {'modelName':<25} {'凭据后端':<22} {'状态':<10} sk")
            print("-" * 95)
            for alias, cfg in models.items():
                mn = cfg.get("modelName", alias)
                cred = cfg.get("credential", {})
                backend = cred.get("type", "无")
                sk = _get_sk(alias, cfg)
                status = "\033[32m可用\033[0m" if sk else "\033[31m不可用\033[0m"
                sk_preview = (sk[:8] + "...") if sk else "-"
                print(f"{alias:<20} {mn:<25} {backend:<22} {status:<10} {sk_preview}")
            print()
            if confirm("重新设置某个模型的 API Key？"):
                sel = select_from_list(names, title="选择模型")
                if sel is not None:
                    alias = names[sel]
                    new_sk = input_with_prompt(f"新的 API Key ({alias}): ")
                    cfg = models[alias]
                    cred = cfg.get("credential", cred_default_config(alias))
                    cred_store(cred, new_sk)
                    cfg["credential"] = cred
                    models[alias] = cfg
                    save_custom_models(models)
                    _print_color(f"✔ {alias} 凭据已更新\n", color="\033[32m")
            _press_enter()
            continue

        elif choice == "退出":
            break

    print("已退出。")

# ============================================================
# CLI 入口
# ============================================================

if __name__ == "__main__":
    if len(sys.argv) >= 2:
        cmd = sys.argv[1]
        if cmd == "--env":
            cmd_env(sys.argv[2:])
        elif cmd == "--get-sk":
            cmd_get_sk(sys.argv[2:])
        elif cmd == "--reveal":
            cmd_reveal()
        elif cmd == "--migrate-import":
            cmd_migrate_import()
        elif cmd == "--help":
            print("用法: python claude-code-model-switcher.py [选项]")
            print()
            print("选项:")
            print("  --env [模型名]        输出 export 命令（eval 用）")
            print("  --get-sk [模型名]     输出原始 sk（供 apiKeyHelper 调用）")
            print("  --reveal              展示所有模型凭据状态")
            print("  --migrate-import      从 stdin JSON 批量导入 sk")
            print("  (无参数)              交互式菜单")
        else:
            _print_color(f"未知选项: {cmd}\n", color="\033[31m")
            print("用法: python claude-code-model-switcher.py [--env|--get-sk|--reveal|--migrate-import|--help]")
            sys.exit(1)
        sys.exit(0)

    try:
        main()
    except KeyboardInterrupt:
        print("\n已退出。")
        sys.exit(0)
