# CCMS — Claude Code Model Switcher

Claude Code 自定义模型管理器。sk 存入 OS 原生凭据管理器，配置文件零明文。

## 项目简介

Claude Code 支持通过环境变量或 `settings.json` 配置第三方模型端点，但多模型切换时需要手动修改 `ANTHROPIC_MODEL`、`ANTHROPIC_BASE_URL` 和 API Key。如果你的 Key 写在配置文件里，还面临明文泄露风险。

CCMS 解决三个问题：

1. **多模型管理** — 模型配置统一维护在 `~/.claude/custom-models.json`，支持别名，一键切换
2. **凭据安全** — sk 不落盘，存入 OS 原生凭据管理器（Windows Credential Manager / macOS Keychain / Linux secret-service），配置文件只存凭据引用
3. **无感认证** — 切换模型时自动生成 apiKeyHelper 脚本，Claude Code 启动时自动从凭据管理器取 Key，无需手动设置环境变量

适合场景：多 API 端点、多模型版本（标准版 vs 1M 上下文版）、团队共用 API 网关的自定义模型。

## 快速开始

```bash
# 安装
.\install.cmd
# 或 PowerShell:
.\install.ps1

# 使用
claude-code-model-switcher
```

## 功能

- **交互式菜单** — ↑↓ 选择模型切换、添加、删除、查看凭据状态
- **零明文 sk** — API Key 存入 Windows Credential Manager / macOS Keychain / Linux secret-service
- **跨平台凭据后端** — wincred / macos-keychain / secret-service，自动检测
- **apiKeyHelper 自动生成** — 切换模型时生成 helper 脚本，Claude Code 启动时自动取 sk
- **跨机器迁移** — `--reveal` 导出 + `--migrate-import` 导入
- **别名系统** — 模型别名 vs modelName 分离，同一 API 不同 model 自由切换

## 数据模型

```
~/.claude/custom-models.json    ← 全局模型仓库（别名/URL/modelName/凭据引用，不含 sk）
./.claude/settings.json          ← 项目配置（env + apiKeyHelper，CWD 驱动）
OS 凭据管理器                     ← sk 实际存储（DPAPI/Keychain/secret-service）
```

## CLI 模式

| 命令 | 用途 |
|------|------|
| `(无参)` | 交互式菜单 |
| `--env [模型]` | 输出 export 命令 |
| `--get-sk [模型]` | 输出原始 sk（供 helper 调用）|
| `--reveal` | 凭据状态 + 迁移导出 |
| `--migrate-import` | 批量导入凭据 |

## 要求

- Python 3.8+
- 零第三方依赖
- Windows Terminal / PowerShell / WSL / Git Bash / Linux / macOS

## 文档

- [用户手册](claude-code-model-switcher-help.md)
- [规格文档](CCMS-SPEC.md)
- [开发日志](devlog/CCMS-DEVLOG.md)
