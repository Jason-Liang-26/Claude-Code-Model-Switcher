# Claude 模型切换器 (CCMS)

管理 Endpoint 及其下的自定义模型配置，sk 存入 OS 凭据管理器（不落盘），切换模型时自动更新项目配置并生成 apiKeyHelper 脚本。支持角色路由（opus/sonnet/haiku/subagent 分配不同模型）。要求 Python 3.8+，零第三方依赖。

---

## 安装与环境配置

### 1. 安装到全局 PATH

```bash
# Windows CMD
install.cmd

# Windows PowerShell
.\install.ps1

# Linux / macOS
./install.sh
```

安装脚本会将文件复制到 `~/.local/bin` + `~/.local/lib/claude-code-model-switcher/`。`~/.local/bin` 需在系统 PATH 中（Git Bash 默认已包含，PowerShell 需手动添加）。

### 2. 验证

```bash
claude-code-model-switcher --help
```

### 3. 工作原理

脚本基于**当前工作目录 (CWD)** 寻找 `.claude/` 目录——`cd` 到哪个项目，就操作哪个项目的配置。`~/.claude/ccms-endpoints.json`（Endpoint 模型库）为全局唯一。

---

## 数据模型

CCMS 采用 Endpoint 架构，三层模型：Endpoint → Model → Routing。

### Endpoint（端点）

一个 Endpoint 包含：
- **URL** — API 端点地址
- **凭据** — 该 Endpoint 的 API Key（存入 OS 凭据管理器）
- **模型列表** — 该 Endpoint 下的多个模型（共享 URL 和凭据）
- **默认路由** — 角色 → 模型的映射关系

### 模型（Model）

每个模型有：
- **别名** — 菜单显示用，可随意命名
- **modelName** — 真实模型 ID，写入环境变量

### 路由（Routing）

4 个角色各自独立分配模型：

| 角色 | 环境变量 | 说明 |
|------|---------|------|
| opus | `ANTHROPIC_DEFAULT_OPUS_MODEL` | Claude Code Opus 角色 |
| sonnet | `ANTHROPIC_DEFAULT_SONNET_MODEL` | Claude Code Sonnet 角色 |
| haiku | `ANTHROPIC_DEFAULT_HAIKU_MODEL` | Claude Code Haiku 角色 |
| subagent | `CLAUDE_CODE_SUBAGENT_MODEL` | Claude Code 子代理 |

路由有两级：
1. **Endpoint 默认路由** — 全局，存在 `ccms-endpoints.json` 中
2. **项目路由** — 项目级，存在 `ccms_settings.local.json` 中，覆盖 Endpoint 默认路由

### 数据文件

| 文件 | 路径 | 范围 | 说明 |
|------|------|------|------|
| Endpoint 模型库 | `~/.claude/ccms-endpoints.json` | 全局 | Endpoint/模型/路由/凭据引用，**不含 sk** |
| 项目配置 | `.claude/settings.local.json` | 项目 | env 角色路由 + apiKeyHelper |
| CCMS 路由快照 | `.claude/ccms_settings.local.json` | 项目 | endpoint + routing 快照 |
| Helper (Win) | `.claude/get-sk.ps1` | 项目 | PowerShell 脚本 |
| Helper (WSL/Bash)| `.claude/get-sk.sh` | 项目 | Bash 脚本 |

### ccms-endpoints.json 结构

```json
{
  "endpoints": {
    "deepseek": {
      "url": "https://api.deepseek.com/anthropic",
      "credential": {
        "type": "wincred",
        "target": "claude/deepseek"
      },
      "models": {
        "DS V4 Flash": {
          "modelName": "deepseek-v4-flash"
        },
        "DS V4 Pro 1M": {
          "modelName": "deepseek-v4-pro[1m]"
        }
      },
      "defaultRouting": {
        "opus": "DS V4 Pro 1M",
        "sonnet": "DS V4 Flash",
        "haiku": "DS V4 Flash",
        "subagent": "DS V4 Flash"
      }
    }
  }
}
```

### .claude/settings.local.json 示例（Windows）

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "deepseek-v4-pro[1m]",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "deepseek-v4-flash",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "deepseek-v4-flash",
    "CLAUDE_CODE_SUBAGENT_MODEL": "deepseek-v4-flash",
    "CCMS_ENDPOINT": "deepseek"
  },
  "apiKeyHelper": "powershell -NoProfile -Command .claude\\get-sk.ps1"
}
```

### .claude/ccms_settings.local.json 示例

```json
{
  "endpoint": "deepseek",
  "routing": {
    "opus": {
      "alias": "DS V4 Pro 1M",
      "modelName": "deepseek-v4-pro[1m]"
    },
    "sonnet": {
      "alias": "DS V4 Flash",
      "modelName": "deepseek-v4-flash"
    },
    "haiku": {
      "alias": "DS V4 Flash",
      "modelName": "deepseek-v4-flash"
    },
    "subagent": {
      "alias": "DS V4 Flash",
      "modelName": "deepseek-v4-flash"
    }
  }
}
```

---

## 交互式菜单

```bash
claude-code-model-switcher
```

采用 Tab 式菜单布局：

### Tab: Endpoint 管理

| 功能 | 说明 |
|------|------|
| **切换 Endpoint** | 选择 Endpoint → 应用其 defaultRouting 到当前项目 |
| **管理 Endpoints** | 创建 / 重命名 / 删除 Endpoint |
| **修改凭据** | 更新当前 Endpoint 的 API Key |

### Tab: 路由管理

| 功能 | 说明 |
|------|------|
| **编辑 endpoint 默认路由** | 修改 Endpoint 级路由（全局 ccms-endpoints.json） |
| **编辑当前项目路由** | 修改项目级路由（settings.local.json + ccms_settings.local.json） |

路由编辑器：`↑↓` 选择角色，`← →` 切换该角色对应的模型，`Enter` 确认，`ESC` 放弃。

### Tab: 模型管理

| 功能 | 说明 |
|------|------|
| **添加模型** | 在当前 Endpoint 下添加模型（凭据继承 Endpoint，无需重新输入 Key） |
| **删除模型** | 删除当前 Endpoint 下的模型 |

### 底部公共项

| 功能 | 说明 |
|------|------|
| **查看所有凭据** | 列出所有模型的凭据状态 |
| **退出** | 退出程序 |

操作：`← →` 切换 Tab，`↑↓` 选择，`Enter` 确认，`ESC` 返回/退出。

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

支持通过别名或 modelName 查找模型。无参数时使用当前项目配置的模型。

### `--get-sk`

输出当前项目的 sk（从 `ccms_settings.local.json` 读取 Endpoint，再从 `ccms-endpoints.json` 查找凭据）：

```bash
claude-code-model-switcher --get-sk
# → sk-e88203f4d7a34fbda73a8cf24c8656b1
```

**注意**：此命令不再接受模型名参数，改为从项目配置自动读取。供 apiKeyHelper 脚本调用。

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

## 凭据后端

| type | OS | 存储位置 | 参数 |
|------|----|---------|------|
| `wincred` | Windows | 控制面板 → 凭据管理器 → Windows 凭据 | `target` |
| `macos-keychain` | macOS | 钥匙串访问 → 登录 | `service`, `account` |
| `secret-service` | Linux (GUI) | GNOME Keyring / KDE Wallet | `key`, `label` |
| `age` | Linux (headless 首选) | `~/.local/share/ccms/creds/*.age` | `identity`, `keyname` |
| `linux-file` | Linux (fallback) | `~/.local/share/ccms/creds/*.enc` | `keyname` |

**Linux 后端自动选择**：GUI 会话优先 secret-service；headless (SSH/tty) 优先 age（需安装，否则回退到 openssl）。age 身份文件自动生成于 `~/.local/share/ccms/identity.age`，也可通过 `$CCMS_AGE_IDENTITY` 环境变量或 `~/.config/ccms/age-identity` 文件指定自定义路径。

**自动迁移**：旧版 `sk` 字段首次运行时自动转到凭据管理器，同时补全 `modelName` 字段。

---

## apiKeyHelper 机制

Claude Code 支持 `apiKeyHelper`：在 settings.json 中指定命令，运行时自动调用读取认证凭据。

**平台写法差异：**

| OS | settings.local.json 中 apiKeyHelper 的值 |
|----|----------------------------------------|
| Windows | `"powershell -NoProfile -Command .claude\\get-sk.ps1"` |
| macOS / Linux | `".claude/get-sk.sh"` |

脚本会根据当前平台自动选择正确写法。

**调用链路：**

```
Claude Code 需要认证
  → 执行 apiKeyHelper 命令
  → .ps1/.sh 调用 claude-code-model-switcher --get-sk
  → Python 从 ccms_settings.local.json 读取 endpoint
  → 从 ccms-endpoints.json 查找 endpoint 的 credential
  → cred_retrieve() 从 OS 凭据管理器读取 sk
  → sk 写到 stdout
  → Claude Code 使用 sk 作为 AUTH_TOKEN / API_KEY
```

全程**无明文 key 落盘**。settings.local.json 的 `env` 里不含 API Key，只有角色路由环境变量和 `CCMS_ENDPOINT`。

---

## 一致性检查

启动时自动比对 `ccms_settings.local.json` 与 `settings.local.json` 的一致性。发现差异时提供三种处理方式：

1. 以 ccms_settings 为准，覆盖 settings.local
2. 以 settings.local 为准，更新 ccms_settings
3. 忽略

检测字段：`CCMS_ENDPOINT`、4 路路由环境变量。

---

## 验证 helper 脚本

```powershell
# Windows
powershell -NoProfile -Command .claude\get-sk.ps1
```

```bash
# WSL / Git Bash
bash .claude/get-sk.sh
```

---

## 迁移到新机器

`ccms-endpoints.json` 不含 sk，所以**必须在旧机器仍可用时导出**。完整流程：

```bash
# 1. 旧机器 — 从凭据管理器读取 sk 并导出为 JSON
claude-code-model-switcher --reveal
# → 选择 Y 导出 → 保存为 keys.json

# 2. 复制两个文件到新机器
#    · keys.json（含明文 sk，仅导出时生成）
#    · ccms-endpoints.json（Endpoint 配置，不含 sk）

# 3. 新机器 — 将 keys.json 中的 sk 写入新凭据管理器
claude-code-model-switcher --migrate-import < keys.json
```

**原理**：`--reveal` 从旧机器的 OS 凭据管理器（本机可读）读出明文 sk 输出到 stdout；`--migrate-import` 读取这个明文 JSON，写入新机器的 OS 凭据管理器。只复制 `ccms-endpoints.json` 不能完成迁移——它从未存储 sk。

---

## 环境变量说明

脚本写入的环境变量：

| 变量 | 用途 |
|------|------|
| `ANTHROPIC_BASE_URL` | API 端点地址 |
| `ANTHROPIC_DEFAULT_OPUS_MODEL` | Opus 角色模型 ID |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` | Sonnet 角色模型 ID |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL` | Haiku 角色模型 ID |
| `CLAUDE_CODE_SUBAGENT_MODEL` | 子代理模型 ID |
| `CCMS_ENDPOINT` | 当前活跃 Endpoint 名称 |

`--env` 模式额外输出：

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
  - Linux: `secret-tool` CLI (GUI) / `age` CLI (headless 首选) / `openssl` CLI (fallback，OS 自带)
- **终端**：支持 Windows Terminal（PowerShell/cmd）、WSL、Git Bash、原生 Linux/macOS
- **箭头键**：同时处理 `\xe0`（msvcrt）和 `\x1b[`（VT）两种编码

---

## 已知限制

- apiKeyHelper 仅在 CLI 会话生效，Claude Desktop 和远程会话使用 OAuth
- WSL 下直接运行 `python3 claude-code-model-switcher.py` 时 `~` 指向 Linux 家目录，需使用 `python.exe`（Windows Python）或确保文件路径可访问
- 旧版 `custom-models.json` 首次运行时自动迁移到 `ccms-endpoints.json`，迁移后原文件保留但不再使用
