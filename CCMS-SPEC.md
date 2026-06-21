# CCMS (Claude Code Model Switcher) — 规格文档

## 概述

CCMS 是 Claude Code 自定义模型管理工具，功能：
- 管理多 Endpoint 及其下模型（别名、URL、modelName、凭据）
- sk 存入 OS 原生凭据管理器，配置文件零明文
- 切换模型时自动更新项目 `.claude/settings.local.json` 并生成 apiKeyHelper 脚本
- 支持角色路由（opus/sonnet/haiku/subagent 分配不同模型）
- 支持跨平台凭据后端、跨机器迁移

## 架构

```
┌───────────────────────────────────────────────────────────┐
│              claude-code-model-switcher.py                 │
│                                                           │
│  ┌─────────────┐  ┌────────────┐  ┌────────────────────┐ │
│  │ 交互式菜单    │  │ CLI 模式    │  │ 凭据后端            │ │
│  │ main()       │  │ --env       │  │ wincred            │ │
│  │ Tab 菜单     │  │ --get-sk    │  │ macos-keychain     │ │
│  │ Endpoint 管理 │  │ --reveal    │  │ secret-service     │ │
│  │ 路由管理      │  │ --migrate.. │  │ age / linux-file   │ │
│  └──────┬───────┘  └──────┬──────┘  └─────────┬──────────┘ │
│         │                 │                    │            │
│  ┌──────┴─────────────────┴────────────────────┴─────────┐ │
│  │                    数据层                               │ │
│  │  ~/.claude/ccms-endpoints.json   (全局 endpoint 模型库) │ │
│  │  .claude/settings.local.json     (项目本地 env+helper)  │ │
│  │  .claude/ccms_settings.local.json (CCMS 路由快照)      │ │
│  │  .claude/get-sk.ps1 / .sh        (helper，生成)        │ │
│  │  OS 凭据管理器 / ~/.local/share/ccms/  (sk 存储)       │ │
│  └────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────┘

Claude Code 启动
  → settings.local.json: apiKeyHelper → 命令
  → get-sk.ps1 / get-sk.sh
    → python claude-code-model-switcher.py --get-sk
    → 读 ccms_settings.local.json → endpoint → credential
    → cred_retrieve() → OS 凭据管理器 → sk → stdout
  → Claude Code 拿到 sk，发起请求
```

## 数据模型

### ccms-endpoints.json（全局、用户级、v2 endpoint 架构）

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

| 字段 | 说明 |
|------|------|
| `endpoints.<name>` | Endpoint 名称（自动从 URL hostname 推断） |
| `url` | API 端点地址 |
| `credential` | 凭据后端配置（类型 + 参数） |
| `models.<alias>` | 该 Endpoint 下的模型，alias 为菜单显示别名 |
| `models.<alias>.modelName` | 真实模型 ID，写入对应的 `ANTHROPIC_DEFAULT_*_MODEL` |
| `defaultRouting` | Endpoint 默认路由，角色 → 模型别名 |

**三层模型**：Endpoint → Model → Routing。一个 Endpoint 共享 URL 和凭据，下挂多个模型。路由表决定每个角色（opus/sonnet/haiku/subagent）使用哪个模型。

**旧版迁移**：首次运行时自动从 `~/.claude/custom-models.json`（v1 扁平格式）或旧 v2 格式迁移到新的 endpoint 架构。

### .claude/settings.local.json（项目本地、CWD、gitignored）

CCMS 写入此文件（local 层），因为是开发者个人偏好而非项目配置。读取时合并 local + project 两层，local 覆盖 project。

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

| 环境变量 | 说明 |
|---------|------|
| `ANTHROPIC_BASE_URL` | API 端点地址 |
| `ANTHROPIC_DEFAULT_OPUS_MODEL` | Opus 角色使用的模型 ID |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` | Sonnet 角色使用的模型 ID |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL` | Haiku 角色使用的模型 ID |
| `CLAUDE_CODE_SUBAGENT_MODEL` | 子代理使用的模型 ID |
| `CCMS_ENDPOINT` | 当前活跃的 Endpoint 名称 |

> **v1 遗留**：`CCMS_MODEL_ALIAS` 是 v1 时代的托管标记，v2 已不再写入。切换模型时自动清除（`env.pop("CCMS_MODEL_ALIAS", None)`），`_migrate_ccms_fields_from_project()` 也会从项目 `settings.json` 中清理。

### .claude/ccms_settings.local.json（项目本地、CWD、gitignored）

CCMS 路由快照文件，记录当前项目的 Endpoint 和路由配置。用于 `--get-sk` 和一致性检查。

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

## 凭据后端

| type | OS | 存储 | API | 安全模型 |
|------|----|------|-----|---------|
| wincred | Windows | 凭据管理器 (DPAPI) | advapi32.CredReadW/WriteW (ctypes) | Windows 登录会话 |
| macos-keychain | macOS | 钥匙串 | security CLI | 钥匙串访问控制 |
| secret-service | Linux (GUI) | GNOME/KDE | secret-tool CLI | libsecret |
| age | Linux (headless 首选) | `~/.local/share/ccms/creds/*.age` | age CLI | Curve25519 密钥对 |
| linux-file | Linux (fallback) | `~/.local/share/ccms/creds/*.enc` | openssl CLI | AES-256-CBC + 本地密钥文件 |

**Linux 后端选择逻辑**：GUI 会话（`$DISPLAY` / `$WAYLAND_DISPLAY` 存在）→ secret-service；headless + age 已安装 → age（`$CCMS_AGE_IDENTITY` 指定身份文件）；否则 → linux-file (openssl)。

旧版 `sk` 字段首次运行时自动迁移到凭据管理器。

## CLI 参考

| 命令 | 用途 | 输出 |
|------|------|------|
| `(无参)` | 交互式菜单 | TUI |
| `--env [alias/modelName]` | 输出 export 命令 | stdout (供 eval) |
| `--get-sk` | 输出当前项目的 sk（从 ccms_settings 读 endpoint） | stdout (供 apiKeyHelper) |
| `--reveal` | 查看凭据状态 + 导出 | 表格 + 可选 JSON |
| `--migrate-import` | stdin JSON 批量导入 | 日志 |
| `--help` | 帮助 | 文本 |

**注意**：`--get-sk` 不再接受模型名参数，改为从 `ccms_settings.local.json` 读取当前 Endpoint，再从 `ccms-endpoints.json` 查找对应凭据。

## 交互式菜单

采用 Tab 式菜单布局，`← →` 切换 Tab，`↑↓` 在 Tab 内选择，`Enter` 确认，`ESC` 退出。

### Tab: Endpoint 管理
| 功能 | 说明 |
|------|------|
| 切换 Endpoint | 切换活跃 Endpoint，应用其 defaultRouting 到项目 |
| 管理 Endpoints | 创建 / 重命名 / 删除 Endpoint |
| 修改凭据 | 更新当前 Endpoint 的 API Key |

### Tab: 路由管理
| 功能 | 说明 |
|------|------|
| 编辑 endpoint 默认路由 | 修改 Endpoint 级路由（全局 ccms-endpoints.json） |
| 编辑当前项目路由 | 修改项目级路由（settings.local.json + ccms_settings.local.json） |

路由编辑器：`↑↓` 选择槽位（opus / sonnet / haiku / subagent），`← →` 切换该槽位对应的模型，`Enter` 确认。

### Tab: 模型管理
| 功能 | 说明 |
|------|------|
| 添加模型 | 在当前 Endpoint 下添加模型（凭据继承 Endpoint） |
| 删除模型 | 删除当前 Endpoint 下的模型 |

### 底部公共项
| 功能 | 说明 |
|------|------|
| 查看所有凭据 | 列出所有模型的凭据状态 |
| 退出 | 退出程序 |

## 角色路由

CCMS 支持 4 个角色的独立路由：

| 槽位 | 环境变量 | 说明 |
|------|---------|------|
| opus | `ANTHROPIC_DEFAULT_OPUS_MODEL` | Claude Code Opus 角色 |
| sonnet | `ANTHROPIC_DEFAULT_SONNET_MODEL` | Claude Code Sonnet 角色 |
| haiku | `ANTHROPIC_DEFAULT_HAIKU_MODEL` | Claude Code Haiku 角色 |
| subagent | `CLAUDE_CODE_SUBAGENT_MODEL` | Claude Code 子代理 |

路由来源优先级：
1. 项目级路由（`ccms_settings.local.json`）
2. Endpoint 默认路由（`ccms-endpoints.json` → endpoint.defaultRouting）
3. Fallback：全部指向当前模型

## helper 脚本

切换模型时自动生成两份：

| 文件 | 平台 | 内容 |
|------|------|------|
| `.claude/get-sk.ps1` | Windows | PowerShell，委托 `--get-sk` |
| `.claude/get-sk.sh` | WSL/Bash | Bash，委托 `--get-sk` |

均嵌入生成时的绝对路径，不依赖运行时路径解析。

## 一致性检查

启动时自动比对 `ccms_settings.local.json` 与 `settings.local.json` 的一致性，检测以下字段：
- `CCMS_ENDPOINT`
- 4 路路由环境变量（`ANTHROPIC_DEFAULT_OPUS_MODEL` 等）

发现差异时提供三种处理方式：
1. 以 ccms_settings 为准，覆盖 settings.local
2. 以 settings.local 为准，更新 ccms_settings
3. 忽略

## 安装

```
项目目录/
├── claude-code-model-switcher.py     ← 主脚本
├── claude-code-model-switcher.cmd    ← 项目本地启动器 (%~dp0)
├── install.cmd                  ← 安装器 (CMD)
├── install.ps1                  ← 安装器 (PowerShell)
├── install.sh                   ← 安装器 (Linux/macOS)
└── .claude/
    ├── settings.local.json
    ├── ccms_settings.local.json
    ├── get-sk.ps1
    └── get-sk.sh

安装后 → ~/.local/
├── bin/claude-code-model-switcher.cmd
└── lib/claude-code-model-switcher/claude-code-model-switcher.py
```

运行 `install.cmd`、`install.ps1` 或 `install.sh` 完成安装。

## 兼容性

- Python 3.8+（`from __future__ import annotations`）
- 零第三方依赖
- 终端：Windows Terminal / PowerShell / cmd / WSL / Git Bash / Linux / macOS
- 箭头键：同时兼容 `\xe0`（msvcrt）和 `\x1b[`（VT）

## 迁移流程

```
旧机器: --reveal → 从凭据管理器读 sk → 输出明文 JSON → keys.json
新机器: --migrate-import < keys.json → 写入新凭据管理器
```

## 安全边界

- sk 仅存 OS 凭据管理器，配置文件零明文
- 凭据读取受限于当前 OS 用户会话（DPAPI/Keychain/age/file-permissions/secret-service）
- WSL 下需用 Windows Python（`python.exe`）调用 advapi32，Linux Python 无 DPAPI 访问权限
- `settings.local.json` 和 `ccms_settings.local.json` 自动加入 `.gitignore`
