# CCMS 开发日志

> 会话：dev-claude-code-model-switcher
> 日期：2026-05-04 ～ 2026-05-05
> 环境：Windows 11 + PowerShell / WSL (Ubuntu Python 3.8 + Windows Python 3.13)

---

## Phase 1: 基础切换器 (v0.1)

**目标**：命令行交互式菜单，管理 custom-models.json，切换模型写入 settings.json

- 创建 `claude-code-model-switcher.py`
- 实现 `select_from_list()` 交互式菜单（↑↓ 选择，Enter 确认，ESC 退出）
- 实现 `_getch()` 跨平台单字符读取
- 菜单功能：切换模型、添加模型、删除模型
- 数据源：`~/.claude/custom-models.json`
- 输出：`.claude/settings.json > env`
- 修复：`custom-models.json` 尾随逗号 → JSON 解析容错

**问题**：终端箭头键不生效

**根因**：Windows 下 `msvcrt` 返回 `\xe0 + H/P`，不是 Unix 的 `\x1b[A` 序列

**修复**：`select_from_list` 同时处理 `\xe0`（msvcrt）和 `\x1b[`（VT）两种箭头键编码；`_setup_console()` 通过 `ctypes` + Win32 API 关闭行缓冲/回显；`input()` 在原始模式下不工作 → 替换为 `_press_enter()` 轮询 `_getch()`

---

## Phase 2: 凭据安全 (v0.2)

**目标**：sk 不存明文，迁入 OS 凭据管理器

**决策**：方案 A（credential 字段携带凭据后端信息），支持 wincred / macos-keychain / secret-service

**实现**：
- 凭据后端层：`cred_store()` / `cred_retrieve()` / `cred_delete()`
- Windows: `advapi32.CredWriteW` / `CredReadW` / `CredDeleteW` (ctypes, 零依赖)
- macOS: `security add-generic-password` / `find-generic-password -w` / `delete-generic-password`
- Linux: `secret-tool store` / `lookup` / `clear`
- 配置文件从 `sk` → `credential: {type, ...}` 自动迁移
- `write_model_to_project()` 只写 `ANTHROPIC_BASE_URL` + `ANTHROPIC_MODEL`，不含 sk

**问题**：`type("_CRED", ...)` → SyntaxWarning + c_char_p vs c_void_p 类型不兼容

**修复**：定义模块级 `_CREDENTIALW` 类，`_wincred_store` 中用 `ctypes.create_string_buffer` + `ctypes.cast`

---

## Phase 3: apiKeyHelper 机制 (v0.3)

**目标**：生成 helper 脚本，Claude Code 自动从凭据管理器取 sk

**实现**：
- `_generate_helper_scripts()` 生成 `.sh` + `.ps1` 两份 helper
- `write_model_to_project()` 写入 `apiKeyHelper` 到 settings.json
- 交互式菜单顶部显示 apiKeyHelper 状态
- Helper 脚本统一委托给 `python claude-code-model-switcher.py --get-sk`

**问题**：`get-sk.sh` 在 WSL 下路径解析失败 (`/c/Users/` vs `/mnt/c/Users/`)、Python 路径不符

**修复**：
- 路径从运行时解析 → 生成时嵌入绝对路径
- Windows 路径转 WSL 格式（`C:\...` → `/mnt/c/...`）
- `SELF_DIR` 不再依赖 `cd $(dirname $0)/..`

**问题**：WSL 下 `command -v` 不认识 Windows 路径，fallback 到 WSL Python 3.8 → 读不到凭据管理器

**关键发现**：Windows Python 在 WSL 下可用（`/mnt/c/.../python.exe`），拥有 Windows 用户令牌，能访问 advapi32；WSL Python 不能

**修复**：generate 时 `sys.executable` 转为 `/mnt/c/...` WSL 格式，确保 fallback 链优先 Windows Python

**问题**：Windows 上 `.sh` 无法直接执行，需 PowerShell 脚本

**修复**：生成 `get-sk.ps1`，`apiKeyHelper` 写为 `powershell -NoProfile -Command .claude\get-sk.ps1`

**问题**：PowerShell 脚本中 `$root` 路径含 `\U` → Python `-c` 参数被当成 Unicode 转义

**修复**：`$root` 改用正斜杠 `C:/Users/...`，Python 在 Windows 上接受正斜杠路径

**问题**：PowerShell 脚本无参数时读不到模型名

**修复**：同上

---

## Phase 4: 数据模型升级 (v0.4)

**目标**：外层 key 改为别名，增加 modelName 字段

**实现**：
- `custom-models.json` key = 别名（菜单显示），`modelName` = 真实模型 ID（写入 `ANTHROPIC_MODEL`）
- `resolve_model()` 支持 alias / modelName 双向查找
- `migrate_models()` 自动补 `modelName` 字段
- `--env` / `--get-sk` / `--reveal` 支持 alias 或 modelName 查询

---

## Phase 5: 托管标记与 CWD 路径 (v0.5)

**目标**：`CCMS_MODEL_ALIAS` 托管标记 + CWD 驱动路径

**实现**：
- `write_model_to_project()` 写入 `env.CCMS_MODEL_ALIAS`
- `get_current_alias()` 优先读此字段，无则反查 + 标记 "未托管"
- 交互菜单显示三种状态：托管(★)、未托管(⚠)、未配置(⚠)
- 所有项目级路径从 `os.path.abspath(__file__)` → `os.getcwd()`
- `env` 合并写入（不覆盖用户其他变量）

---

## Phase 6: 安装器与跨平台兼容 (v0.6)

**目标**：一键安装、全平台可访问

**实现**：
- `install.cmd` / `install.ps1`：复制文件到 `~/.local/bin` + `~/.local/lib/claude-code-model-switcher/`
- `.cmd` 启动器：`call python "%~dp0..\\lib\\...\\claude-code-model-switcher.py" %*`
- 自动检测 PATH 并提示

**问题**：WSL Python 3.8.10 → `list[str]` TypeError + f-string `\` SyntaxError

**修复**：
- `from __future__ import annotations`（3.8+ 兼容）
- f-string 中 `\033` 提到变量外

---

## Phase 7: UI 优化 (v0.7)

**目标**：菜单信息分层、颜色标注

**实现**：
- 菜单顶部按分区展示：系统信息 / 当前项目 / 全局配置
- 颜色标注：绿色=正常、黄色=警告、红色=冲突/错误
- 标签明确标注项目级 vs 全局配置
- 显示当前工作目录

---

## 产物清单

| 文件 | 说明 |
|------|------|
| `claude-code-model-switcher.py` | 主脚本 (~900行) |
| `claude-code-model-switcher.cmd` | 项目本地启动器 |
| `install.cmd` | 安装器 (CMD) |
| `install.ps1` | 安装器 (PowerShell) |
| `CCMS-SPEC.md` | 规格文档 |
| `CCMS-DEVLOG.md` | 本文档 |
| `claude-code-model-switcher-help.md` | 用户手册 |
