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


# v2 数据存储（endpoint → model → routing 三层模型）
MODELS_PATH = os.path.expanduser("~/.claude/ccms-endpoints.json")
LEGACY_MODELS_PATH = os.path.expanduser("~/.claude/custom-models.json")
# 项目级路径基于当前工作目录（CWD），确保从任意位置运行都找到当前项目的 .claude/
def _project_path(subpath: str) -> str:
    return os.path.join(os.getcwd(), ".claude", subpath)

PROJECT_SETTINGS_PATH = _project_path("settings.json")
LOCAL_SETTINGS_PATH = _project_path("settings.local.json")
CCMS_SETTINGS_PATH = _project_path("ccms_settings.local.json")
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
        import atexit
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        atexit.register(lambda: termios.tcsetattr(fd, termios.TCSADRAIN, old))
        new = termios.tcgetattr(fd)
        # 关闭回显、规范模式（行缓冲）、扩展模式、信号
        # 保留 c_oflag（输出处理），避免 \n 失去回车行首效果
        new[3] = new[3] & ~(termios.ECHO | termios.ICANON | termios.IEXTEN | termios.ISIG)
        termios.tcsetattr(fd, termios.TCSADRAIN, new)
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
        ch = sys.stdin.read(1)
        # EOF (empty string) → treat as Ctrl+C to avoid infinite loops
        return ch if ch else "\x03"
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
        elif ch == "\x03" or not ch:
            _clear_lines(total_display)
            raise KeyboardInterrupt
        else:
            continue
        _clear_lines(total_display)
        render()


def select_from_tabs(tabs: list[tuple[str, list[str]]],
                     common_items: list[str] | None = None,
                     prompt: str = "← → 切换  ↑↓ 选择  Enter 确认  ESC 退出",
                     initial_tab: int = 0) -> str | None:
    """Tab 式菜单。← → 切换 tab，↑↓ 在 tab 内选择。

    tabs: [(标签名, [菜单项...]), ...] — 顺序即布局顺序
    common_items: 所有 tab 共享的底部项
    initial_tab: 初始选中的 tab 索引
    返回 (选中的菜单项字符串, 当前tab索引)，ESC 返回 None"""
    if not tabs:
        return None
    common = common_items or []
    tab_idx = initial_tab % len(tabs)
    item_idx = 0
    n_tabs = len(tabs)

    def _all_items():
        """当前 tab 的 items + common_items（统一选择范围）"""
        return tabs[tab_idx][1] + common

    def _total_lines():
        # tab header (1) + blank (1) + all items + prompt (1)
        return 3 + len(_all_items())

    last_lines = [0]

    def render():
        if last_lines[0] > 0:
            _clear_lines(last_lines[0])
        # Tab headers — 紧凑格式，避免换行
        parts = []
        for ti, (label, _) in enumerate(tabs):
            marker = "\033[1m" if ti == tab_idx else "\033[2m"
            parts.append(f"{marker}[{label}]\033[0m")
        sys.stdout.write("  " + "  ".join(parts) + "\n")
        print()
        # Current tab items + common items（统一索引）
        items = _all_items()
        for i, item in enumerate(items):
            prefix = "\033[7m > \033[0m" if i == item_idx else "   "
            print(f"{prefix}{item}")
        _print_color(f"{prompt}\n", dim=True)
        last_lines[0] = _total_lines()

    render()
    while True:
        ch = _getch()
        if ch == "\xe0":
            ch2 = _getch()
            if ch2 == "H":   # ↑
                item_idx = (item_idx - 1) % max(len(_all_items()), 1)
            elif ch2 == "P": # ↓
                item_idx = (item_idx + 1) % max(len(_all_items()), 1)
            elif ch2 == "K": # ←
                tab_idx = (tab_idx - 1) % n_tabs
                item_idx = 0
            elif ch2 == "M": # →
                tab_idx = (tab_idx + 1) % n_tabs
                item_idx = 0
            else:
                continue
        elif ch == "\x1b":
            ch2 = _getch()
            if ch2 == "[":
                ch3 = _getch()
                if ch3 == "A":   # ↑
                    item_idx = (item_idx - 1) % max(len(_all_items()), 1)
                elif ch3 == "B": # ↓
                    item_idx = (item_idx + 1) % max(len(_all_items()), 1)
                elif ch3 == "C": # →
                    tab_idx = (tab_idx + 1) % n_tabs
                    item_idx = 0
                elif ch3 == "D": # ←
                    tab_idx = (tab_idx - 1) % n_tabs
                    item_idx = 0
                else:
                    continue
            elif ch2 == "\x1b":
                _clear_lines(last_lines[0])
                return None
            else:
                continue
        elif ch in ("\r", "\n"):
            _clear_lines(last_lines[0])
            return _all_items()[item_idx], tab_idx
        elif ch == "\x03" or not ch:
            _clear_lines(last_lines[0])
            raise KeyboardInterrupt
        else:
            continue
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

def input_with_prompt(prompt_text: str, allow_empty: bool = False) -> str:
    """带光标移动的行输入。支持 ← → Home End Delete，ESC 取消。"""
    val = ""
    pos = 0  # 光标在 val 中的位置

    def _redraw():
        """重绘：回到行首写 prompt+val，清除残余，用 SGR 定位光标到 pos"""
        sys.stdout.write(f"\r{prompt_text}{val}\033[K")
        # 用保存/恢复光标定位，避免列号计算在多字节字符下的偏差
        n = len(val) - pos
        if n > 0:
            sys.stdout.write(f"\033[{n}D")  # 左移 n 列
        sys.stdout.flush()

    sys.stdout.write(prompt_text)
    sys.stdout.flush()

    while True:
        ch = _getch()
        if ch in ("\r", "\n"):
            sys.stdout.write("\n")
            if val or allow_empty:
                return val
            _print_color("（不能为空，请重新输入）\n", dim=True)
            sys.stdout.write(prompt_text)
            sys.stdout.flush()
            val, pos = "", 0
        elif ch in ("\x7f", "\x08"):
            if pos > 0:
                val = val[:pos - 1] + val[pos:]
                pos -= 1
                _redraw()
        elif ch == "\x03":
            raise KeyboardInterrupt
        elif ch == "\xe0":
            ch2 = _getch()
            if ch2 == "K" and pos > 0:          # ←
                pos -= 1
                _redraw()
            elif ch2 == "M" and pos < len(val):  # →
                pos += 1
                _redraw()
            elif ch2 == "S" and pos < len(val):  # Delete
                val = val[:pos] + val[pos + 1:]
                _redraw()
            elif ch2 == "G":                     # Home
                pos = 0
                _redraw()
            elif ch2 == "O":                     # End
                pos = len(val)
                _redraw()
        elif ch == "\x1b":
            ch2 = _getch()
            if ch2 == "[":
                ch3 = _getch()
                if ch3 == "D" and pos > 0:          # ←
                    pos -= 1
                    _redraw()
                elif ch3 == "C" and pos < len(val):  # →
                    pos += 1
                    _redraw()
                elif ch3 == "H":                     # Home
                    pos = 0
                    _redraw()
                elif ch3 == "F":                     # End
                    pos = len(val)
                    _redraw()
                elif ch3 == "3":
                    ch4 = _getch()                  # \x1b[3~ → Delete
                    if ch4 == "~" and pos < len(val):
                        val = val[:pos] + val[pos + 1:]
                        _redraw()
                else:
                    _getch()
            else:
                sys.stdout.write("\n")
                return ""
        elif ch and 0x20 <= ord(ch) <= 0x7e:
            val = val[:pos] + ch + val[pos:]
            pos += 1
            _redraw()


def _press_enter(prompt_text: str = "按 Enter 返回菜单..."):
    """等待 Enter 按键（兼容原始模式）。ESC 返回 None，Ctrl+C 抛出异常。"""
    _print_color(f"{prompt_text}\n", dim=True)
    while True:
        ch = _getch()
        if ch in ("\r", "\n"):
            return
        elif ch == "\x1b" or not ch:
            return
        elif ch == "\x03":
            raise KeyboardInterrupt


# ============================================================
# 凭据后端 (Credential Backend)
# ============================================================

CRED_WINCRED = "wincred"
CRED_MACOS_KEYCHAIN = "macos-keychain"
CRED_SECRET_SERVICE = "secret-service"
CRED_AGE = "age"
CRED_LINUX_FILE = "linux-file"

CCMS_CRED_DIR = os.path.expanduser("~/.local/share/ccms")
CCMS_AGE_IDENTITY_DEFAULT = os.path.join(CCMS_CRED_DIR, "identity.age")
CCMS_FILE_KEY_DEFAULT = os.path.join(CCMS_CRED_DIR, "ccms.key")

def _detect_platform() -> str:
    """返回当前 OS 标识: windows / macos / linux"""
    s = platform.system().lower()
    if s == "windows": return "windows"
    if s == "darwin": return "macos"
    return "linux"

def _is_gui_session() -> bool:
    """检测是否有 GUI 会话（X11 或 Wayland）。"""
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

def cred_available_backends() -> list[str]:
    """返回当前 OS 可用的凭据后端列表"""
    plat = _detect_platform()
    if plat == "windows": return [CRED_WINCRED]
    if plat == "macos":   return [CRED_MACOS_KEYCHAIN]
    # Linux
    backends = []
    try:
        subprocess.run(["secret-tool", "--version"],
                       capture_output=True, timeout=3)
        backends.append(CRED_SECRET_SERVICE)
    except Exception:
        pass
    if shutil.which("age"):
        backends.append(CRED_AGE)
    backends.append(CRED_LINUX_FILE)  # openssl 几乎总是可用
    return backends

def cred_default_config(model_name: str) -> dict:
    """为当前 OS 生成默认的 credential 配置"""
    plat = _detect_platform()
    if plat == "windows":
        return {"type": CRED_WINCRED, "target": f"claude/{model_name}"}
    elif plat == "macos":
        return {"type": CRED_MACOS_KEYCHAIN, "service": "claude",
                "account": model_name}
    # Linux
    # GUI 会话 + secret-tool 可用 → secret-service
    if _is_gui_session():
        try:
            subprocess.run(["secret-tool", "--version"],
                           capture_output=True, timeout=3)
            return {"type": CRED_SECRET_SERVICE,
                    "label": f"claude/{model_name}",
                    "key": f"claude-{model_name}"}
        except Exception:
            pass
    # age 可用 → age
    if shutil.which("age"):
        identity = _age_resolve_identity(autocreate=True)
        if identity:
            return {"type": CRED_AGE, "identity": identity,
                    "keyname": model_name}
    # fallback → linux-file (openssl)
    identity = _linux_file_resolve_identity(autocreate=True)
    return {"type": CRED_LINUX_FILE, "identity": identity,
            "keyname": model_name}

def cred_store(cred: dict, sk: str):
    """将 sk 存入凭据后端"""
    t = cred.get("type", "")
    if t == CRED_WINCRED:
        _wincred_store(cred["target"], sk)
    elif t == CRED_MACOS_KEYCHAIN:
        _macos_keychain_store(cred["service"], cred["account"], sk)
    elif t == CRED_SECRET_SERVICE:
        _secret_service_store(cred.get("key"), cred.get("label", ""), sk)
    elif t == CRED_AGE:
        _age_store(cred["identity"], cred["keyname"], sk)
    elif t == CRED_LINUX_FILE:
        _linux_file_store(cred["identity"], cred["keyname"], sk)
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
        elif t == CRED_AGE:
            return _age_retrieve(cred["identity"], cred["keyname"])
        elif t == CRED_LINUX_FILE:
            return _linux_file_retrieve(cred["identity"], cred["keyname"])
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
    elif t == CRED_AGE:
        _age_delete(cred["keyname"])
    elif t == CRED_LINUX_FILE:
        _linux_file_delete(cred["keyname"])

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

def _try_start_keyring_daemon() -> bool:
    """如果守护进程没运行，尝试启动它。返回是否成功。"""
    try:
        r = subprocess.run(["pidof", "gnome-keyring-daemon"],
                           capture_output=True, text=True, timeout=3)
        if r.returncode == 0 and r.stdout.strip():
            return True
    except Exception:
        pass
    try:
        r = subprocess.run(
            ["gnome-keyring-daemon", "--start",
             "--components=secrets", "--daemonize"],
            capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def _secret_service_store(key: str | None, label: str, sk: str):
    if not key:
        key = label
    _try_start_keyring_daemon()
    r = subprocess.run(["secret-tool", "store",
                        "--label", label, "key", key],
                       input=sk, text=True, capture_output=True)
    if r.returncode != 0:
        err = r.stderr.strip() if r.stderr else "unknown error"
        raise RuntimeError(f"secret-tool store failed: {err}")

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
                   capture_output=True, text=True)


# ---- Linux age 后端 ----

def _age_resolve_identity(*, autocreate: bool = False) -> str | None:
    """返回 age 身份文件路径。autocreate=True 时自动生成。
    优先级: $CCMS_AGE_IDENTITY → ~/.config/ccms/age-identity → 自动生成"""
    # 1. 环境变量
    env_id = os.environ.get("CCMS_AGE_IDENTITY")
    if env_id:
        p = os.path.expanduser(env_id)
        if os.path.isfile(p):
            return os.path.realpath(p)
    # 2. 配置文件
    cfg = os.path.expanduser("~/.config/ccms/age-identity")
    if os.path.isfile(cfg):
        with open(cfg) as f:
            path = f.read().strip()
        if path:
            p = os.path.expanduser(path)
            if os.path.isfile(p):
                return os.path.realpath(p)
    # 3. 默认路径
    default = os.path.realpath(os.path.expanduser(CCMS_AGE_IDENTITY_DEFAULT))
    if os.path.isfile(default):
        return default
    # 4. 自动生成
    if autocreate:
        os.makedirs(os.path.dirname(default), exist_ok=True)
        r = subprocess.run(["age-keygen", "-o", default],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            os.chmod(default, 0o600)
            return default
    return None


def _age_store(identity: str, keyname: str, sk: str):
    """用 age 加密 sk 并存储到文件。"""
    cred_dir = os.path.join(CCMS_CRED_DIR, "creds")
    os.makedirs(cred_dir, exist_ok=True)
    cred_path = os.path.join(cred_dir, f"{keyname}.age")

    # 从身份文件提取公钥
    r = subprocess.run(["age-keygen", "-y", identity],
                       capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError(f"age-keygen 失败: {r.stderr.strip()}")
    pubkey = r.stdout.strip()

    # 加密（二进制输出，不能用 text=True）
    r = subprocess.run(["age", "-e", "-r", pubkey],
                       input=sk.encode(), capture_output=True, timeout=10)
    if r.returncode != 0:
        err = r.stderr.decode().strip() if r.stderr else "unknown error"
        raise RuntimeError(f"age 加密失败: {err}")

    with open(cred_path, "wb") as f:
        f.write(r.stdout)

def _age_retrieve(identity: str, keyname: str) -> str | None:
    """解密 age 加密文件并返回 sk。"""
    cred_path = os.path.join(CCMS_CRED_DIR, "creds", f"{keyname}.age")
    if not os.path.isfile(cred_path):
        return None
    with open(cred_path, "rb") as f:
        encrypted = f.read()
    r = subprocess.run(["age", "-d", "-i", identity],
                       input=encrypted, capture_output=True, timeout=10)
    if r.returncode != 0:
        return None
    return r.stdout.decode().strip()

def _age_delete(keyname: str):
    cred_path = os.path.join(CCMS_CRED_DIR, "creds", f"{keyname}.age")
    if os.path.isfile(cred_path):
        os.remove(cred_path)


# ---- Linux 文件加密后端 (openssl fallback) ----

def _linux_file_resolve_identity(*, autocreate: bool = False) -> str | None:
    """返回 openssl 密钥文件路径。autocreate=True 时自动生成随机密钥。"""
    default = os.path.realpath(os.path.expanduser(CCMS_FILE_KEY_DEFAULT))
    if os.path.isfile(default):
        return default
    if autocreate:
        os.makedirs(os.path.dirname(default), exist_ok=True)
        r = subprocess.run(["openssl", "rand", "-hex", "32"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            with open(default, "w") as f:
                f.write(r.stdout.strip())
            os.chmod(default, 0o600)
            return default
    return None

def _linux_file_store(identity: str, keyname: str, sk: str):
    """用 openssl aes-256-cbc 加密 sk 并存储。"""
    cred_dir = os.path.join(CCMS_CRED_DIR, "creds")
    os.makedirs(cred_dir, exist_ok=True)
    cred_path = os.path.join(cred_dir, f"{keyname}.enc")
    r = subprocess.run(
        ["openssl", "enc", "-aes-256-cbc", "-pbkdf2",
         "-pass", f"file:{identity}"],
        input=sk.encode(), capture_output=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError(f"openssl 加密失败: {r.stderr.decode(errors='replace').strip()}")
    with open(cred_path, "wb") as f:
        f.write(r.stdout)

def _linux_file_retrieve(identity: str, keyname: str) -> str | None:
    """解密 openssl 加密文件并返回 sk。"""
    cred_path = os.path.join(CCMS_CRED_DIR, "creds", f"{keyname}.enc")
    if not os.path.isfile(cred_path):
        return None
    with open(cred_path, "rb") as f:
        encrypted = f.read()
    r = subprocess.run(
        ["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2",
         "-pass", f"file:{identity}"],
        input=encrypted, capture_output=True, timeout=10)
    if r.returncode != 0:
        return None
    return r.stdout.decode().strip()

def _linux_file_delete(keyname: str):
    cred_path = os.path.join(CCMS_CRED_DIR, "creds", f"{keyname}.enc")
    if os.path.isfile(cred_path):
        os.remove(cred_path)

# ============================================================
# 模型数据读写
# ============================================================

def _json_strip_trailing_commas(text: str) -> str:
    text = re.sub(r',\s*}', '}', text)
    text = re.sub(r',\s*]', ']', text)
    return text

def load_custom_models() -> dict:
    """加载 v2 模型数据。首次运行时从旧版 custom-models.json 导入。"""
    if os.path.isfile(MODELS_PATH):
        with open(MODELS_PATH, "r", encoding="utf-8") as f:
            raw = f.read()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = json.loads(_json_strip_trailing_commas(raw))
        # 旧版 v2 格式迁移（顶层 models → endpoint 内）
        if "models" in data:
            data = _migrate_old_v2(data)
            save_custom_models(data)
        return data
    # 首次运行：从旧版 custom-models.json 导入（或直接使用已迁移的 v2 数据）
    data = {"endpoints": {}}
    if os.path.isfile(LEGACY_MODELS_PATH):
        with open(LEGACY_MODELS_PATH, "r", encoding="utf-8") as f:
            raw = f.read()
        try:
            legacy = json.loads(raw)
        except json.JSONDecodeError:
            legacy = json.loads(_json_strip_trailing_commas(raw))
        if legacy:
            if isinstance(legacy, dict) and "endpoints" in legacy \
                    and "models" not in legacy:
                data = legacy  # 已是新版 v2 格式
            elif isinstance(legacy, dict) and "endpoints" in legacy and "models" in legacy:
                # 旧版 v2 格式：model 在顶层，需迁移到 endpoint 内
                data = _migrate_old_v2(legacy)
            else:
                data = _import_legacy(legacy)  # v1 格式
            save_custom_models(data)
    return data


def _migrate_old_v2(data: dict) -> dict:
    """旧 v2 格式 → 新 v2 格式：顶层 models 迁移到 endpoint 内，删除旧 routing。

    旧: {endpoints: {ep: {url, credential}}, models: {alias: {...}}, routing}
    新: {endpoints: {ep: {url, credential, models: {alias: {modelName}}, defaultRouting}}}
    """
    for alias, m in data.get("models", {}).items():
        ep_name = m.get("endpoint", "")
        if ep_name in data.get("endpoints", {}):
            data["endpoints"][ep_name].setdefault("models", {})[alias] = {
                "modelName": m.get("modelName", alias)
            }
    data.pop("models", None)
    data.pop("routing", None)
    data.pop("_version", None)
    # 为每个 endpoint 设置 defaultRouting
    for ep_name, ep in data.get("endpoints", {}).items():
        if not ep.get("defaultRouting"):
            ep_aliases = list(ep.get("models", {}).keys())
            if ep_aliases:
                first = ep_aliases[0]
                ep["defaultRouting"] = {r: first for r, _ in _ROLE_LABELS}
    return data


def _import_legacy(legacy: dict) -> dict:
    """从 v1 扁平格式 {alias: {url, modelName, credential/sk}} 导入为 v2。"""
    endpoints = {}
    url_to_ep = {}
    ep_counter = 0

    for alias, cfg in sorted(legacy.items()):
        if not isinstance(cfg, dict):
            continue
        url = cfg.get("url", "")
        cred = cfg.get("credential", {})
        mn = cfg.get("modelName", alias)

        if "sk" in cfg and not cred:
            sk = cfg.pop("sk")
            if sk:
                cred = cred_default_config(alias)
                cred_store(cred, sk)

        cred_key = cred.get("keyname") or cred.get("target") or cred.get("account", "")
        ep_key = (url, cred_key)
        if ep_key not in url_to_ep:
            ep_counter += 1
            ep_name = _infer_endpoint_name(url)
            if ep_name in endpoints:
                ep_name = f"{ep_name}-{ep_counter}"
            url_to_ep[ep_key] = ep_name
            endpoints[ep_name] = {"url": url, "credential": cred, "models": {}}
        ep_name = url_to_ep[ep_key]
        endpoints[ep_name]["models"][alias] = {"modelName": mn}

    # 为每个 endpoint 设置 defaultRouting
    for ep_name, ep in endpoints.items():
        ep_aliases = list(ep.get("models", {}).keys())
        if ep_aliases:
            first = ep_aliases[0]
            ep["defaultRouting"] = {r: first for r, _ in _ROLE_LABELS}

    return {"endpoints": endpoints}

def save_custom_models(models: dict):
    os.makedirs(os.path.dirname(MODELS_PATH), exist_ok=True)
    with open(MODELS_PATH, "w", encoding="utf-8") as f:
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

def load_local_settings() -> dict:
    """读取 .claude/settings.local.json"""
    if not os.path.isfile(LOCAL_SETTINGS_PATH):
        return {}
    try:
        with open(LOCAL_SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

def save_local_settings(settings: dict):
    """写入 .claude/settings.local.json"""
    os.makedirs(os.path.dirname(LOCAL_SETTINGS_PATH), exist_ok=True)
    with open(LOCAL_SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
        f.write("\n")

def load_ccms_settings() -> dict:
    """读取 .claude/ccms_settings.local.json（项目级 CCMS 快照）"""
    if not os.path.isfile(CCMS_SETTINGS_PATH):
        return {}
    try:
        with open(CCMS_SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

def save_ccms_settings(data: dict):
    """写入 .claude/ccms_settings.local.json"""
    os.makedirs(os.path.dirname(CCMS_SETTINGS_PATH), exist_ok=True)
    with open(CCMS_SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

def load_merged_ccms_settings() -> dict:
    """合并 project + local settings，local 覆盖 project。

    对 env 子 dict 做深度合并，保留两边的非 CCMS 环境变量。
    旧用户升级后 local 无文件时回退到 project settings。"""
    project = load_project_settings()
    local = load_local_settings()
    merged = dict(project)
    merged.update(local)
    project_env = project.get("env")
    local_env = local.get("env")
    if project_env is not None or local_env is not None:
        merged_env = dict(project_env or {})
        merged_env.update(local_env or {})
        merged["env"] = merged_env
    return merged

_CCMS_MANAGED_ENV_KEYS = (
    "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL",  # 旧字段，迁移时清理
    "CLAUDE_CODE_SUBAGENT_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL", "CCMS_ENDPOINT",
)


def _migrate_ccms_fields_from_project():
    """从 .claude/settings.json 移除 CCMS 托管字段。

    首次写入 local 后调用，一次性清理。静默处理所有错误。"""
    if not os.path.isfile(PROJECT_SETTINGS_PATH):
        return
    try:
        with open(PROJECT_SETTINGS_PATH, "r", encoding="utf-8") as f:
            settings = json.load(f)
    except (json.JSONDecodeError, OSError):
        return
    env = settings.get("env", {})
    changed = False
    for key in _CCMS_MANAGED_ENV_KEYS:
        if key in env:
            del env[key]
            changed = True
    if "apiKeyHelper" in settings:
        del settings["apiKeyHelper"]
        changed = True
    if not changed:
        return
    if not env and "env" in settings:
        del settings["env"]
    if not settings:
        os.remove(PROJECT_SETTINGS_PATH)
    else:
        with open(PROJECT_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
            f.write("\n")

def _model_aliases(v2_data: dict) -> list[str]:
    """所有模型别名"""
    result = []
    for ep in v2_data.get("endpoints", {}).values():
        result.extend(ep.get("models", {}).keys())
    return result


def _iter_models(v2_data: dict):
    """遍历 (alias, flat_config)"""
    for ep_name, ep in v2_data.get("endpoints", {}).items():
        for alias, m in ep.get("models", {}).items():
            yield alias, _model_flat_config(v2_data, alias)


def _find_model(v2_data: dict, alias: str):
    """返回 (ep_name, model_dict) 或 (None, None)"""
    for ep_name, ep in v2_data.get("endpoints", {}).items():
        if alias in ep.get("models", {}):
            return ep_name, ep["models"][alias]
    return None, None


def resolve_model(target: str, models: dict) -> tuple[str, dict] | None:
    """通过别名或 modelName 查找模型，返回 (alias, config)"""
    for alias, cfg in _iter_models(models):
        if alias == target or cfg["modelName"] == target:
            return alias, cfg
    return None


def _infer_endpoint_name(url: str) -> str:
    """从 URL hostname 推断 endpoint 名称（取倒数第二段，跳过 TLD）"""
    import re
    m = re.search(r'//([^/:]+)', url)
    if m:
        host = m.group(1)
        parts = host.split('.')
        # IP 地址 → 返回完整 host
        if all(p.isdigit() for p in parts):
            return host
        if len(parts) >= 2:
            return parts[-2]   # e.g. deepseek / kimi / siliconflow
        return host
    return "default"


def _model_flat_config(v2_data: dict, alias: str) -> dict:
    """合成扁平配置 {url, modelName, credential, endpoint}"""
    ep_name, m = _find_model(v2_data, alias)
    ep = v2_data.get("endpoints", {}).get(ep_name, {}) if ep_name else {}
    return {
        "url": ep.get("url", ""),
        "modelName": (m or {}).get("modelName", alias),
        "credential": ep.get("credential", {}),
        "endpoint": ep_name or "",
    }


def _upsert_model(v2_data: dict, alias: str, url: str,
                     model_name: str, credential: dict,
                     ep_name: str = "") -> str:
    """在 endpoint 下新增/更新模型。返回 endpoint 名称"""
    if not ep_name:
        ep_name = _infer_endpoint_name(url)
    endpoints = v2_data.setdefault("endpoints", {})
    if ep_name in endpoints and endpoints[ep_name].get("url") != url:
        i = 2
        while f"{ep_name}-{i}" in endpoints:
            i += 1
        ep_name = f"{ep_name}-{i}"
    if ep_name not in endpoints:
        endpoints[ep_name] = {"url": url, "credential": credential, "models": {}}
    endpoints[ep_name].setdefault("models", {})[alias] = {"modelName": model_name}
    # 首个模型自动设置 defaultRouting
    ep = endpoints[ep_name]
    if not ep.get("defaultRouting"):
        ep["defaultRouting"] = {r: alias for r, _ in _ROLE_LABELS}
    return ep_name


def _delete_model(v2_data: dict, alias: str) -> bool:
    """从 endpoint 下删除模型"""
    for ep in v2_data.get("endpoints", {}).values():
        if alias in ep.get("models", {}):
            del ep["models"][alias]
            # 清理 endpoint 的 defaultRouting 中引用该别名的项
            dr = ep.get("defaultRouting", {})
            for role, a in list(dr.items()):
                if a == alias:
                    if ep.get("models"):
                        dr[role] = list(ep["models"].keys())[0]
                    else:
                        del dr[role]
            return True
    return False


def get_current_alias(models: dict) -> str | None:
    """返回当前项目设置的模型别名（从 ccms_settings.local.json 读取）。"""
    ccms = load_ccms_settings()
    if not ccms:
        return None
    routing = ccms.get("routing", {})
    sonnet = routing.get("sonnet", {})
    if isinstance(sonnet, dict):
        return sonnet.get("alias")
    return sonnet or list(routing.values())[0] if routing else None

# ============================================================
# 模型配置兼容: 旧版 sk 字段 → 迁移到凭据后端
# ============================================================




# ============================================================
# 写项目配置 + helper 脚本
# ============================================================

_ROLE_ENV_MAP = [
    ("opus",     "ANTHROPIC_DEFAULT_OPUS_MODEL"),
    ("sonnet",   "ANTHROPIC_DEFAULT_SONNET_MODEL"),
    ("haiku",    "ANTHROPIC_DEFAULT_HAIKU_MODEL"),
    ("subagent", "CLAUDE_CODE_SUBAGENT_MODEL"),
]


def write_model_to_project(alias: str, model_config: dict, v2_data: dict = None,
                           project_routing: dict = None):
    """写入项目 .claude/settings.local.json (env 不含 sk) 并生成 helper 脚本。

    路由来源: project_routing（项目级编辑）> endpoint defaultRouting > 当前模型。
    同时写入 ccms_settings.local.json 快照。"""
    settings = load_local_settings()
    if "env" not in settings:
        settings["env"] = {}
    env = settings["env"]

    # 路由: 项目级 > endpoint defaultRouting > 当前模型
    routing = None
    if project_routing is not None:
        routing = project_routing
    elif v2_data:
        ep_name = _find_model(v2_data, alias)[0]
        ep = v2_data.get("endpoints", {}).get(ep_name, {}) if ep_name else {}
        routing = ep.get("defaultRouting")

    if routing:
        for role, env_key in _ROLE_ENV_MAP:
            target_alias = routing.get(role)
            if target_alias and _find_model(v2_data, target_alias)[0]:
                target_cfg = _model_flat_config(v2_data, target_alias)
                env[env_key] = target_cfg.get("modelName", target_alias)
    else:
        # 无路由表时 4 路全部指向当前模型
        mn = model_config.get("modelName", alias)
        for _, env_key in _ROLE_ENV_MAP:
            env[env_key] = mn

    # 清除旧版 ANTHROPIC_MODEL（已由路由 env var 替代）
    env.pop("ANTHROPIC_MODEL", None)

    # 活跃 endpoint 标记
    env["ANTHROPIC_BASE_URL"] = model_config.get("url", "")
    if v2_data:
        env["CCMS_ENDPOINT"] = _active_endpoint(v2_data) or ""

    if _detect_platform() == "windows":
        settings["apiKeyHelper"] = "powershell -NoProfile -Command .claude\\get-sk.ps1"
    else:
        settings["apiKeyHelper"] = ".claude/get-sk.sh"
    save_local_settings(settings)
    _generate_helper_scripts()
    _migrate_ccms_fields_from_project()

    # 写入 ccms_settings.local.json 快照
    if v2_data and (routing or project_routing is not None):
        ep_name = _find_model(v2_data, alias)[0]
        snapshot = {"endpoint": ep_name or "", "routing": {}}
        for role, _ in _ROLE_ENV_MAP:
            target_alias = routing.get(role)
            if target_alias:
                target_cfg = _model_flat_config(v2_data, target_alias)
                snapshot["routing"][role] = {
                    "alias": target_alias,
                    "modelName": target_cfg.get("modelName", target_alias)
                }
        save_ccms_settings(snapshot)

def _generate_helper_scripts():
    """生成 helper 脚本（.sh + .ps1），委托给 Python 取凭据"""
    helper_dir = os.path.dirname(HELPER_SCRIPT_PATH)
    os.makedirs(helper_dir, exist_ok=True)

    root = os.getcwd()
    py = sys.executable
    # 主脚本实际路径（安装后可能在 ~/.local/lib/，不一定是 CWD）
    script_path = os.path.abspath(__file__)

    # ── PowerShell 版 (get-sk.ps1) ──
    # 用正斜杠避免 Python -c 参数里的反斜杠被当成转义符
    root_ps1 = root.replace("\\", "/")
    script_ps1 = script_path.replace("\\", "/")
    ps1 = textwrap.dedent(f"""\
    # Claude Code apiKeyHelper — 由 claude-code-model-switcher.py 自动维护
    $script = "{script_ps1}"
    $py = "{py}"
    $sk = & $py "$script" --get-sk 2>$null
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
    script_nix = script_path.replace("\\", "/")
    if len(script_nix) > 1 and script_nix[1] == ":":
        drive = script_nix[0].lower()
        script_nix = f"/mnt/{drive}{script_nix[2:]}"

    sh = textwrap.dedent(f"""\
    #!/bin/bash
    # Claude Code apiKeyHelper — 由 claude-code-model-switcher.py 自动维护
    set -euo pipefail
    SCRIPT="{script_nix}"
    _PY=""
    for _p in "{py_nix}" python3 python; do
        if command -v "$_p" >/dev/null 2>&1; then
            _PY="$_p"
            break
        fi
    done
    [ -z "$_PY" ] && exit 1
    exec "$_PY" "$SCRIPT" --get-sk
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
    """--get-sk 模式: 从 ccms_settings.local.json 查 endpoint → credential → 输出 sk"""
    ccms = load_ccms_settings()
    ep_name = ccms.get("endpoint", "") if ccms else ""
    if not ep_name:
        print("错误: ccms_settings.local.json 中未配置 endpoint", file=sys.stderr)
        sys.exit(1)
    models = load_custom_models()
    ep = models.get("endpoints", {}).get(ep_name, {})
    cred = ep.get("credential", {})
    if not cred:
        print(f"错误: endpoint \"{ep_name}\" 无凭据配置", file=sys.stderr)
        sys.exit(1)
    sk = cred_retrieve(cred)
    if sk:
        sys.stdout.write(sk)
    else:
        print("错误: 凭据不可用", file=sys.stderr)
        sys.exit(1)

def cmd_reveal():
    """--reveal 模式: 展示所有模型凭据状态 + 路由配置（用于迁移）"""
    models = load_custom_models()

    aliases = _model_aliases(models)
    if not aliases:
        _print_color("没有配置任何模型\n", color="\033[33m")
        return
    print(f"平台: {_detect_platform()}")
    print(f"可用凭据后端: {', '.join(cred_available_backends())}")
    print()
    print(f"{'别名':<20} {'modelName':<25} {'endpoint':<15} {'凭据后端':<20} {'状态':<10} sk")
    print("-" * 105)
    for alias, cfg in _iter_models(models):
        mn = cfg.get("modelName", alias)
        cred = cfg.get("credential", {})
        backend = cred.get("type", "无")
        sk = _get_sk(alias, cfg)
        status = "\033[32m可用\033[0m" if sk else "\033[31m不可用\033[0m"
        sk_preview = (sk[:8] + "...") if sk else "-"
        ep = cfg.get("endpoint", "")
        print(f"{alias:<20} {mn:<25} {ep:<15} {backend:<20} {status:<10} {sk_preview}")
    # 各 endpoint 的默认路由
    print(f"\n{'':-^105}")
    _print_color("Endpoint 默认路由\n", bold=True)
    for ep_name, ep in models.get("endpoints", {}).items():
        dr = ep.get("defaultRouting", {})
        if dr:
            print(f"  [{ep_name}]")
            for role in ("opus", "sonnet", "haiku", "subagent"):
                alias = dr.get(role, "—")
                cfg = _model_flat_config(models, alias) if alias != "—" else {}
                mn = cfg.get("modelName", alias)
                print(f"    {role:<10} → {alias:<20} ({mn})")
        else:
            print(f"  [{ep_name}] （无默认路由）")
    print()
    if confirm("导出全部 sk 到 stdout（JSON 格式，用于迁移）？", default_no=True):
        out = {}
        for alias, cfg in _iter_models(models):
            sk = _get_sk(alias, cfg)
            out[alias] = {"url": cfg.get("url", ""), "credential": cfg.get("credential", {}),
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
        url = entry.get("url", "")
        cred = cred_default_config(alias)
        cred_store(cred, entry["sk"])
        ep_name, existing = _find_model(models, alias)
        if existing:
            if ep_name in models.get("endpoints", {}):
                models["endpoints"][ep_name]["credential"] = cred
            existing["modelName"] = mn
            imported += 1
            print(f"  ✔ {alias} → {mn}")
        else:
            _upsert_model(models, alias, url, mn, cred)
            imported += 1
            print(f"  ✔ {alias} → {mn} (新建)")
    save_custom_models(models)
    _print_color(f"成功导入 {imported} 个模型到 {_detect_platform()} 凭据后端\n", color="\033[32m")

# ============================================================
# 冲突检测
# ============================================================

def detect_env_api_key_conflict():
    """检查三层 settings 中是否有 env 直接配置了 ANTHROPIC_API_KEY"""
    conflicts = []
    # 本地级（最高优先级）
    ls = load_local_settings()
    if ls.get("env", {}).get("ANTHROPIC_API_KEY"):
        conflicts.append(("本地 (local)", str(LOCAL_SETTINGS_PATH)))
    # 项目级
    ps = load_project_settings()
    if ps.get("env", {}).get("ANTHROPIC_API_KEY"):
        conflicts.append(("项目 (project)", str(PROJECT_SETTINGS_PATH)))
    # 用户级
    us = load_user_settings()
    if us.get("env", {}).get("ANTHROPIC_API_KEY"):
        conflicts.append(("用户 (user)", os.path.expanduser("~/.claude/settings.json")))
    return conflicts


def check_ccms_consistency() -> list[tuple[str, str, str]]:
    """比对 ccms_settings.local.json 快照与 settings.local.json 的一致性。

    返回 [(字段名, ccms值, settings值), ...] 差异列表。空列表表示一致。"""
    ccms = load_ccms_settings()
    if not ccms:
        return []
    local = load_local_settings()
    local_env = local.get("env", {})

    diffs = []
    # 比对基础字段
    ccms_ep = ccms.get("endpoint", "")
    if ccms_ep and ccms_ep != local_env.get("CCMS_ENDPOINT", ""):
        diffs.append(("CCMS_ENDPOINT", ccms_ep, local_env.get("CCMS_ENDPOINT", "")))

    # 比对 4 路路由
    ccms_routing = ccms.get("routing", {})
    for role, env_key in _ROLE_ENV_MAP:
        ccms_entry = ccms_routing.get(role, {})
        ccms_mn = ccms_entry.get("modelName", "") if isinstance(ccms_entry, dict) else ""
        local_mn = local_env.get(env_key, "")
        if ccms_mn and ccms_mn != local_mn:
            diffs.append((env_key, ccms_mn, local_mn))

    return diffs


def _prompt_ccms_sync(diffs: list[tuple[str, str, str]]):
    """显示一致性差异并询问用户处理方式。"""
    if not diffs:
        return
    _print_color("\n⚠  项目 CCMS 配置与 settings.local.json 不一致\n", color="\033[33m")
    print(f"  {'字段':<35} {'ccms_settings':<25} {'settings.local':<25}")
    print(f"  {'-'*85}")
    for field, ccms_val, local_val in diffs:
        print(f"  {field:<35} {ccms_val:<25} {local_val:<25}")
    print()

    options = ["以 ccms_settings 为准，覆盖 settings.local",
               "以 settings.local 为准，更新 ccms_settings",
               "忽略"]
    sel = select_from_list(options, title="选择同步方向")
    if sel == 0:
        # ccms → settings.local
        local = load_local_settings()
        local_env = local.setdefault("env", {})
        ccms = load_ccms_settings()
        ccms_routing = ccms.get("routing", {})
        for field, ccms_val, _ in diffs:
            local_env[field] = ccms_val
        # 也同步路由 env var
        for role, env_key in _ROLE_ENV_MAP:
            entry = ccms_routing.get(role, {})
            if isinstance(entry, dict) and entry.get("modelName"):
                local_env[env_key] = entry["modelName"]
        save_local_settings(local)
        _print_color("✔ 已用 ccms_settings 覆盖 settings.local.json\n", color="\033[32m")
    elif sel == 1:
        # settings.local → ccms
        local = load_local_settings()
        local_env = local.get("env", {})
        ccms = load_ccms_settings()
        ccms["endpoint"] = local_env.get("CCMS_ENDPOINT", ccms.get("endpoint", ""))
        ccms_routing = ccms.setdefault("routing", {})
        for role, env_key in _ROLE_ENV_MAP:
            mn = local_env.get(env_key, "")
            if mn:
                if role not in ccms_routing or not isinstance(ccms_routing[role], dict):
                    ccms_routing[role] = {}
                ccms_routing[role]["modelName"] = mn
        save_ccms_settings(ccms)
        _print_color("✔ 已用 settings.local 更新 ccms_settings\n", color="\033[32m")


def _is_secret_service_locked() -> bool:
    """检测 secret-service 后端是否被锁定。"""
    try:
        r = subprocess.run(
            ["secret-tool", "lookup", "key", "ccms-health-check"],
            capture_output=True, text=True, timeout=3)
        if r.returncode != 0:
            err = (r.stderr or "").lower()
            return "locked" in err
        return False
    except subprocess.TimeoutExpired:
        return True
    except Exception:
        return False


def _unlock_linux_keyring() -> bool:
    """在 Linux 上交互式解锁 keyring collection。

    找到 gnome-keyring 控制套接字，用 CCMS 自己的提示符获取密码，
    通过 subprocess 传给 gnome-keyring-daemon --unlock。"""
    if _detect_platform() != "linux":
        return False

    # 1. 找到控制套接字
    control = os.environ.get("GNOME_KEYRING_CONTROL", "")
    if not control:
        try:
            default_path = f"/run/user/{os.getuid()}/keyring/control"
            if os.path.exists(default_path):
                control = default_path
        except Exception:
            pass

    if not control:
        # 尝试通过 gnome-keyring-daemon --start 获取
        try:
            r = subprocess.run(
                ["gnome-keyring-daemon", "--start",
                 "--components=secrets"],
                capture_output=True, text=True, timeout=10)
            for line in (r.stdout + "\n" + r.stderr).split("\n"):
                if "GNOME_KEYRING_CONTROL=" in line:
                    control = line.split("=", 1)[1].strip()
        except Exception:
            pass

    if not control or not os.path.exists(control):
        _print_color("✘ 无法连接 gnome-keyring 守护进程\n",
                     color="\033[31m")
        return False

    # 2. 获取密码并解锁
    _print_color("\n解锁 Keyring\n", bold=True)
    _print_color("（没有设置过密码则直接回车）\n", dim=True)
    password = input_with_prompt("Keyring 密码: ")

    try:
        r = subprocess.run(
            ["gnome-keyring-daemon", "--unlock"],
            input=password + "\n",
            text=True,
            capture_output=True,
            timeout=15,
            env={**os.environ, "GNOME_KEYRING_CONTROL": control})
    except subprocess.TimeoutExpired:
        _print_color("✘ 解锁超时\n", color="\033[31m")
        return False

    if r.returncode == 0:
        # 退出码成功不意味着真的解锁了，必须实测
        if not _is_secret_service_locked():
            _print_color("✔ Keyring 已解锁\n", color="\033[32m")
            return True
        _print_color("✘ 解锁命令执行成功，但 keyring 仍处于锁定状态\n",
                     color="\033[33m")
        return False

    err = (r.stderr or "").strip()
    if "invalid" in err.lower():
        _print_color("✘ 密码错误\n", color="\033[31m")
    else:
        _print_color(f"✘ 解锁失败: {err or '未知错误'}\n",
                     color="\033[31m")
    return False


def is_global_config_dir() -> bool:
    """检查 CWD 是否为用户家目录。
    若是，项目级 .claude/settings.json 与用户级 ~/.claude/settings.json 是同一文件，
    切换模型会修改全局配置。"""
    try:
        cwd = os.path.normcase(os.path.realpath(os.getcwd()))
        home = os.path.normcase(os.path.realpath(os.path.expanduser("~")))
        return cwd == home
    except Exception:
        return False


def _backup_user_settings_json_once():
    """备份 ~/.claude/settings.json（仅当存在且非空）。

    仅在全局配置目录下调用。仅备份一次（检测 .bak 文件存在）。
    确保用户的现有配置不会因 CCMS 写入而丢失。"""
    path = os.path.expanduser("~/.claude/settings.json")
    if not os.path.isfile(path):
        return False
    bak = path + ".bak"
    if os.path.isfile(bak):
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        if not content.strip():
            return False
        with open(bak, "w", encoding="utf-8") as f:
            f.write(content)
        _print_color(f"✔ 已备份现有配置: {bak}\n", color="\033[32m")
        return True
    except OSError:
        return False


def _check_high_priority_env_vars() -> list[tuple[str, str]]:
    """检查各层 settings 中是否有 ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN。

    这些环境变量的优先级高于 apiKeyHelper，会导致 helper 脚本不生效。
    返回 [(文件名, 变量名), ...] 列表。"""
    warnings = []
    for load_fn, label in [
        (load_local_settings, "settings.local.json"),
        (load_project_settings, "settings.json"),
        (load_user_settings, "~/.claude/settings.json"),
    ]:
        try:
            s = load_fn()
            env = s.get("env", {})
            for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
                if key in env:
                    warnings.append((label, key))
        except Exception:
            pass
    return warnings


def _get_global_gitignore_path():
    """获取全局 gitignore 文件路径（从 core.excludesfile 读取）。"""
    try:
        r = subprocess.run(["git", "config", "--global", "core.excludesfile"],
                           capture_output=True, text=True, timeout=3)
        if r.returncode == 0 and r.stdout.strip():
            path = r.stdout.strip()
            return os.path.expanduser(path)
    except Exception:
        pass
    return None


def _check_global_gitignore_rules():
    """检查全局 gitignore 是否已包含 CCMS 忽略规则。

    返回 (path, missing_patterns):
    - path: 全局 gitignore 文件路径（未配置则为 None）
    - missing_patterns: 缺失的规则列表
    """
    path = _get_global_gitignore_path()
    required = [".claude/*local.json", ".claude/get-sk*"]
    if not path:
        return None, required
    if not os.path.isfile(path):
        return path, required
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    missing = [p for p in required if p not in content]
    return path, missing


def _ensure_global_gitignore():
    """检查并配置全局 gitignore，追加 CCMS 忽略规则。"""
    path, missing = _check_global_gitignore_rules()

    # 未配置 core.excludesfile → 设置为 ~/.gitignore_global
    if not path:
        default_path = os.path.expanduser("~/.gitignore_global")
        try:
            subprocess.run(["git", "config", "--global", "core.excludesfile", default_path],
                           capture_output=True, timeout=3, check=True)
            path = default_path
            _print_color(f"✔ 已设置 core.excludesfile → {default_path}\n", color="\033[32m")
        except Exception as e:
            _print_color(f"✘ 设置 core.excludesfile 失败: {e}\n", color="\033[31m")
            return False

    if not missing:
        _print_color("✔ 全局 gitignore 已包含所有 CCMS 忽略规则\n", color="\033[32m")
        return True

    # 追加缺失规则
    content = ""
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    if content and not content.endswith("\n"):
        content += "\n"
    for pattern in missing:
        content += pattern + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    _print_color(f"✔ 已将 CCMS 忽略规则追加到 {path}\n", color="\033[32m")
    return True

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
                      CRED_SECRET_SERVICE: "secret-service (libsecret)",
                      CRED_AGE: "age 加密",
                      CRED_LINUX_FILE: "linux-file (openssl)"}
    blabels = [backend_labels.get(b, b) for b in backends]
    backend_status = ", ".join(blabels) if blabels else "无"
    backend_color = "green" if blabels else "red"
    lines.append(("凭据后端", backend_status, backend_color))
    if plat == "linux" and not backends:
        lines.append(("", "无可用凭据后端，请安装 libsecret-tools 或 age", "red"))
    if CRED_AGE in backends and not _age_resolve_identity():
        lines.append(("", "age 身份文件未配置（首次添加模型时自动生成）", "yellow"))
    if CRED_SECRET_SERVICE in backends and _is_secret_service_locked():
        lines.append(("", "secret-service 被锁定（GUI 环境可解锁）", "yellow"))

    # ── 当前项目 (.claude/) ──
    lines.append(("项目路径", os.getcwd(), ""))
    lines.append(("配置文件", "settings.local.json + ccms_settings.local.json + settings.json", ""))

    # ccms_settings 一致性
    ccms_diffs = check_ccms_consistency()
    if ccms_diffs:
        lines.append(("⚠ 配置不一致", f"ccms_settings 与 settings.local 有 {len(ccms_diffs)} 处差异", "yellow"))

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

    # ── 项目 settings.json 与 local 冲突检测 ──
    ps = load_project_settings()
    p_env = ps.get("env", {})
    for key, label in [("ANTHROPIC_MODEL", "ANTHROPIC_MODEL"),
                        ("ANTHROPIC_BASE_URL", "ANTHROPIC_BASE_URL")]:
        if key in p_env:
            lines.append(("⚠ 冲突",
                          f"settings.json 中配置了 {label}: \"{p_env[key]}\"，settings.local.json 的值会覆盖它",
                          "yellow"))

    # ── 全局 (~/.claude/) ──
    models = load_custom_models()
    names = _model_aliases(models)
    if names:
        lines.append(("全局模型", f"~/.claude/ccms-endpoints.json — {len(names)} 个模型", ""))
    else:
        lines.append(("全局模型", "~/.claude/ccms-endpoints.json — 未配置", "yellow"))

    return lines

# ============================================================
# 路由管理
# ============================================================

def _endpoint_menu(v2_data: dict) -> str:
    """Endpoint 管理子菜单：创建 / 删除。返回最后的状态消息。"""
    _last_msg = ""
    while True:
        print("\033[2J\033[H", end="")
        width = shutil.get_terminal_size().columns
        _print_color(f"{' 管理 Endpoints ':=^{width}}\n", bold=True)

        endpoints = v2_data.get("endpoints", {})
        if endpoints:
            for ep_name, ep_cfg in endpoints.items():
                url = ep_cfg.get("url", "")
                model_list = list(ep_cfg.get("models", {}).keys())
                cred = ep_cfg.get("credential", {})
                sk_ok = bool(cred and cred_retrieve(cred))
                status = "\033[32m✓\033[0m" if sk_ok else "\033[31m✗\033[0m"
                _print_color(f"  {status} {ep_name}  ", bold=True)
                _print_color(f"{url}", dim=True)
                _print_color(f"  ({len(model_list)} 个模型)\n", dim=True)
            print()
        else:
            _print_color("  暂无 Endpoint\n\n", dim=True)

        menu_items = ["创建 Endpoint"]
        if endpoints:
            menu_items.append("重命名 Endpoint")
            menu_items.append("删除 Endpoint")
        menu_items.append("返回主菜单")

        idx = select_from_list(menu_items, prompt="↑↓ 选择 Enter 确认  ESC 返回")
        if idx is None or idx == len(menu_items) - 1:
            return _last_msg

        if idx == 0:  # 创建
            print("\n\033[1m创建 Endpoint\033[0m")
            url = input_with_prompt("API URL: ")
            if not url: continue
            default_name = _infer_endpoint_name(url)
            ep_name = input_with_prompt(f"名称 [{default_name}]: ")
            if not ep_name:
                ep_name = default_name
            if ep_name in endpoints:
                i = 2
                while f"{ep_name}-{i}" in endpoints:
                    i += 1
                _print_color(f"  名称已存在，自动改为: {ep_name}-{i}\n", color="\033[33m")
                ep_name = f"{ep_name}-{i}"
            sk = input_with_prompt("API Key (sk-...): ")
            if not sk: continue
            cred = cred_default_config(ep_name)
            cred_store(cred, sk)
            endpoints[ep_name] = {"url": url, "credential": cred, "models": {}}
            save_custom_models(v2_data)
            _last_msg = f"✔ Endpoint \"{ep_name}\" 已创建\n"

        elif idx == 1 and endpoints:  # 重命名
            ep_names = list(endpoints.keys())
            sel = select_from_list(ep_names, title="选择要重命名的 Endpoint")
            if sel is not None:
                old_name = ep_names[sel]
                new_name = input_with_prompt(f"新名称 [{old_name}]: ")
                if new_name and new_name != old_name:
                    if new_name in endpoints:
                        _print_color("名称已存在\n", color="\033[31m")
                    else:
                        endpoints[new_name] = endpoints.pop(old_name)
                        # 如果重命名的是活跃 endpoint，更新 CCMS_ENDPOINT
                        if _active_endpoint(v2_data) is None:  # 旧名已不在
                            s = load_local_settings()
                            if s.get("env", {}).get("CCMS_ENDPOINT") == old_name:
                                s["env"]["CCMS_ENDPOINT"] = new_name
                                save_local_settings(s)
                        save_custom_models(v2_data)
                        _last_msg = f"✔ {old_name} → {new_name}\n"

        elif idx == 2 and endpoints:  # 删除
            ep_names = list(endpoints.keys())
            sel = select_from_list(ep_names, title="选择要删除的 Endpoint（其下模型将同时删除）")
            if sel is not None:
                ep_name = ep_names[sel]
                affected = list(endpoints[ep_name].get("models", {}).keys())
                _print_color(f"\n⚠  将删除 {ep_name} 及其 {len(affected)} 个模型\n", color="\033[31m")
                if confirm(f"确定删除？", default_no=True):
                    cred = endpoints[ep_name].get("credential", {})
                    if cred:
                        try: cred_delete(cred)
                        except Exception: pass
                    for alias in affected:
                        _delete_model(v2_data, alias)
                    del endpoints[ep_name]
                    save_custom_models(v2_data)
                    _last_msg = "✔ 已删除\n"
                    aliases = _model_aliases(v2_data)
                    if aliases:
                        write_model_to_project(aliases[0],
                                               _model_flat_config(v2_data, aliases[0]), v2_data)


# ============================================================
# 主菜单
# ============================================================

def _active_endpoint(models: dict) -> str | None:
    """返回当前活跃的 endpoint 名。优先读 CCMS_ENDPOINT，fallback ccms_settings / URL 匹配。"""
    s = load_merged_ccms_settings()
    ep_tag = s.get("env", {}).get("CCMS_ENDPOINT", "")
    if ep_tag and ep_tag in models.get("endpoints", {}):
        return ep_tag
    # fallback: ccms_settings.local.json
    ccms = load_ccms_settings()
    ccms_ep = ccms.get("endpoint", "") if ccms else ""
    if ccms_ep and ccms_ep in models.get("endpoints", {}):
        return ccms_ep
    # fallback: URL 匹配（旧项目无 CCMS_ENDPOINT）
    base_url = s.get("env", {}).get("ANTHROPIC_BASE_URL", "")
    if base_url:
        for ep_name, ep in models.get("endpoints", {}).items():
            if ep.get("url", "") == base_url:
                return ep_name
    eps = list(models.get("endpoints", {}).keys())
    return eps[0] if eps else None


_ROLE_LABELS = [("opus", "Opus"), ("sonnet", "Sonnet"),
                ("haiku", "Haiku"), ("subagent", "Subagent")]


def _routing_picker(v2_data: dict, active_ep: str, model_aliases: list[str],
                    scope: str = "project"):
    """交互式路由编辑：↑↓ 选角色，← → 切模型，Enter 确认。

    scope:
      "endpoint" — 编辑 endpoint 的 defaultRouting（全局 ccms-endpoints.json）
      "project"  — 编辑当前项目路由（settings.local.json + ccms_settings.local.json）"""
    if not model_aliases:
        _print_color("当前 endpoint 下无可用模型\n", color="\033[33m")
        _press_enter()
        return

    # 根据 scope 决定读取来源
    ep = v2_data.get("endpoints", {}).get(active_ep, {})
    if scope == "endpoint":
        routing = dict(ep.get("defaultRouting", {}))
        title_suffix = "endpoint 默认路由"
    else:
        # 从 ccms_settings.local.json 读取项目路由
        ccms_local = load_ccms_settings()
        project_raw = ccms_local.get("routing", {}) if ccms_local else {}
        routing = {}
        for role, entry in project_raw.items():
            routing[role] = entry.get("alias", entry) if isinstance(entry, dict) else entry
        title_suffix = "当前项目路由"

    # 每个角色当前选的 model index（-1 = 清空）
    options = model_aliases + ["（清空）"]
    selected_role = 0
    model_indices = []
    for role_key, _ in _ROLE_LABELS:
        cur = routing.get(role_key)
        model_indices.append(model_aliases.index(cur) if cur in model_aliases else len(options) - 1)

    n_roles = len(_ROLE_LABELS)
    total_lines = n_roles + 3  # title + prompt + roles + blank

    first = [True]

    def render():
        if first[0]:
            first[0] = False
        else:
            _clear_lines(total_lines)
        _print_color(f"编辑{title_suffix} ({active_ep} 下的模型)\n", bold=True)
        _print_color("↑↓ 选角色  ← → 切模型  Enter 确认  ESC 放弃\n", dim=True)
        for i, (role_key, role_label) in enumerate(_ROLE_LABELS):
            mi = model_indices[i]
            cur_model = options[mi]
            prefix = "\033[7m > \033[0m" if i == selected_role else "   "
            arrow = "  ◀ ▶" if i == selected_role else ""
            _print_color(f"{prefix}{role_label:<10} → {cur_model}{arrow}\n")
        print()

    render()
    while True:
        ch = _getch()
        if ch == "\xe0":
            ch2 = _getch()
            if ch2 == "H":  # ↑
                selected_role = (selected_role - 1) % n_roles
            elif ch2 == "P":  # ↓
                selected_role = (selected_role + 1) % n_roles
            elif ch2 == "K":  # ←
                model_indices[selected_role] = (model_indices[selected_role] - 1) % len(options)
            elif ch2 == "M":  # →
                model_indices[selected_role] = (model_indices[selected_role] + 1) % len(options)
            else:
                continue
        elif ch == "\x1b":
            ch2 = _getch()
            if ch2 == "[":
                ch3 = _getch()
                if ch3 == "A":  # ↑
                    selected_role = (selected_role - 1) % n_roles
                elif ch3 == "B":  # ↓
                    selected_role = (selected_role + 1) % n_roles
                elif ch3 == "C":  # →
                    model_indices[selected_role] = (model_indices[selected_role] + 1) % len(options)
                elif ch3 == "D":  # ←
                    model_indices[selected_role] = (model_indices[selected_role] - 1) % len(options)
                else:
                    continue
            elif ch2 == "\x1b":
                _clear_lines(total_lines)
                return
            else:
                continue
        elif ch in ("\r", "\n"):
            break
        elif ch == "\x03" or not ch:
            _clear_lines(total_lines)
            raise KeyboardInterrupt
        else:
            continue
        render()

    # 应用变更
    _clear_lines(total_lines)
    new_routing = {}
    for i, (role_key, _) in enumerate(_ROLE_LABELS):
        mi = model_indices[i]
        if mi != len(options) - 1:
            new_routing[role_key] = model_aliases[mi]

    if scope == "endpoint":
        # 写入 endpoint 的 defaultRouting（全局）
        if ep:
            ep["defaultRouting"] = new_routing
        save_custom_models(v2_data)
        return "✔ endpoint 默认路由已更新\n"
    else:
        # 写入项目级：settings.local.json + ccms_settings.local.json
        rep = new_routing.get("sonnet") or list(new_routing.values())[0] if new_routing else (
            model_aliases[0] if model_aliases else "")
        if rep:
            write_model_to_project(rep, _model_flat_config(v2_data, rep), v2_data,
                                   project_routing=new_routing)
        return "✔ 当前项目路由已更新\n"


def main():
    _setup_console()
    models = load_custom_models()


    # 启动时一致性检查
    diffs = check_ccms_consistency()
    if diffs:
        _prompt_ccms_sync(diffs)

    _status_msg = ""  # 操作成功/失败提示，下次渲染时显示一次后清除
    _last_tab = 0     # 记住上次选中的 tab

    while True:
        print("\033[2J\033[H", end="")
        if _status_msg:
            _print_color(_status_msg, color="\033[32m")
            _status_msg = ""
        width = shutil.get_terminal_size().columns
        _print_color(f"{' CCMS ':=^{width}}\n", bold=True)

        cwd = os.getcwd()
        _print_color(f"  📁 {cwd}", dim=True)
        if is_global_config_dir():
            _print_color("  ⚠ 全局配置模式", color="\033[33m")
        print()

        endpoints = models.get("endpoints", {})
        # 项目级路由优先，fallback 到全局路由
        ccms_local = load_ccms_settings()
        ccms_routing_raw = ccms_local.get("routing", {})
        # ccms_routing 格式: {role: {alias, modelName}}，提取 alias
        project_routing = {}
        for role, entry in ccms_routing_raw.items():
            if isinstance(entry, dict):
                project_routing[role] = entry.get("alias", "")
            else:
                project_routing[role] = entry
        routing = project_routing
        active_ep = _active_endpoint(models)

        # ── 活跃 Endpoint ──
        if active_ep and active_ep in endpoints:
            ep = endpoints[active_ep]
            cred = ep.get("credential", {})
            sk_ok = bool(cred and cred_retrieve(cred))
            _print_color(f"  ★ {active_ep}", bold=True)
            _print_color(f"  {ep.get('url', '')}", dim=True)
            _print_color(f"  凭据: ", dim=True)
            if sk_ok:
                _print_color("✓\n", color="\033[32m")
            else:
                _print_color("✗\n", color="\033[31m")
            print()

            # ── 模型列表 ──
            ep_models = ep.get("models", {})
            model_aliases = list(ep_models.keys())
            if model_aliases:
                _print_color("  模型\n", bold=True)
                for ma in model_aliases:
                    mn = ep_models[ma].get("modelName", ma)
                    _print_color(f"  {ma}  →  {mn}\n", dim=True)
            else:
                _print_color("  模型: （空）\n", dim=True)
            print()

            # ── 路由表 ──
            _print_color("  路由\n", bold=True)
            for role_key, role_label in _ROLE_LABELS:
                r_alias = routing.get(role_key, "—")
                in_ep = r_alias in model_aliases
                col = "" if in_ep else "\033[33m"
                _print_color(f"  {role_label:<10} → {r_alias}\n", color=col)
            print()
            _print_color("  " + "─" * 40 + "\n", dim=True)

            # ── 菜单 ──
            _TABS = [
                ("Endpoint 管理", [
                    "切换 Endpoint",
                    "管理 Endpoints",
                    "修改凭据",
                ]),
                ("路由管理", [
                    "编辑 endpoint 默认路由",
                    "编辑当前项目路由",
                ]),
                ("模型管理", [
                    "添加模型",
                    "删除模型",
                ]),
            ]
            _COMMON = ["查看所有凭据", "退出"]
            result = select_from_tabs(_TABS, _COMMON, initial_tab=_last_tab)
            if result is None:
                choice = None
            else:
                choice, _last_tab = result
        else:
            _print_color("  暂无可用 Endpoint\n", color="\033[33m")
            choice = select_from_list(["创建 Endpoint", "退出"], prompt="↑↓ 选择 Enter 确认  ESC 退出")
            if choice is not None:
                choice = ["创建 Endpoint", "退出"][choice]

        if choice is None:
            break
        ep_models = endpoints.get(active_ep, {}).get("models", {}) if active_ep else {}
        model_aliases = list(ep_models.keys())

        # ---- 切换 Endpoint ----
        if choice == "切换 Endpoint":
            ep_names = list(endpoints.keys())
            sel = select_from_list(ep_names, title="选择 Endpoint")
            if sel is not None:
                ep_name = ep_names[sel]
                ep_cfg = endpoints[ep_name]
                ep_models_sel = ep_cfg.get("models", {})
                aliases_sel = list(ep_models_sel.keys())
                # 使用 endpoint 的 defaultRouting 覆盖项目路由
                dr = ep_cfg.get("defaultRouting", {})
                if dr:
                    routing_to_apply = dict(dr)
                elif aliases_sel:
                    first = aliases_sel[0]
                    routing_to_apply = {r: first for r, _ in _ROLE_LABELS}
                else:
                    routing_to_apply = {}
                # 先写 CCMS_ENDPOINT 再调 write_model_to_project
                settings = load_local_settings()
                settings.setdefault("env", {})["CCMS_ENDPOINT"] = ep_name
                save_local_settings(settings)
                rep = routing_to_apply.get("sonnet") or (aliases_sel[0] if aliases_sel else "none")
                cfg = _model_flat_config(models, rep) if aliases_sel else {"url": ep_cfg.get("url", ""), "modelName": "", "credential": {}}
                write_model_to_project(rep, cfg, models)
                _status_msg = f"✔ 已切换至 {ep_name}，路由已应用\n"
                # 检查全局 gitignore 是否配置
                _, missing = _check_global_gitignore_rules()
                if missing:
                    ans = input_with_prompt("全局 gitignore 未配置 CCMS 忽略规则，是否现在配置？(Y/n) ")
                    if ans.strip().lower() != "n":
                        _ensure_global_gitignore()

        # ---- 添加模型 (在当前 endpoint 下) ----
        elif choice == "添加模型":
            print(f"\n\033[1m添加模型 → {active_ep}\033[0m")
            print(f"  URL: {endpoints[active_ep].get('url', '')}")
            print(f"  （凭据继承 endpoint，无需重新输入 Key）\n")
            alias = input_with_prompt("别名: ")
            if not alias: continue
            mn = input_with_prompt("模型名 (modelName): ")
            if not mn: continue
            ep = endpoints[active_ep]
            _upsert_model(models, alias, ep["url"], mn, ep.get("credential", {}), ep_name=active_ep)
            save_custom_models(models)
            _status_msg = f"✔ 已添加: {alias} → {mn}\n"

        # ---- 删除模型 ----
        elif choice == "删除模型":
            sel = select_from_list(model_aliases, title=f"删除 {active_ep} 下的模型")
            if sel is not None:
                alias = model_aliases[sel]
                if confirm(f"确定删除 \"{alias}\" 吗？", default_no=True):
                    _delete_model(models, alias)
                    save_custom_models(models)
                    _status_msg = "✔ 已删除\n"

        # ---- 编辑 endpoint 默认路由 ----
        elif choice == "编辑 endpoint 默认路由":
            msg = _routing_picker(models, active_ep, model_aliases, scope="endpoint")
            if msg: _status_msg = msg

        # ---- 编辑当前项目路由 ----
        elif choice == "编辑当前项目路由":
            msg = _routing_picker(models, active_ep, model_aliases, scope="project")
            if msg: _status_msg = msg
            # 重载（routing 可能已变更）
        # ---- 修改凭据 ----
        elif choice == "修改凭据":
            _print_color(f"\n修改 {active_ep} 的 API Key\n", bold=True)
            new_sk = input_with_prompt("新的 API Key (sk-...): ")
            if new_sk:
                cred = cred_default_config(active_ep)
                cred_store(cred, new_sk)
                endpoints[active_ep]["credential"] = cred
                save_custom_models(models)
                if model_aliases:
                    write_model_to_project(model_aliases[0],
                                           _model_flat_config(models, model_aliases[0]), models)
                _status_msg = "✔ 凭据已更新\n"

        # ---- 管理 Endpoints ----
        elif choice == "管理 Endpoints":
            msg = _endpoint_menu(models)
            if msg: _status_msg = msg

        # ---- 创建 Endpoint (无 endpoint 时的初始状态) ----
        elif choice == "创建 Endpoint":
            print("\n\033[1m创建 Endpoint\033[0m")
            url = input_with_prompt("API URL: ")
            if not url: continue
            default_name = _infer_endpoint_name(url)
            ep_name = input_with_prompt(f"名称 [{default_name}]: ")
            if not ep_name:
                ep_name = default_name
            if ep_name in endpoints:
                _status_msg = "\033[31m✗ 名称已存在\n\033[0m"
                continue
            sk = input_with_prompt("API Key (sk-...): ")
            if not sk: continue
            cred = cred_default_config(ep_name)
            cred_store(cred, sk)
            endpoints[ep_name] = {"url": url, "credential": cred, "models": {}}
            save_custom_models(models)
            _status_msg = f"✔ Endpoint \"{ep_name}\" 已创建\n"

        # ---- 查看所有凭据 ----
        elif choice == "查看所有凭据":
            print(f"\n{'别名':<20} {'modelName':<25} {'endpoint':<15} {'凭据后端':<22} {'状态':<10} sk")
            print("-" * 110)
            for alias, cfg in _iter_models(models):
                mn = cfg.get("modelName", alias)
                cred = cfg.get("credential", {})
                backend = cred.get("type", "无")
                sk = _get_sk(alias, cfg)
                status = "\033[32m可用\033[0m" if sk else "\033[31m不可用\033[0m"
                sk_preview = (sk[:8] + "...") if sk else "-"
                ep = cfg.get("endpoint", "")
                print(f"{alias:<20} {mn:<25} {ep:<15} {backend:<22} {status:<10} {sk_preview}")
            _press_enter()

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
