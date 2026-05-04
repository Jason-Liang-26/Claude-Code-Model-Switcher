# Claude 模型切换器 (CCMS)

管理自定义模型配置，sk 存入 OS 凭据管理器（不落盘），切换模型时自动更新项目配置并生成 apiKeyHelper 脚本。要求 Python 3.8+，零第三方依赖。

---

## 安装与环境配置

### 1. 安装到全局 PATH

```bash
# 创建 ~/.local/bin（如果不存在）
mkdir -p ~/.local/bin

# 复制启动器到全局 PATH 目录
cp claude-code-model-switcher.cmd ~/.local/bin/
```

`~/.local/bin` 需在系统 PATH 中（Git Bash 默认已包含，PowerShell 需手动添加）。

### 2. 验证

```bash
claude-code-model-switcher --help
```

### 3. 工作原理

脚本基于**当前工作目录 (CWD)** 寻找 `.claude/settings.json`——`cd` 到哪个项目，就操作哪个项目的配置。`~/.claude/custom-models.json`（模型仓库）为全局唯一。

---

## 交互式菜单

```bash
claude-code-model-switcher
# 或
claude-code-model-switcher
```

菜单顶部显示三组信息：

| 分区 | 内容 |
|------|------|
| **系统信息** | OS、架构、凭据后端 |
| **当前项目** | 项目路径、helper 脚本状态、配置冲突 |
| **全局配置** | custom-models.json 模型数量 |

| 功能 | 说明 |
|------|------|
| **切换模型** | 选择已配置的模型 → 写入 `.claude/settings.json` + 生成 helper 脚本 |
| **添加模型** | 输入别名、modelName、URL、API Key → sk 存入 OS 凭据管理器 |
| **删除模型** | 删除配置 + 清理 OS 凭据管理器 |
| **查看凭据状态** | 列出所有模型凭据是否可用，支持重设 Key |

操作：`↑↓` 选择，`Enter` 确认，`ESC` 返回/退出。

---

## CLI 模式

以下命令中 `claude-code-model-switcher` 可替换为 `claude-code-model-switcher`。

### `--env [模型名]`

输出 export 命令，配合 eval 在当前终端设置环境变量：

```bash
eval "$(claude-code-model-switcher --env)"
eval "$(claude-code-model-switcher --env deepseek-v4-flash)"
# PowerShell:
# claude-code-model-switcher --env deepseek-v4-flash | Invoke-Expression
```

支持通过别名或 modelName 查找模型。

### `--get-sk [模型名]`

输出原始 sk（仅 sk 本身），供 apiKeyHelper 脚本调用：

```bash
claude-code-model-switcher --get-sk deepseek-v4-flash
# → sk-e88203f4d7a34fbda73a8cf24c8656b1
```

### `--reveal`

展示所有模型凭据状态，可选导出完整 JSON（含 sk），用于跨机器迁移：

```bash
# 旧机器：导出
claude-code-model-switcher --reveal
# → 选 Y 导出，保存为 keys.json

# 新机器：导入
claude-code-model-switcher --migrate-import < keys.json
```

### `--help`

显示帮助摘要。

---

## 数据文件

| 文件 | 路径 | 范围 | 说明 |
|------|------|------|------|
| 模型仓库 | `~/.claude/custom-models.json` | 全局 | 别名/URL/modelName/credential，**不含 sk** |
| 项目配置 | `./.claude/settings.json` | 项目 | env + apiKeyHelper + CCMS_MODEL_ALIAS |
| Helper (Win) | `./.claude/get-sk.ps1` | 项目 | PowerShell 脚本 |
| Helper (WSL/Bash)| `./.claude/get-sk.sh` | 项目 | Bash 脚本 |
| 启动器 | `~/.local/bin/claude-code-model-switcher.cmd` | 全局 | Windows CMD 入口 |

### custom-models.json 结构

```json
{
  "DS V4 Flash": {
    "url": "https://api.deepseek.com/anthropic",
    "modelName": "deepseek-v4-flash",
    "credential": {
      "type": "wincred",
      "target": "claude/DS V4 Flash"
    }
  }
}
```

| 字段 | 说明 |
|------|------|
| 外层 key | **别名**，菜单显示用，可随意命名 |
| `modelName` | 真实模型 ID，写入 `ANTHROPIC_MODEL` |
| `url` | API 端点，写入 `ANTHROPIC_BASE_URL` |
| `credential.type` | 凭据后端类型（见下表） |
| `credential.*` | 凭据后端参数 |

### credential 类型

| type | OS | 存储位置 | 参数 |
|------|----|---------|------|
| `wincred` | Windows | 控制面板 → 凭据管理器 → Windows 凭据 | `target` |
| `macos-keychain` | macOS | 钥匙串访问 → 登录 | `service`, `account` |
| `secret-service` | Linux | GNOME Keyring / KDE Wallet | `key`, `label` |

**自动迁移**：旧版 `sk` 字段首次运行时自动转到凭据管理器，同时补全 `modelName` 字段。

---

## apiKeyHelper 机制

Claude Code 支持 `apiKeyHelper`：在 settings.json 中指定命令，运行时自动调用读取认证凭据。

**平台写法差异：**

| OS | settings.json 中 apiKeyHelper 的值 |
|----|-----------------------------------|
| Windows | `"powershell -NoProfile -Command .claude\\get-sk.ps1"` |
| macOS / Linux | `".claude/get-sk.sh"` |

脚本会根据当前平台自动选择正确写法。

**调用链路：**

```
Claude Code 需要认证
  → 执行 apiKeyHelper 命令
  → .ps1/.sh 调用 claude-code-model-switcher --get-sk
  → Python 从 OS 凭据管理器读取 sk
  → sk 写到 stdout
  → Claude Code 使用 sk 作为 AUTH_TOKEN / API_KEY
```

全程**无明文 key 落盘**。settings.json 的 `env` 里也不含 API Key，只有 `ANTHROPIC_BASE_URL`、`ANTHROPIC_MODEL` 和工具托管标记 `CCMS_MODEL_ALIAS`。

### .claude/settings.json 示例（Windows）

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
    "ANTHROPIC_MODEL": "deepseek-v4-pro[1m]",
    "CCMS_MODEL_ALIAS": "deepseek-v4-pro-1m"
  },
  "apiKeyHelper": "powershell -NoProfile -Command .claude\\get-sk.ps1"
}
```

`CCMS_MODEL_ALIAS` 是工具写入的托管标记——有此字段说明当前模型由切换器管理，菜单中正常显示别名；无此字段则显示 `⚠ 未托管` 警告。
}
```

---

## 验证 helper 脚本

```powershell
# Windows
powershell -NoProfile -Command .claude\get-sk.ps1
powershell -NoProfile -Command .claude\get-sk.ps1 deepseek-v4-pro
```

```bash
# WSL / Git Bash
bash .claude/get-sk.sh
bash .claude/get-sk.sh deepseek-v4-pro
```

---

## 迁移到新机器

`custom-models.json` 不含 sk，所以**必须在旧机器仍可用时导出**。完整流程：

```bash
# 1. 旧机器 — 从凭据管理器读取 sk 并导出为 JSON
claude-code-model-switcher --reveal
# → 选择 Y 导出 → 保存为 keys.json

# 2. 复制两个文件到新机器
#    · keys.json（含明文 sk，仅导出时生成）
#    · custom-models.json（模型配置，不含 sk）

# 3. 新机器 — 将 keys.json 中的 sk 写入新凭据管理器
claude-code-model-switcher --migrate-import < keys.json
```

**原理**：`--reveal` 从旧机器的 OS 凭据管理器（本机可读）读出明文 sk 输出到 stdout；`--migrate-import` 读取这个明文 JSON，写入新机器的 OS 凭据管理器。只复制 `custom-models.json` 不能完成迁移——它从未存储 sk。

---

## 环境变量说明

脚本 `--env` 模式输出的两个变量：

| 变量 | 用途 | HTTP 头 |
|------|------|---------|
| `ANTHROPIC_AUTH_TOKEN` | 网关/代理认证 | `Authorization: Bearer` |
| `ANTHROPIC_API_KEY` | 直连 Anthropic API | `X-Api-Key` |

脚本同时填入两者，覆盖不同认证场景。Claude Code 认证优先级：云凭证 > AUTH_TOKEN > API_KEY > apiKeyHelper > OAuth。

---

## 兼容性

- **Python 3.8+**（通过 `from __future__ import annotations` 兼容低版本类型注解）
- **零第三方依赖**，凭据操作全部使用系统原生接口
  - Windows: `advapi32` 通过 ctypes
  - macOS: `security` CLI
  - Linux: `secret-tool` CLI（需要安装了 libsecret）
- **终端**：支持 Windows Terminal（PowerShell/cmd）、WSL、Git Bash、原生 Linux/macOS
- **箭头键**：同时处理 `\xe0`（msvcrt）和 `\x1b[`（VT）两种编码

---

## 已知限制

- 项目切换器脚本放在项目根目录（`claude-code-model-switcher.py`）
- apiKeyHelper 仅在 CLI 会话生效，Claude Desktop 和远程会话使用 OAuth
- WSL 下直接运行 `python3 claude-code-model-switcher.py` 时 `~` 指向 Linux 家目录，需使用 `python.exe`（Windows Python）或确保文件路径可访问
