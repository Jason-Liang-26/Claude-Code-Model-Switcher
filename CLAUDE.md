# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CCMS is a single-file Python script (`claude-code-model-switcher.py`, ~1000 lines) that manages custom model configurations for Claude Code. It stores API keys in the OS native credential manager and generates `apiKeyHelper` scripts so Claude Code can retrieve keys at runtime.

**Key constraint: zero third-party dependencies. Python 3.8+ only.**

## Running / Testing

There is no build system or linter. Tests use stdlib `unittest` only:

```bash
# Run all tests
python -m unittest discover tests

# Run a single test file
python -m unittest tests.test_ccms

# Run a single test class
python -m unittest tests.test_ccms.TestResolveModel

# Interactive menu (primary verification)
python claude-code-model-switcher.py

# Test credential retrieval for a specific model
python claude-code-model-switcher.py --get-sk <alias-or-modelName>

# Check credential status across all models
python claude-code-model-switcher.py --reveal

# Install to ~/.local/bin
./install.cmd        # Windows
./install.ps1        # Windows (PowerShell)
```

## Architecture

### CWD-Driven Project Paths

All project-level paths are resolved against `os.getcwd()` at module load time:

- `.claude/settings.local.json` — local config (env vars + `apiKeyHelper`, gitignored, CCMS writes here)
- `./.claude/get-sk.sh` / `get-sk.ps1` — generated helper scripts

**Critical implication**: running the script from `~` writes to `~/.claude/settings.local.json` (the user-level config), silently affecting all unconfigured projects. A guard (`is_global_config_dir()`) detects this and shows warnings + requires confirmation.

### Three-Layer Data Model

| Layer | Storage | Content |
|-------|---------|---------|
| Global models | `~/.claude/custom-models.json` | Alias, URL, modelName, credential reference (no sk) |
| Local config | `.claude/settings.local.json` | CCMS-managed: `env.ANTHROPIC_BASE_URL`, `env.ANTHROPIC_MODEL`, `apiKeyHelper` (local layer, gitignored) |
| Project config | `.claude/settings.json` | Non-CCMS fields preserved; CCMS reads merged local + project |
| Credentials | OS credential manager / `~/.local/share/ccms/` | Actual sk (DPAPI / Keychain / age / openssl / secret-service) |

### Credential Backend Abstraction

Platform-specific backends are hidden behind `cred_store(cred, sk)` / `cred_retrieve(cred)` / `cred_delete(cred)`:

- **Windows**: `advapi32.CredReadW/WriteW` via ctypes (`_CREDENTIALW` struct)
- **macOS**: `security` CLI subprocess
- **Linux (GUI)**: `secret-tool` CLI subprocess (secret-service / libsecret)
- **Linux (headless, preferred)**: `age` CLI — encrypts with key pair at `~/.local/share/ccms/identity.age`
- **Linux (headless, fallback)**: `openssl enc` CLI — AES-256-CBC with key file at `~/.local/share/ccms/ccms.key`

Linux backend selection: `_is_gui_session()` checks `$DISPLAY` / `$WAYLAND_DISPLAY`. GUI → secret-service. Headless → age (if installed) → linux-file/openssl.

The `credential` dict in `custom-models.json` carries backend-specific params (e.g. `{"type": "age", "identity": "~/.local/share/ccms/identity.age", "keyname": "alias"}`).

### apiKeyHelper Mechanism

When switching models, `_generate_helper_scripts()` creates:

- `.claude/get-sk.ps1` — Windows PowerShell, calls `python ... --get-sk`
- `.claude/get-sk.sh` — WSL/Bash, with WSL path conversion (`C:\...` → `/mnt/c/...`)

Both embed absolute paths at generation time (no runtime path resolution). The helper reads `settings.local.json` to get the current model, then calls `--get-sk` to retrieve the sk from the OS credential manager.

**WSL nuance**: WSL Python cannot access Windows DPAPI. The script ensures the Windows Python executable (`python.exe`) is used in WSL, which has the Windows user token and can call `advapi32`.

### Auto-Migration

`migrate_models()` runs on every load. It handles two legacy formats:

1. Missing `modelName` → auto-populate from alias
2. Plaintext `sk` field → move to OS credential manager, replace with `credential` reference

## Commit Co-Authored-By

Use the actual running model name from the system prompt, not the default `Claude Opus 4.7`. Current format: `Co-Authored-By: Claude Code CLI/Kimi-K2.6`.
