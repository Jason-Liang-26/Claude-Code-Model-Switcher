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
            "ds-v3": {"url": "https://api.example.com", "modelName": "deepseek-v3"},
            "gpt4o": {"url": "https://api.openai.com", "modelName": "gpt-4o"},
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
    def test_linux_gui_secret_service(self, mock_gui, mock_system):
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
        self.models_path = Path(self.tmpdir.name) / "custom-models.json"
        self.patch_path = mock.patch(
            "ccms.CUSTOM_MODELS_PATH", str(self.models_path)
        )
        self.patch_path.start()

    def tearDown(self):
        self.patch_path.stop()
        self.tmpdir.cleanup()

    def test_load_missing_file(self):
        self.assertFalse(self.models_path.exists())
        self.assertEqual(ccms.load_custom_models(), {})

    def test_roundtrip(self):
        data = {"alias1": {"url": "https://example.com", "modelName": "model-1"}}
        ccms.save_custom_models(data)
        loaded = ccms.load_custom_models()
        self.assertEqual(loaded, data)

    def test_trailing_comma_tolerance(self):
        self.models_path.write_text('{"a": {"url": "x",},}', encoding="utf-8")
        loaded = ccms.load_custom_models()
        self.assertIn("a", loaded)
        self.assertEqual(loaded["a"]["url"], "x")

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


class TestMigrateModels(unittest.TestCase):
    """migrate_models — upgrades old-format configs."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.patch_path = mock.patch(
            "ccms.CUSTOM_MODELS_PATH",
            str(Path(self.tmpdir.name) / "custom-models.json")
        )
        self.patch_path.start()

    def tearDown(self):
        self.patch_path.stop()
        self.tmpdir.cleanup()

    @mock.patch("ccms.cred_store")
    def test_adds_missing_modelName(self, mock_cred_store):
        models = {"alias1": {"url": "https://example.com"}}
        result = ccms.migrate_models(models)
        self.assertEqual(result["alias1"]["modelName"], "alias1")

    @mock.patch("ccms.cred_store")
    def test_migrates_plaintext_sk(self, mock_cred_store):
        models = {"alias1": {"url": "x", "sk": "secret-key"}}
        result = ccms.migrate_models(models)
        self.assertNotIn("sk", result["alias1"])
        self.assertIn("credential", result["alias1"])
        mock_cred_store.assert_called_once()
        # Verify the sk was passed to cred_store
        _, sk_arg = mock_cred_store.call_args[0]
        self.assertEqual(sk_arg, "secret-key")

    @mock.patch("ccms.cred_store")
    def test_skips_when_credential_exists(self, mock_cred_store):
        models = {"alias1": {"url": "x", "sk": "secret", "credential": {"type": "wincred"}}}
        result = ccms.migrate_models(models)
        # credential already present, sk should stay? Actually the code checks "credential" not in cfg
        # Wait, let me re-read the code...
        # if "sk" in cfg and "credential" not in cfg:
        # So if credential exists, sk is NOT migrated. It remains in the dict.
        self.assertIn("sk", result["alias1"])
        mock_cred_store.assert_not_called()


class TestDetectEnvApiKeyConflict(unittest.TestCase):
    """detect_env_api_key_conflict — finds ANTHROPIC_API_KEY in settings."""

    def test_no_conflict(self):
        with mock.patch("ccms.load_project_settings", return_value={}), \
             mock.patch("ccms.load_user_settings", return_value={}):
            self.assertEqual(ccms.detect_env_api_key_conflict(), [])

    def test_project_level_conflict(self):
        with mock.patch(
            "ccms.load_project_settings",
            return_value={"env": {"ANTHROPIC_API_KEY": "sk-xxx"}}
        ), mock.patch("ccms.load_user_settings", return_value={}):
            conflicts = ccms.detect_env_api_key_conflict()
            self.assertEqual(len(conflicts), 1)
            self.assertEqual(conflicts[0][0], "项目")

    def test_user_level_conflict(self):
        with mock.patch("ccms.load_project_settings", return_value={}), \
             mock.patch(
                 "ccms.load_user_settings",
                 return_value={"env": {"ANTHROPIC_API_KEY": "sk-yyy"}}
             ):
            conflicts = ccms.detect_env_api_key_conflict()
            self.assertEqual(len(conflicts), 1)
            self.assertEqual(conflicts[0][0], "用户")


if __name__ == "__main__":
    unittest.main()
