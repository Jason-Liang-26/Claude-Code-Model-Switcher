# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CCMS is a single-file Python script (`claude-code-model-switcher.py`, ~2300 lines) that manages custom model configurations for Claude Code. It uses a v2 Endpoint architecture (`~/.claude/ccms-endpoints.json`) where each Endpoint groups a URL + credential + multiple models + role routing. It stores API keys in the OS native credential manager and generates `apiKeyHelper` scripts so Claude Code can retrieve keys at runtime.

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

# Test credential retrieval for the current project (reads ccms_settings.local.json)
python claude-code-model-switcher.py --get-sk

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

### Data Model (v2 Endpoint Architecture)

| File | Scope | Content |
|------|-------|---------|
| `~/.claude/ccms-endpoints.json` | Global (user) | Endpoint → Model → Routing. Each endpoint: `url`, `credential` (backend params, no sk), `models` (alias→modelName), `defaultRouting` (role→alias). See `MODELS_PATH`. |
| `.claude/settings.local.json` | Project (gitignored) | CCMS-managed `env`: 4 route env vars + `CCMS_ENDPOINT` + `ANTHROPIC_BASE_URL`, plus `apiKeyHelper`. See `LOCAL_SETTINGS_PATH`. |
| `.claude/ccms_settings.local.json` | Project (gitignored) | Routing snapshot (endpoint + role→alias→modelName). Used by `--get-sk` and consistency check. See `CCMS_SETTINGS_PATH`. |
| `.claude/settings.json` | Project (committed) | Non-CCMS fields preserved. CCMS fields migrated to `settings.local.json` by `_migrate_ccms_fields_from_project()`. |
| OS credential manager / `~/.local/share/ccms/` | OS / filesystem | Actual sk. DPAPI / Keychain / age / openssl / secret-service. |

Legacy: `~/.claude/custom-models.json` (`LEGACY_MODELS_PATH`) is auto-migrated to `ccms-endpoints.json` on first load.

### Role Routing

CCMS routes 4 Claude Code roles to distinct models. Defined in `_ROLE_ENV_MAP`:

| Role | Environment Variable |
|------|---------------------|
| opus | `ANTHROPIC_DEFAULT_OPUS_MODEL` |
| sonnet | `ANTHROPIC_DEFAULT_SONNET_MODEL` |
| haiku | `ANTHROPIC_DEFAULT_HAIKU_MODEL` |
| subagent | `CLAUDE_CODE_SUBAGENT_MODEL` |

Priority: project routing (`ccms_settings.local.json`) > endpoint `defaultRouting` > fallback (all roles → current model).

### Credential Backend Abstraction

Platform-specific backends are hidden behind `cred_store(cred, sk)` / `cred_retrieve(cred)` / `cred_delete(cred)`:

- **Windows**: `advapi32.CredReadW/WriteW` via ctypes (`_CREDENTIALW` struct)
- **macOS**: `security` CLI subprocess
- **Linux (GUI)**: `secret-tool` CLI subprocess (secret-service / libsecret)
- **Linux (headless, preferred)**: `age` CLI — encrypts with key pair at `~/.local/share/ccms/identity.age`
- **Linux (headless, fallback)**: `openssl enc` CLI — AES-256-CBC with key file at `~/.local/share/ccms/ccms.key`

Linux backend selection: `_is_gui_session()` checks `$DISPLAY` / `$WAYLAND_DISPLAY`. GUI → secret-service. Headless → age (if installed) → linux-file/openssl.

The `credential` dict in `ccms-endpoints.json` carries backend-specific params (e.g. `{"type": "age", "identity": "~/.local/share/ccms/identity.age", "keyname": "alias"}`).

### apiKeyHelper Mechanism

When switching models, `_generate_helper_scripts()` creates:

- `.claude/get-sk.ps1` — Windows PowerShell, calls `python ... --get-sk`
- `.claude/get-sk.sh` — WSL/Bash, with WSL path conversion (`C:\...` → `/mnt/c/...`)

Both embed absolute paths at generation time (no runtime path resolution). The helper calls `--get-sk`, which reads `ccms_settings.local.json` to get the current endpoint, then looks up the credential in `ccms-endpoints.json` and retrieves the sk from the OS credential manager.

**WSL nuance**: WSL Python cannot access Windows DPAPI. The script ensures the Windows Python executable (`python.exe`) is used in WSL, which has the Windows user token and can call `advapi32`.

### Auto-Migration

`load_custom_models()` runs on every load and performs lazy migration:

1. **v1 → v2** (`_import_legacy()`): Converts flat `custom-models.json` (`{alias: {url, modelName, sk/credential}}`) into v2 Endpoint architecture. Models sharing the same URL+credential are grouped under one Endpoint. Plaintext `sk` fields are moved to OS credential manager, replaced with `credential` reference.
2. **Old v2 → current v2** (`_migrate_old_v2()`): Migrates data where `models` and `routing` were top-level keys (alongside `endpoints`) into the current structure where each model lives inside its parent endpoint and `defaultRouting` is per-endpoint. Old top-level `models`/`routing`/`_version` keys are removed.

## Commit Co-Authored-By

Use the actual running model name from the system prompt, not the default `Claude Opus 4.7`. Current format: `Co-Authored-By: Claude Code CLI/Kimi-K2.6`.
