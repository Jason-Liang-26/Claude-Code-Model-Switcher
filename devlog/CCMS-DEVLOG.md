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

## Phase 8: 全局配置目录安全检测 (v0.8)

**目标**：防止在用户家目录下运行时，模型切换无声改写全局配置

**实现**：
- `is_global_config_dir()`：通过 `realpath + normcase` 精确判断 CWD 是否等于 `~`
- 主菜单顶部显示红色加粗警告：`当前处于全局配置目录，切换模型会影响全局配置`
- "切换模型" 和 "添加模型 → 立即切换" 流程在执行写入前增加二次确认（默认 No）
- CLI 模式（`--env` / `--get-sk`）无变更，避免破坏自动化调用

---

## Phase 9: 测试基础设施 (v0.9)

**目标**：零第三方依赖的自动化测试

**实现**：
- `tests/` 目录 + `unittest`（标准库），`tempfile` + `unittest.mock` 隔离文件 I/O
- 通过 `importlib.util.spec_from_file_location` 动态加载带连字符的主脚本
- 30 个测试覆盖：纯函数解析、resolve 查找、平台检测、文件读写 roundtrip、迁移逻辑、冲突检测
- 运行：`python -m unittest discover tests/`

**附带修复**：`load_custom_models()` 文件未关闭 ResourceWarning

---

## Phase 10: helper 脚本跨目录修复 (v0.10)

**问题**：`_generate_helper_scripts()` 用 `os.getcwd() + "claude-code-model-switcher.py"` 定位主脚本。安装后主脚本在 `~/.local/lib/` 下，helper 脚本生成在项目目录，跨目录时找不到主脚本。

**修复**：用 `os.path.abspath(__file__)` 替代 `os.getcwd()` 定位主脚本，helper 脚本中新增 `$script` / `$SCRIPT` 变量指向主脚本实际路径，`$root` / `$SELF_DIR` 仍用于读取 settings.json。

---

## Phase 11: Linux 初步适配 (v0.11)

**目标**：补齐缺失的 Linux 安装脚本和依赖检查

**实现**：
- `install.sh`：安装到 `~/.local/bin` + `~/.local/lib/`，前置检测 python3 + secret-tool
- 按发行版给出 libsecret-tools 安装命令（apt/dnf/pacman）
- `get_env_info_lines()` 中 Linux 无凭据后端时显示安装提示
- `_getch()` / `select_from_list()` / `_press_enter()` EOF 健壮化：空字符串 → Ctrl+C

---

## Phase 12: Linux 终端输出修复 (v0.12)

**问题**：Linux 下菜单显示"混乱"——每行输出不回车行首，文字偏移。

**根因**：`tty.setraw()` 禁用了 `OPOST`（输出处理），`\n` 不自动转换为 `\r\n`。光标下移后停在上一行末列，后续输出从该列开始。

**修复**：手动配置 termios，只关闭 `ECHO | ICANON | IEXTEN | ISIG`，保留 `c_oflag` 输出处理。

---

## Phase 13: Linux keyring 适配 (v0.13) — 进行中

**目标**：解决 headless Linux 上 GNOME Keyring 的 locked collection 问题

**问题链**：
1. `secret-tool store` → "Cannot create an item in a locked collection"
2. `gnome-keyring-daemon --unlock` 静默等待 stdin，无提示符
3. D-Bus session bus 和守护进程在 SSH 登录后不自动启动
4. 删除 `login.keyring` 会破坏用户其他应用的凭据

**实现**：
- `_is_secret_service_locked()`：主动检测 keyring 锁定状态
- `_unlock_linux_keyring()`：CCMS 自己的交互式解锁（查找控制套接字 → 提示密码 → 传密码给 `--unlock` → 实测验证）
- `_ensure_ccms_collection()`：通过 busctl/gdbus 创建 CCMS 专属 collection（`claude-code-models`），与用户 `login.keyring` 隔离
- `_try_start_keyring_daemon()`：检测并自动启动守护进程
- 菜单环境信息显示锁定状态 + 解锁提示
- D-Bus 超时、守护进程缺失等场景友好提示

**待解决**：busctl/gdbus 不可用且守护进程未运行时的终极回退方案（文件加密存储）

---

## Phase 14: Review 清理与文档同步 (v0.14)

**目标**：修复整体 review 发现的文档与代码脱节、孤儿 env var、死代码，补齐核心函数单测

**发现问题**：
- `CLAUDE.md` 仍描述 v1 架构（`custom-models.json`、`migrate_models()`、`ANTHROPIC_MODEL`、~1000 行），实际已是 v2 的 Endpoint→Model→Routing 三层（`ccms-endpoints.json`、4 路角色路由、~2300 行）
- `--get-sk` 的 `--help` 文本暗示可传模型名参数，但 `cmd_get_sk()` 已不读 args（从 `ccms_settings.local.json` 读 endpoint）
- v1 遗留的 `CCMS_MODEL_ALIAS` env var（Phase 5 写入）在 v2 中不再写入，但仍残留在 `settings.local.json` 且未被清理
- `print_env_export` 函数无调用方（`cmd_env` 自行 print），属于死代码
- `_get_sk(model_name, model_config)` 的 `model_name` 参数未使用
- `_migrate_old_v2` 和 `check_ccms_consistency` 两个核心函数零测试覆盖

**修复**：
- 重写 `CLAUDE.md` Architecture 小节：v2 数据模型表（5 层）、角色路由（`_ROLE_ENV_MAP`）、迁移函数（`_import_legacy` / `_migrate_old_v2`）、apiKeyHelper 流程
- `--get-sk` 帮助文本：删除 `[模型名]`，改为"输出当前项目的 sk"
- `_CCMS_MANAGED_ENV_KEYS` 追加 `CCMS_MODEL_ALIAS`，`write_model_to_project` 新增 `env.pop("CCMS_MODEL_ALIAS", None)` 惰性清理
- 删除 `print_env_export` 死代码，`_get_sk` 签名简化为 `_get_sk(model_config)`
- 新增 `TestMigrateOldV2`（3 个用例）和 `TestCheckCcmsConsistency`（5 个用例）共 8 个测试
- 删除误入仓库的 `bash.exe.stackdump`，`.gitignore` 追加 `*.stackdump`
- 更新 `CCMS-SPEC.md`：环境变量表后加 `CCMS_MODEL_ALIAS` 不再写入的说明

**测试结果**：85 tests, OK

**deferred**（行为变更范围，不在本次清理中）：
- `_infer_endpoint_name` 的 ccTLD 完整处理（需 publicsuffix 表）
- age/linux-file keyname 路径净化（防 `a/b` 创建子目录）
- 删除活跃 endpoint 后陈旧快照刷新

---

## Phase 15: 配置诊断功能 (v0.15)

**目标**：新增 `--diagnose` CLI 模式 + 菜单"配置诊断"，检查 `~/.claude/settings.json` 是否符合最佳实践

**背景**：Claude Code 默认在系统提示开头插入归属块（客户端版本 + prompt fingerprint）。当通过 LLM 网关路由时，该指纹每次请求不同，导致 prompt cache 无法命中，产生大量 cache missing 费用。设置 `env.CLAUDE_CODE_ATTRIBUTION_HEADER=0` 可禁用此行为。

**实现**：
- `save_user_settings()`：新增写入 `~/.claude/settings.json` 的函数（之前只有 `load_user_settings`）
- `_check_attribution_header(user_settings)`：检查 `CLAUDE_CODE_ATTRIBUTATION_HEADER` 是否为 `"0"`
- `_DIAGNOSTIC_RULES` 列表：规则注册表，未来扩展只需 append 新 dict
- `cmd_diagnose()`：遍历规则，输出状态，有 warn 时交互式询问是否修复
- CLI 入口 `--diagnose` 分支
- 交互菜单 `_COMMON` 新增"配置诊断"选项（有 endpoint 和无 endpoint 两种状态）

**测试**：
- `TestCheckAttributionHeader`：5 个用例（ok string/int、warn missing/wrong/no-env）
- `TestCmdDiagnose`：3 个用例（all-ok、warn+fix、warn+skip）
- 合计 93 tests

**调试记录**：测试中发现 env var 拼写问题——`CLAUDE_CODE_ATTRIBUTION_HEADER`（正确，30 字符）vs `CLAUDE_CODE_ATTRIBUTATION_HEADER`（错误，32 字符，多了一个 `AT`）。通过字节级比较定位：key 第 20 字节 `0x49`(I) vs `0x41`(A)，差 2 个字符。

---

## 产物清单

| 文件 | 说明 |
|------|------|
| `claude-code-model-switcher.py` | 主脚本 |
| `claude-code-model-switcher.cmd` | 项目本地启动器 (Windows) |
| `install.cmd` | 安装器 (CMD) |
| `install.ps1` | 安装器 (PowerShell) |
| `install.sh` | 安装器 (Linux/macOS) |
| `CCMS-SPEC.md` | 规格文档 |
| `CLAUDE.md` | Claude Code 指引 |
| `devlog/CCMS-DEVLOG.md` | 本文档 |
| `claude-code-model-switcher-help.md` | 用户手册 |
| `tests/test_ccms.py` | 单元测试 (93 cases, stdlib) |
