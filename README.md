# CCMS — Claude Code Model Switcher

Claude Code 自定义模型管理器。sk 存入 OS 原生凭据管理器，配置文件零明文。

## 项目简介

Claude Code 支持通过环境变量或 `settings.json` 配置第三方模型端点，但多模型切换时需要手动修改环境变量和 API Key。如果你的 Key 写在配置文件里，还面临明文泄露风险。

CCMS 解决三个问题：

1. **多 Endpoint 管理** — Endpoint（URL + 凭据）与其下的模型统一维护在 `~/.claude/ccms-endpoints.json`，支持别名，Tab 式菜单一键切换
2. **凭据安全** — sk 不落盘，存入 OS 原生凭据管理器（Windows Credential Manager / macOS Keychain / age 加密 / OpenSSL / secret-service，自动适配）
3. **无感认证** — 切换模型时自动生成 apiKeyHelper 脚本，Claude Code 启动时自动从凭据管理器取 Key，无需手动设置环境变量
4. **角色路由** — opus/sonnet/haiku/subagent 四个角色可分配不同模型，Endpoint 级默认路由 + 项目级覆盖

适合场景：多 API 端点、多模型版本（标准版 vs 1M 上下文版）、团队共用 API 网关的自定义模型。

## 快速开始

```bash
# 安装
.\install.cmd          # Windows CMD
.\install.ps1          # Windows PowerShell
./install.sh           # Linux / macOS

# 使用
claude-code-model-switcher
```

## 功能

- **Tab 式交互菜单** — Endpoint 管理 / 路由管理 / 模型管理，`← →` 切换 Tab，`↑↓` 选择
- **Endpoint 架构** — URL + 凭据 + 模型列表 + 路由表，一个 Endpoint 共享凭据
- **角色路由** — opus/sonnet/haiku/subagent 四个角色独立分配模型
- **零明文 sk** — API Key 存入 Windows Credential Manager / macOS Keychain / age 加密 / OpenSSL / secret-service
- **跨平台凭据后端** — wincred / macos-keychain / age / linux-file (openssl) / secret-service，自动检测
- **apiKeyHelper 自动生成** — 切换模型时生成 helper 脚本，Claude Code 启动时自动取 sk
- **一致性检查** — 启动时自动比对 ccms_settings 与 settings.local，发现差异提示同步
- **跨机器迁移** — `--reveal` 导出 + `--migrate-import` 导入

## 数据模型

```
~/.claude/ccms-endpoints.json        ← 全局 Endpoint 模型库（URL/凭据/模型/路由，不含 sk）
.claude/settings.local.json          ← 项目配置（env 角色路由 + apiKeyHelper，gitignored）
.claude/ccms_settings.local.json     ← CCMS 路由快照（endpoint + routing，gitignored）
OS 凭据管理器 / ~/.local/share/ccms/ ← sk 实际存储（DPAPI/Keychain/age/openssl/secret-service）
```

## CLI 模式

| 命令 | 用途 |
|------|------|
| `(无参)` | 交互式菜单 |
| `--env [模型]` | 输出 export 命令 |
| `--get-sk` | 输出当前项目的 sk（从 ccms_settings 读 endpoint） |
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
