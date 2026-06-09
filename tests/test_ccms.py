"""CCMS unit tests — zero third-party dependencies, stdlib only."""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Load the hyphenated filename module via importlib.util
_REPO_ROOT = Path(__file__).parent.parent
_spec = importlib.util.spec_from_file_location(
    "ccms", _REPO_ROOT / "claude-code-model-switcher.py"
)
ccms = importlib.util.module_from_spec(_spec)
sys.modules["ccms"] = ccms
_spec.loader.exec_module(ccms)


class TestJsonStripTrailingCommas(unittest.TestCase):
    """_json_strip_trailing_commas — pure string transformation."""

    def test_no_trailing_comma(self):
        text = '{"a": 1, "b": 2}'
        self.assertEqual(ccms._json_strip_trailing_commas(text), text)

    def test_trailing_comma_in_object(self):
        text = '{"a": 1,}'
        self.assertEqual(ccms._json_strip_trailing_commas(text), '{"a": 1}')

    def test_trailing_comma_in_array(self):
        text = '[1, 2, 3,]'
        self.assertEqual(ccms._json_strip_trailing_commas(text), '[1, 2, 3]')

    def test_nested_with_trailing_commas(self):
        text = '{"arr": [1, 2,], "obj": {"k": "v",},}'
        expected = '{"arr": [1, 2], "obj": {"k": "v"}}'
        self.assertEqual(ccms._json_strip_trailing_commas(text), expected)

    def test_whitespace_before_comma(self):
        text = '{"a": 1,  \n  }'
        self.assertEqual(ccms._json_strip_trailing_commas(text), '{"a": 1}')


class TestResolveModel(unittest.TestCase):
    """resolve_model — lookup by alias or modelName."""

    def setUp(self):
        self.models = {
            "endpoints": {
                "example": {"url": "https://api.example.com", "credential": {},
                            "models": {"ds-v3": {"modelName": "deepseek-v3"}}},
                "openai": {"url": "https://api.openai.com", "credential": {},
                           "models": {"gpt4o": {"modelName": "gpt-4o"}}},
            },
            "routing": {},
        }

    def test_by_alias(self):
        result = ccms.resolve_model("ds-v3", self.models)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "ds-v3")
        self.assertEqual(result[1]["modelName"], "deepseek-v3")

    def test_by_modelName(self):
        result = ccms.resolve_model("gpt-4o", self.models)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "gpt4o")

    def test_not_found(self):
        self.assertIsNone(ccms.resolve_model("nonexistent", self.models))


class TestDetectPlatform(unittest.TestCase):
    """_detect_platform — maps platform.system() to internal identifiers."""

    @mock.patch("ccms.platform.system")
    def test_windows(self, mock_system):
        mock_system.return_value = "Windows"
        self.assertEqual(ccms._detect_platform(), "windows")

    @mock.patch("ccms.platform.system")
    def test_darwin(self, mock_system):
        mock_system.return_value = "Darwin"
        self.assertEqual(ccms._detect_platform(), "macos")

    @mock.patch("ccms.platform.system")
    def test_linux(self, mock_system):
        mock_system.return_value = "Linux"
        self.assertEqual(ccms._detect_platform(), "linux")


class TestIsGuiSession(unittest.TestCase):
    """_is_gui_session — detects display environment."""

    @mock.patch.dict("ccms.os.environ", {"DISPLAY": ":0"}, clear=True)
    def test_x11_display(self):
        self.assertTrue(ccms._is_gui_session())

    @mock.patch.dict("ccms.os.environ", {"WAYLAND_DISPLAY": "wayland-0"},
                     clear=True)
    def test_wayland_display(self):
        self.assertTrue(ccms._is_gui_session())

    @mock.patch.dict("ccms.os.environ", {}, clear=True)
    def test_headless(self):
        self.assertFalse(ccms._is_gui_session())


class TestAgeResolveIdentity(unittest.TestCase):
    """_age_resolve_identity — resolves age identity path."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.patch_dir = mock.patch("ccms.CCMS_AGE_IDENTITY_DEFAULT",
                                    os.path.join(self.tmpdir.name,
                                                 "identity.age"))
        self.patch_dir.start()

    def tearDown(self):
        self.patch_dir.stop()
        self.tmpdir.cleanup()

    @mock.patch("ccms.os.environ", {"CCMS_AGE_IDENTITY": "/custom/key.age"})
    @mock.patch("ccms.os.path.isfile", return_value=True)
    @mock.patch("ccms.os.path.realpath", side_effect=lambda x: x)
    def test_env_var(self, mock_realpath, mock_isfile):
        result = ccms._age_resolve_identity()
        self.assertEqual(result, "/custom/key.age")

    def test_not_found(self):
        self.assertIsNone(ccms._age_resolve_identity())

    @mock.patch("ccms.os.chmod")
    @mock.patch("ccms.subprocess.run")
    def test_autocreate(self, mock_run, mock_chmod):
        def fake_run(args, **_kw):
            if args and args[0] == "age-keygen" and len(args) >= 3:
                Path(args[2]).write_text("AGE-SECRET-KEY-xxxxxxxxx")
            return mock.Mock(returncode=0)
        mock_run.side_effect = fake_run
        result = ccms._age_resolve_identity(autocreate=True)
        self.assertIsNotNone(result)
        self.assertIn("identity.age", result)
        self.assertTrue(mock_run.called)

    @mock.patch("ccms.subprocess.run")
    def test_autocreate_fails(self, mock_run):
        mock_run.return_value = mock.Mock(returncode=1)
        result = ccms._age_resolve_identity(autocreate=True)
        self.assertIsNone(result)


class TestLinuxFileResolveIdentity(unittest.TestCase):
    """_linux_file_resolve_identity — resolves openssl key path."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.patch_dir = mock.patch("ccms.CCMS_FILE_KEY_DEFAULT",
                                    os.path.join(self.tmpdir.name,
                                                 "ccms.key"))
        self.patch_dir.start()

    def tearDown(self):
        self.patch_dir.stop()
        self.tmpdir.cleanup()

    def test_not_found(self):
        self.assertIsNone(ccms._linux_file_resolve_identity())

    @mock.patch("ccms.subprocess.run")
    def test_autocreate(self, mock_run):
        mock_run.return_value = mock.Mock(
            returncode=0, stdout="a1b2c3d4e5f6g7h8"
        )
        result = ccms._linux_file_resolve_identity(autocreate=True)
        self.assertIsNotNone(result)
        self.assertIn("ccms.key", result)

    @mock.patch("ccms.subprocess.run")
    def test_autocreate_fails(self, mock_run):
        mock_run.return_value = mock.Mock(returncode=1)
        result = ccms._linux_file_resolve_identity(autocreate=True)
        self.assertIsNone(result)


class TestCredDefaultConfig(unittest.TestCase):
    """cred_default_config — generates platform-appropriate credential config."""

    @mock.patch("ccms.platform.system")
    def test_windows(self, mock_system):
        mock_system.return_value = "Windows"
        cfg = ccms.cred_default_config("my-model")
        self.assertEqual(cfg["type"], "wincred")
        self.assertEqual(cfg["target"], "claude/my-model")

    @mock.patch("ccms.platform.system")
    def test_macos(self, mock_system):
        mock_system.return_value = "Darwin"
        cfg = ccms.cred_default_config("my-model")
        self.assertEqual(cfg["type"], "macos-keychain")
        self.assertEqual(cfg["service"], "claude")
        self.assertEqual(cfg["account"], "my-model")

    @mock.patch("ccms.platform.system")
    @mock.patch("ccms._is_gui_session", return_value=True)
    @mock.patch("ccms.subprocess.run")
    def test_linux_gui_secret_service(self, mock_run, mock_gui, mock_system):
        mock_run.return_value = mock.Mock(returncode=0)
        mock_system.return_value = "Linux"
        cfg = ccms.cred_default_config("my-model")
        self.assertEqual(cfg["type"], "secret-service")
        self.assertIn("label", cfg)
        self.assertIn("key", cfg)

    @mock.patch("ccms.platform.system")
    @mock.patch("ccms._is_gui_session", return_value=False)
    @mock.patch("ccms.shutil.which", return_value=None)  # no age
    @mock.patch("ccms._linux_file_resolve_identity",
                return_value="/home/user/.local/share/ccms/ccms.key")
    def test_linux_headless_fallback(self, mock_id, mock_which, mock_gui,
                                      mock_system):
        mock_system.return_value = "Linux"
        cfg = ccms.cred_default_config("my-model")
        self.assertEqual(cfg["type"], "linux-file")
        self.assertEqual(cfg["identity"],
                         "/home/user/.local/share/ccms/ccms.key")
        self.assertEqual(cfg["keyname"], "my-model")

    @mock.patch("ccms.platform.system")
    @mock.patch("ccms._is_gui_session", return_value=False)
    @mock.patch("ccms.shutil.which", return_value="/usr/bin/age")
    @mock.patch("ccms._age_resolve_identity",
                return_value="/home/user/.local/share/ccms/identity.age")
    def test_linux_headless_age(self, mock_id, mock_which, mock_gui,
                                 mock_system):
        mock_system.return_value = "Linux"
        cfg = ccms.cred_default_config("my-model")
        self.assertEqual(cfg["type"], "age")
        self.assertEqual(cfg["identity"],
                         "/home/user/.local/share/ccms/identity.age")
        self.assertEqual(cfg["keyname"], "my-model")


class TestIsGlobalConfigDir(unittest.TestCase):
    """is_global_config_dir — detects when CWD equals user home."""

    @mock.patch("ccms.os.getcwd")
    @mock.patch("ccms.os.path.expanduser")
    def test_cwd_is_home(self, mock_expanduser, mock_getcwd):
        mock_getcwd.return_value = "/home/user"
        mock_expanduser.return_value = "/home/user"
        self.assertTrue(ccms.is_global_config_dir())

    @mock.patch("ccms.os.getcwd")
    @mock.patch("ccms.os.path.expanduser")
    def test_cwd_is_subdir(self, mock_expanduser, mock_getcwd):
        mock_getcwd.return_value = "/home/user/project"
        mock_expanduser.return_value = "/home/user"
        self.assertFalse(ccms.is_global_config_dir())

    @mock.patch("ccms.os.getcwd")
    @mock.patch("ccms.os.path.expanduser")
    def test_symlink_resolved(self, mock_expanduser, mock_getcwd):
        mock_getcwd.return_value = "/home/user/link"
        mock_expanduser.return_value = "/home/user"
        # realpath resolves the symlink, so they differ
        self.assertFalse(ccms.is_global_config_dir())


class TestLoadSaveCustomModels(unittest.TestCase):
    """load_custom_models / save_custom_models — JSON file I/O."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.models_path = Path(self.tmpdir.name) / "models.json"
        self.legacy_path = Path(self.tmpdir.name) / "custom-models.json"
        self.patch_path = mock.patch("ccms.MODELS_PATH", str(self.models_path))
        self.patch_legacy = mock.patch("ccms.LEGACY_MODELS_PATH", str(self.legacy_path))
        self.patch_path.start()
        self.patch_legacy.start()

    def tearDown(self):
        self.patch_path.stop()
        self.patch_legacy.stop()
        self.tmpdir.cleanup()

    def test_load_missing_file(self):
        self.assertFalse(self.models_path.exists())
        self.assertEqual(ccms.load_custom_models(),
                         {"endpoints": {}, "routing": {}})

    def test_roundtrip(self):
        data = {"endpoints": {"ep1": {"url": "https://example.com", "credential": {},
                                     "models": {"m1": {"modelName": "model-1"}}}},
                "routing": {"opus": "m1", "sonnet": "m1", "haiku": "m1", "subagent": "m1"}}
        ccms.save_custom_models(data)
        loaded = ccms.load_custom_models()
        self.assertEqual(loaded, data)

    def test_trailing_comma_tolerance(self):
        self.models_path.write_text('{"endpoints": {"x": {"url": "x", "credential": {}, "models": {"a": {"modelName": "a"}}}}, "routing": {},}', encoding="utf-8")
        loaded = ccms.load_custom_models()
        self.assertIn("a", loaded["endpoints"]["x"]["models"])

    def test_pretty_print_newline(self):
        ccms.save_custom_models({"k": "v"})
        text = self.models_path.read_text(encoding="utf-8")
        self.assertTrue(text.endswith("\n"))


class TestLoadSaveProjectSettings(unittest.TestCase):
    """load_project_settings / save_project_settings — project JSON I/O."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.settings_path = Path(self.tmpdir.name) / ".claude" / "settings.json"
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        # Patch both the global variable and _project_path helper
        self.patch_project = mock.patch(
            "ccms.PROJECT_SETTINGS_PATH", str(self.settings_path)
        )
        self.patch_project.start()

    def tearDown(self):
        self.patch_project.stop()
        self.tmpdir.cleanup()

    def test_load_missing_file(self):
        self.assertFalse(self.settings_path.exists())
        self.assertEqual(ccms.load_project_settings(), {})

    def test_roundtrip(self):
        data = {"env": {"ANTHROPIC_MODEL": "test-model"}, "apiKeyHelper": "echo ok"}
        ccms.save_project_settings(data)
        loaded = ccms.load_project_settings()
        self.assertEqual(loaded, data)

    def test_creates_directories(self):
        deep_path = Path(self.tmpdir.name) / "a" / "b" / ".claude" / "settings.json"
        with mock.patch("ccms.PROJECT_SETTINGS_PATH", str(deep_path)):
            ccms.save_project_settings({"x": 1})
        self.assertTrue(deep_path.exists())




class TestLoadSaveLocalSettings(unittest.TestCase):
    """load_local_settings / save_local_settings — local JSON I/O."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.settings_path = Path(self.tmpdir.name) / ".claude" / "settings.local.json"
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.patch_local = mock.patch(
            "ccms.LOCAL_SETTINGS_PATH", str(self.settings_path)
        )
        self.patch_local.start()

    def tearDown(self):
        self.patch_local.stop()
        self.tmpdir.cleanup()

    def test_load_missing_file(self):
        self.assertFalse(self.settings_path.exists())
        self.assertEqual(ccms.load_local_settings(), {})

    def test_roundtrip(self):
        data = {"env": {"ANTHROPIC_MODEL": "test-model"}, "apiKeyHelper": "echo ok"}
        ccms.save_local_settings(data)
        loaded = ccms.load_local_settings()
        self.assertEqual(loaded, data)

    def test_creates_directories(self):
        deep_path = Path(self.tmpdir.name) / "a" / "b" / ".claude" / "settings.local.json"
        with mock.patch("ccms.LOCAL_SETTINGS_PATH", str(deep_path)):
            ccms.save_local_settings({"x": 1})
        self.assertTrue(deep_path.exists())


class TestLoadMergedSettings(unittest.TestCase):
    """load_merged_ccms_settings — local over project layering."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.project_path = Path(self.tmpdir.name) / ".claude" / "settings.json"
        self.local_path = Path(self.tmpdir.name) / ".claude" / "settings.local.json"
        for p in [self.project_path, self.local_path]:
            p.parent.mkdir(parents=True, exist_ok=True)
        self.patch_project = mock.patch(
            "ccms.PROJECT_SETTINGS_PATH", str(self.project_path)
        )
        self.patch_local = mock.patch(
            "ccms.LOCAL_SETTINGS_PATH", str(self.local_path)
        )
        self.patch_project.start()
        self.patch_local.start()

    def tearDown(self):
        self.patch_project.stop()
        self.patch_local.stop()
        self.tmpdir.cleanup()

    def test_neither_exists(self):
        self.assertEqual(ccms.load_merged_ccms_settings(), {})

    def test_only_project_has_settings(self):
        self.project_path.write_text(
            '{"env": {"ANTHROPIC_MODEL": "claude-3"}, "apiKeyHelper": "echo"}'
        )
        merged = ccms.load_merged_ccms_settings()
        self.assertEqual(merged["env"]["ANTHROPIC_MODEL"], "claude-3")
        self.assertEqual(merged["apiKeyHelper"], "echo")

    def test_only_local_has_settings(self):
        self.local_path.write_text(
            '{"env": {"ANTHROPIC_MODEL": "claude-4"}, "apiKeyHelper": "ps1"}'
        )
        merged = ccms.load_merged_ccms_settings()
        self.assertEqual(merged["env"]["ANTHROPIC_MODEL"], "claude-4")
        self.assertEqual(merged["apiKeyHelper"], "ps1")

    def test_local_overrides_project(self):
        self.project_path.write_text(
            '{"env": {"ANTHROPIC_MODEL": "claude-3", "ANTHROPIC_BASE_URL": "url1"}}'
        )
        self.local_path.write_text(
            '{"env": {"ANTHROPIC_MODEL": "claude-4"}}'
        )
        merged = ccms.load_merged_ccms_settings()
        self.assertEqual(merged["env"]["ANTHROPIC_MODEL"], "claude-4")
        self.assertEqual(merged["env"]["ANTHROPIC_BASE_URL"], "url1")

    def test_local_merges_env_additively(self):
        self.project_path.write_text('{"env": {"VAR_A": "a"}}')
        self.local_path.write_text('{"env": {"VAR_B": "b"}}')
        merged = ccms.load_merged_ccms_settings()
        self.assertEqual(merged["env"].get("VAR_A"), "a")
        self.assertEqual(merged["env"].get("VAR_B"), "b")


class TestMigrateProjectSettings(unittest.TestCase):
    """_migrate_ccms_fields_from_project — cleanup old settings.json."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.project_path = Path(self.tmpdir.name) / ".claude" / "settings.json"
        self.project_path.parent.mkdir(parents=True, exist_ok=True)
        self.patch_project = mock.patch(
            "ccms.PROJECT_SETTINGS_PATH", str(self.project_path)
        )
        self.patch_project.start()

    def tearDown(self):
        self.patch_project.stop()
        self.tmpdir.cleanup()

    def test_removes_ccms_fields(self):
        self.project_path.write_text(json.dumps({
            "env": {
                "ANTHROPIC_BASE_URL": "https://api.example.com",
                "ANTHROPIC_MODEL": "claude-3",
                "CCMS_MODEL_ALIAS": "my-model",
                "CLAUDE_CODE_SUBAGENT_MODEL": "sub-model",
                "USER_VAR": "keep-me"
            },
            "apiKeyHelper": "echo old",
            "other_key": "keep-me-too"
        }))
        ccms._migrate_ccms_fields_from_project()
        remaining = json.loads(self.project_path.read_text())
        self.assertNotIn("ANTHROPIC_BASE_URL", remaining.get("env", {}))
        self.assertNotIn("ANTHROPIC_MODEL", remaining.get("env", {}))
        self.assertNotIn("CCMS_MODEL_ALIAS", remaining.get("env", {}))
        self.assertNotIn("CLAUDE_CODE_SUBAGENT_MODEL", remaining.get("env", {}))
        self.assertNotIn("apiKeyHelper", remaining)
        self.assertEqual(remaining.get("env", {}).get("USER_VAR"), "keep-me")
        self.assertEqual(remaining.get("other_key"), "keep-me-too")

    def test_removes_empty_file(self):
        self.project_path.write_text(json.dumps({
            "env": {"CCMS_MODEL_ALIAS": "m"},
            "apiKeyHelper": "echo"
        }))
        ccms._migrate_ccms_fields_from_project()
        self.assertFalse(self.project_path.exists())

    def test_noop_when_no_ccms_fields(self):
        self.project_path.write_text(json.dumps({"other_key": "value"}))
        ccms._migrate_ccms_fields_from_project()
        self.assertEqual(
            json.loads(self.project_path.read_text()),
            {"other_key": "value"}
        )

    def test_noop_when_file_missing(self):
        ccms._migrate_ccms_fields_from_project()


class TestHelperScriptReference(unittest.TestCase):
    """Generated helper scripts reference settings.local.json."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.helper_path = Path(self.tmpdir.name) / ".claude" / "get-sk.sh"
        self.helper_path.parent.mkdir(parents=True, exist_ok=True)
        self.patch_helper = mock.patch(
            "ccms.HELPER_SCRIPT_PATH", str(self.helper_path)
        )
        self.patch_helper.start()
        self.patch_project = mock.patch(
            "ccms.PROJECT_SETTINGS_PATH",
            str(Path(self.tmpdir.name) / ".claude" / "settings.json")
        )
        self.patch_project.start()
        self.patch_local = mock.patch(
            "ccms.LOCAL_SETTINGS_PATH",
            str(Path(self.tmpdir.name) / ".claude" / "settings.local.json")
        )
        self.patch_local.start()

    def tearDown(self):
        self.patch_helper.stop()
        self.patch_project.stop()
        self.patch_local.stop()

    def test_ps1_references_settings_local_json(self):
        ccms._generate_helper_scripts()
        ps1_path = self.helper_path.parent / "get-sk.ps1"
        content = ps1_path.read_text(encoding="utf-8")
        self.assertIn("settings.local.json", content)

    def test_sh_references_settings_local_json(self):
        ccms._generate_helper_scripts()
        sh_path = self.helper_path.parent / "get-sk.sh"
        content = sh_path.read_text(encoding="utf-8")
        self.assertIn("settings.local.json", content)


class TestDetectEnvApiKeyConflict(unittest.TestCase):
    """detect_env_api_key_conflict — finds ANTHROPIC_API_KEY in settings."""

    def test_no_conflict(self):
        with mock.patch("ccms.load_project_settings", return_value={}), \
             mock.patch("ccms.load_user_settings", return_value={}), \
             mock.patch("ccms.load_local_settings", return_value={}):
            self.assertEqual(ccms.detect_env_api_key_conflict(), [])

    def test_local_level_conflict(self):
        with mock.patch("ccms.load_project_settings", return_value={}), \
             mock.patch("ccms.load_user_settings", return_value={}), \
             mock.patch(
                 "ccms.load_local_settings",
                 return_value={"env": {"ANTHROPIC_API_KEY": "sk-local"}}
             ):
            conflicts = ccms.detect_env_api_key_conflict()
            self.assertEqual(len(conflicts), 1)
            self.assertIn("local", conflicts[0][0].lower())

    def test_project_level_conflict(self):
        with mock.patch(
            "ccms.load_project_settings",
            return_value={"env": {"ANTHROPIC_API_KEY": "sk-xxx"}}
        ), mock.patch("ccms.load_user_settings", return_value={}), \
             mock.patch("ccms.load_local_settings", return_value={}):
            conflicts = ccms.detect_env_api_key_conflict()
            self.assertEqual(len(conflicts), 1)
            self.assertIn("项目", conflicts[0][0])

    def test_user_level_conflict(self):
        with mock.patch("ccms.load_project_settings", return_value={}), \
             mock.patch(
                 "ccms.load_user_settings",
                 return_value={"env": {"ANTHROPIC_API_KEY": "sk-yyy"}}
             ), mock.patch("ccms.load_local_settings", return_value={}):
            conflicts = ccms.detect_env_api_key_conflict()
            self.assertEqual(len(conflicts), 1)
            self.assertIn("用户", conflicts[0][0])

    def test_all_levels_conflict(self):
        with mock.patch(
            "ccms.load_project_settings",
            return_value={"env": {"ANTHROPIC_API_KEY": "sk-proj"}}
        ), mock.patch(
            "ccms.load_user_settings",
            return_value={"env": {"ANTHROPIC_API_KEY": "sk-user"}}
        ), mock.patch(
            "ccms.load_local_settings",
            return_value={"env": {"ANTHROPIC_API_KEY": "sk-local"}}
        ):
            conflicts = ccms.detect_env_api_key_conflict()
            self.assertEqual(len(conflicts), 3)


class TestInferEndpointName(unittest.TestCase):
    """_infer_endpoint_name — URL → endpoint 名称推断"""

    def test_anthropic(self):
        name = ccms._infer_endpoint_name("https://api.anthropic.com/v1")
        self.assertEqual(name, "anthropic")

    def test_custom_gateway(self):
        name = ccms._infer_endpoint_name("https://my-gateway.com/v1")
        self.assertEqual(name, "my-gateway")

    def test_ip_address(self):
        name = ccms._infer_endpoint_name("http://10.0.0.1:8080/v1")
        self.assertEqual(name, "10.0.0.1")

    def test_empty_url(self):
        name = ccms._infer_endpoint_name("")
        self.assertEqual(name, "default")


class TestImportLegacy(unittest.TestCase):
    """_import_legacy — v1 扁平格式 → v2 三层格式"""

    @mock.patch("ccms.load_merged_ccms_settings", return_value={})
    def test_single_model(self, _mock_settings):
        v1 = {"my-model": {"url": "https://api.example.com/v1", "modelName": "test-model"}}
        result = ccms._import_legacy(v1)
        ep_name = list(result["endpoints"].keys())[0]
        self.assertIn("my-model", result["endpoints"][ep_name]["models"])
        self.assertEqual(result["endpoints"][ep_name]["models"]["my-model"]["modelName"], "test-model")
        self.assertEqual(result["routing"]["opus"], "my-model")

    @mock.patch("ccms.load_merged_ccms_settings", return_value={})
    def test_same_url_deduplication(self, _mock_settings):
        v1 = {
            "m1": {"url": "https://api.example.com/v1", "modelName": "model-1"},
            "m2": {"url": "https://api.example.com/v1", "modelName": "model-2"},
        }
        result = ccms._import_legacy(v1)
        self.assertEqual(len(result["endpoints"]), 1)
        ep_name = list(result["endpoints"].keys())[0]
        self.assertIn("m1", result["endpoints"][ep_name]["models"])
        self.assertIn("m2", result["endpoints"][ep_name]["models"])

    @mock.patch("ccms.load_merged_ccms_settings", return_value={})
    def test_different_urls(self, _mock_settings):
        v1 = {
            "a": {"url": "https://api.one.com/v1", "modelName": "a-model"},
            "b": {"url": "https://api.two.com/v1", "modelName": "b-model"},
        }
        result = ccms._import_legacy(v1)
        self.assertEqual(len(result["endpoints"]), 2)

    @mock.patch("ccms.load_merged_ccms_settings")
    def test_uses_active_alias_from_settings(self, mock_settings):
        mock_settings.return_value = {"env": {"CCMS_MODEL_ALIAS": "m2"}}
        v1 = {
            "m1": {"url": "https://api.example.com/v1", "modelName": "model-1"},
            "m2": {"url": "https://api.example.com/v1", "modelName": "model-2"},
        }
        result = ccms._import_legacy(v1)
        self.assertEqual(result["routing"]["opus"], "m2")

    @mock.patch("ccms.load_merged_ccms_settings", return_value={})
    def test_empty_v1(self, _mock_settings):
        result = ccms._import_legacy({})
        self.assertEqual(result["endpoints"], {})
        self.assertEqual(result["routing"], {})

    @mock.patch("ccms.load_merged_ccms_settings", return_value={})
    @mock.patch("ccms.cred_store")
    def test_migrates_inline_sk(self, mock_cred_store, _mock_settings):
        v1 = {"m1": {"url": "https://api.example.com/v1", "sk": "sk-secret"}}
        result = ccms._import_legacy(v1)
        ep_name = list(result["endpoints"].keys())[0]
        self.assertIn("m1", result["endpoints"][ep_name]["models"])
        self.assertIn("credential", result["endpoints"][ep_name])


class TestAccessors(unittest.TestCase):
    """_model_flat_config, _upsert_model, _delete_model — v2 数据操作"""

    def _empty_v2(self):
        return {"endpoints": {}, "routing": {}}

    def test_flat_config_resolves_endpoint(self):
        v2 = {
            "endpoints": {"ep1": {"url": "https://api.example.com/v1",
                                   "credential": {"type": "wincred"},
                                   "models": {"m1": {"modelName": "test-model"}}}},
            "routing": {},
        }
        cfg = ccms._model_flat_config(v2, "m1")
        self.assertEqual(cfg["url"], "https://api.example.com/v1")
        self.assertEqual(cfg["modelName"], "test-model")
        self.assertEqual(cfg["credential"], {"type": "wincred"})

    def test_flat_config_missing_model(self):
        v2 = self._empty_v2()
        cfg = ccms._model_flat_config(v2, "nonexistent")
        self.assertEqual(cfg["url"], "")

    @mock.patch("ccms.cred_store")
    def test_upsert_new_model(self, _mock):
        v2 = self._empty_v2()
        ep = ccms._upsert_model(v2, "m1", "https://api.example.com/v1", "test-model",
                                {"type": "wincred"})
        self.assertIn("m1", v2["endpoints"][ep]["models"])
        self.assertIn(ep, v2["endpoints"])

    @mock.patch("ccms.cred_store")
    def test_upsert_auto_populates_routing(self, _mock):
        v2 = self._empty_v2()
        ccms._upsert_model(v2, "m1", "https://api.example.com/v1", "test-model",
                          {"type": "wincred"})
        self.assertEqual(v2["routing"]["opus"], "m1")

    def test_delete_model(self):
        v2 = {
            "endpoints": {"ep1": {"url": "https://x.com", "credential": {},
                                   "models": {"m1": {"modelName": "m1"}}}},
            "routing": {"opus": "m1", "sonnet": "m1"},
        }
        self.assertTrue(ccms._delete_model(v2, "m1"))
        self.assertNotIn("m1", v2["endpoints"]["ep1"]["models"])
        self.assertNotIn("opus", v2["routing"])

    def test_delete_model_not_found(self):
        v2 = self._empty_v2()
        self.assertFalse(ccms._delete_model(v2, "nonexistent"))


class TestWriteModelToProject(unittest.TestCase):
    """write_model_to_project — 4 路 env var 写入"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.local_path = Path(self.tmpdir.name) / "settings.local.json"
        self.project_path = Path(self.tmpdir.name) / "settings.json"
        self.helper_dir = Path(self.tmpdir.name)
        self.patches = [
            mock.patch("ccms.LOCAL_SETTINGS_PATH", str(self.local_path)),
            mock.patch("ccms.PROJECT_SETTINGS_PATH", str(self.project_path)),
            mock.patch("ccms.HELPER_SCRIPT_PATH", str(self.helper_dir / "get-sk.sh")),
            mock.patch("ccms._detect_platform", return_value="windows"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmpdir.cleanup()

    def _make_v2(self, routing=None):
        v2 = {
            "endpoints": {"ep1": {"url": "https://api.example.com/v1",
                                   "credential": {"type": "wincred"},
                                   "models": {
                                       "m1": {"modelName": "model-one"},
                                       "m2": {"modelName": "model-two"},
                                   }}},
            "routing": routing or {},
        }
        return v2

    def test_writes_four_role_env_vars(self):
        v2 = self._make_v2({"opus": "m1", "sonnet": "m2", "haiku": "m1", "subagent": "m2"})
        cfg = ccms._model_flat_config(v2, "m1")
        ccms.write_model_to_project("m1", cfg, v2)

        with open(self.local_path) as f:
            written = json.load(f)
        env = written["env"]
        self.assertEqual(env["ANTHROPIC_DEFAULT_OPUS_MODEL"], "model-one")
        self.assertEqual(env["ANTHROPIC_DEFAULT_SONNET_MODEL"], "model-two")
        self.assertEqual(env["ANTHROPIC_DEFAULT_HAIKU_MODEL"], "model-one")
        self.assertEqual(env["CLAUDE_CODE_SUBAGENT_MODEL"], "model-two")

    def test_writes_backward_compat_fields(self):
        v2 = self._make_v2({"opus": "m1", "sonnet": "m1", "haiku": "m1", "subagent": "m1"})
        cfg = ccms._model_flat_config(v2, "m1")
        ccms.write_model_to_project("m1", cfg, v2)

        with open(self.local_path) as f:
            written = json.load(f)
        env = written["env"]
        self.assertEqual(env["ANTHROPIC_MODEL"], "model-one")
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "https://api.example.com/v1")
        self.assertEqual(env["CCMS_MODEL_ALIAS"], "m1")

    def test_no_routing_fallback_all_same(self):
        """无路由表时 4 路全部指向当前模型"""
        v2 = self._make_v2({})
        cfg = ccms._model_flat_config(v2, "m2")
        ccms.write_model_to_project("m2", cfg, v2)

        with open(self.local_path) as f:
            written = json.load(f)
        env = written["env"]
        self.assertEqual(env["ANTHROPIC_DEFAULT_OPUS_MODEL"], "model-two")
        self.assertEqual(env["ANTHROPIC_DEFAULT_SONNET_MODEL"], "model-two")
        self.assertEqual(env["ANTHROPIC_DEFAULT_HAIKU_MODEL"], "model-two")
        self.assertEqual(env["CLAUDE_CODE_SUBAGENT_MODEL"], "model-two")


class TestGetCurrentAliasV2(unittest.TestCase):
    """get_current_alias — v2 数据模型兼容"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.patches = [
            mock.patch("ccms.PROJECT_SETTINGS_PATH",
                       str(Path(self.tmpdir.name) / "settings.json")),
            mock.patch("ccms.LOCAL_SETTINGS_PATH",
                       str(Path(self.tmpdir.name) / "settings.local.json")),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmpdir.cleanup()

    def test_finds_alias_by_tag(self):
        ccms.save_local_settings({"env": {"ANTHROPIC_MODEL": "model-one", "CCMS_MODEL_ALIAS": "m1"}})
        v2 = {
            "endpoints": {"ep1": {"url": "https://x.com", "credential": {},
                                   "models": {"m1": {"modelName": "model-one"}}}},
            "routing": {},
        }
        self.assertEqual(ccms.get_current_alias(v2), "m1")

    def test_returns_none_no_model(self):
        v2 = {"endpoints": {}, "routing": {}}
        self.assertIsNone(ccms.get_current_alias(v2))

    def test_fallback_by_modelname_reverse_lookup(self):
        ccms.save_local_settings({"env": {"ANTHROPIC_MODEL": "model-two"}})
        v2 = {
            "endpoints": {"ep1": {"url": "https://x.com", "credential": {},
                                   "models": {
                                       "m1": {"modelName": "model-one"},
                                       "m2": {"modelName": "model-two"},
                                   }}},
            "routing": {},
        }
        self.assertEqual(ccms.get_current_alias(v2), "m2")


if __name__ == "__main__":
    unittest.main()
