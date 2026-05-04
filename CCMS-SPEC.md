# CCMS (Claude Code Model Switcher) — 规格文档

## 概述

CCMS 是 Claude Code 自定义模型管理工具，功能：
- 管理多模型配置（别名、URL、modelName、凭据）
- sk 存入 OS 原生凭据管理器，配置文件零明文
- 切换模型时自动更新项目 `.claude/settings.json` 并生成 apiKeyHelper 脚本
- 支持跨平台凭据后端、跨机器迁移

## 架构

```
┌──────────────────────────────────────────────────┐
│                  claude-code-model-switcher.py          │
│                                                    │
│  ┌─────────────┐  ┌────────────┐  ┌────────────┐ │
│  │ 交互式菜单    │  │ CLI 模式    │  │ 凭据后端    │ │
│  │ main()       │  │ --env       │  │ wincred    │ │
│  │ 切换/添加/删除 │  │ --get-sk    │  │ macos-key  │ │
│  │ 凭据状态      │  │ --reveal    │  │ secret-svc │ │
│  └──────┬───────┘  │ --migrate.. │  └─────┬──────┘ │
│         │          └──────┬──────┘        │         │
│         │                 │               │         │
│  ┌──────┴─────────────────┴───────────────┴──────┐ │
│  │              数据层                             │ │
│  │  ~/.claude/custom-models.json  (全局，不含sk)    │ │
│  │  ./.claude/settings.json       (项目，CWD)      │ │
│  │  ./.claude/get-sk.ps1 / .sh    (helper，生成)   │ │
│  │  OS 凭据管理器                  (sk 存储)       │ │
│  └───────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────┘

Claude Code 启动
  → settings.json: apiKeyHelper → 命令
  → get-sk.ps1 / get-sk.sh
    → python claude-code-model-switcher.py --get-sk <model>
    → cred_retrieve() → OS 凭据管理器 → sk → stdout
  → Claude Code 拿到 sk，发起请求
```

## 数据模型

### custom-models.json（全局、用户级）

```json
{
  "<alias>": {
    "url": "https://api.deepseek.com/anthropic",
    "modelName": "deepseek-v4-pro[1m]",
    "credential": {
      "type": "wincred",
      "target": "claude/<alias>"
    }
  }
}
```

| 字段 | 说明 | 写入位置 |
|------|------|---------|
| 外层 key | 别名，菜单显示用 | — |
| url | API 端点 | env.ANTHROPIC_BASE_URL |
| modelName | 真实模型 ID | env.ANTHROPIC_MODEL |
| credential.type | 凭据后端类型 | — |
| credential.* | 后端参数 | — |

### .claude/settings.json（项目级、CWD）

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

`CCMS_MODEL_ALIAS` 是托管标记——有此字段说明由本工具管理。缺失时交互界面显示"未托管"警告。env 合并写入，不覆盖用户手动添加的其他变量。

## 凭据后端

| type | OS | 存储 | API | 安全模型 |
|------|----|------|-----|---------|
| wincred | Windows | 凭据管理器 (DPAPI) | advapi32.CredReadW/WriteW (ctypes) | Windows 登录会话 |
| macos-keychain | macOS | 钥匙串 | security CLI | 钥匙串访问控制 |
| secret-service | Linux | GNOME/KDE | secret-tool CLI | libsecret |

旧版 `sk` 字段首次运行时自动迁移到凭据管理器。

## CLI 参考

| 命令 | 用途 | 输出 |
|------|------|------|
| `(无参)` | 交互式菜单 | TUI |
| `--env [alias/modelName]` | 输出 export 命令 | stdout (供 eval) |
| `--get-sk [alias/modelName]` | 输出原始 sk | stdout (供 apiKeyHelper) |
| `--reveal` | 查看凭据状态 + 导出 | 表格 + 可选 JSON |
| `--migrate-import` | stdin JSON 批量导入 | 日志 |
| `--help` | 帮助 | 文本 |

## helper 脚本

切换模型时自动生成两份：

| 文件 | 平台 | 内容 |
|------|------|------|
| `.claude/get-sk.ps1` | Windows | PowerShell，委托 `--get-sk` |
| `.claude/get-sk.sh` | WSL/Bash | Bash，委托 `--get-sk` |

均嵌入生成时的绝对路径，不依赖运行时路径解析。

## 安装

```
项目目录/
├── claude-code-model-switcher.py     ← 主脚本
├── claude-code-model-switcher.cmd    ← 项目本地启动器 (%~dp0)
├── install.cmd                  ← 安装器 (CMD)
├── install.ps1                  ← 安装器 (PowerShell)
└── .claude/
    ├── settings.json
    ├── get-sk.ps1
    └── get-sk.sh

安装后 → ~/.local/
├── bin/claude-code-model-switcher.cmd
└── lib/claude-code-model-switcher/claude-code-model-switcher.py
```

运行 `install.cmd` 或 `install.ps1` 完成安装。

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
- 凭据读取受限于当前 OS 用户会话（DPAPI/Keychain/secret-service）
- WSL 下需用 Windows Python（`python.exe`）调用 advapi32，Linux Python 无 DPAPI 访问权限
