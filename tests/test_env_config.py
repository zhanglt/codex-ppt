from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from env_config import ENV_FIELDS, NEBULA_OPTIONS, TEAMOROUTER_OPTIONS, load_env_values, read_env_file
from image_providers.factory import create_image_provider
from image_providers.nebula import NebulaImageProvider
from image_providers.openai_compatible import OpenAICompatibleImageProvider


CONFIG = """IMAGE_PROVIDER=nebula
OPENAI_API_KEY=legacy-secret
[nebula]
NEBULA_API_KEY="nebula secret # with spaces"
NEBULA_BASE_URL=https://img-api.apinebula.ai/v1
NEBULA_PPT_IMAGE_MODEL=gpt-image-2-4k
NEBULA_SIZE=3840x2160
NEBULA_QUALITY=high
[openai]
OPENAI_API_KEY=openai-secret
OPENAI_BASE_URL=https://api.openai.com/v1
CODEX_PPT_IMAGE_MODEL=gpt-image-2.5-flare
[teamorouter]
OPENAI_API_KEY=teamorouter-secret
CODEX_PPT_IMAGE_MODEL=gpt-image-2.5-flare
TEAMOROUTER_SIZE=3840x2160
TEAMOROUTER_QUALITY=high
"""


class ProviderConfigTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.path = self.home / ".env"
        self.path.write_text(CONFIG, encoding="utf-8")
        self.env = {key: value for key, value in os.environ.items()
                    if key not in (*ENV_FIELDS, *NEBULA_OPTIONS, *TEAMOROUTER_OPTIONS,
                                   "IMAGE_PROVIDER", "CODEX_PPT_HOME")}
        self.env["CODEX_PPT_HOME"] = str(self.home)

    def run_cli(self, script, *args, env=None):
        return subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "scripts" / script), *args],
                              env=env or self.env, capture_output=True, encoding="utf-8")

    def test_only_selected_group_is_loaded_and_routes_adapter(self):
        for provider, expected_class in (("nebula", NebulaImageProvider), ("openai", OpenAICompatibleImageProvider),
                                         ("teamorouter", OpenAICompatibleImageProvider)):
            with self.subTest(provider=provider):
                values = load_env_values(self.path, {"IMAGE_PROVIDER": provider})
                self.assertNotEqual(values["OPENAI_API_KEY"], "legacy-secret")
                adapter = create_image_provider(api_key=values["OPENAI_API_KEY"], base_url=values["OPENAI_BASE_URL"])
                self.assertIsInstance(adapter, expected_class)
        self.assertEqual(load_env_values(self.path, {})["OPENAI_API_KEY"], "nebula secret # with spaces")
        values = load_env_values(self.path, {"IMAGE_PROVIDER": "teamorouter"})
        self.assertEqual(values["OPENAI_BASE_URL"], "https://api.teamorouter.com/v1")
        self.assertEqual(values["CODEX_PPT_IMAGE_MODEL"], "gpt-image-2.5-flare")
        self.assertEqual(values["TEAMOROUTER_SIZE"], "3840x2160")
        self.assertEqual(values["TEAMOROUTER_QUALITY"], "high")

    def test_process_overrides_and_legacy_config(self):
        values = load_env_values(self.path, {"IMAGE_PROVIDER": "openai", "OPENAI_API_KEY": "process-secret"})
        self.assertEqual(values["OPENAI_API_KEY"], "process-secret")
        self.assertEqual(values["CODEX_PPT_IMAGE_MODEL"], "gpt-image-2.5-flare")
        self.path.write_text("OPENAI_API_KEY=legacy\nCODEX_PPT_IMAGE_MODEL=gpt-image-2\n", encoding="utf-8")
        self.assertEqual(load_env_values(self.path, {})["OPENAI_API_KEY"], "legacy")

    def test_nebula_size_quality_precedence_validation_and_isolation(self):
        self.path.write_text(CONFIG.replace("NEBULA_QUALITY=high", "NEBULA_QUALITY=low"), encoding="utf-8")
        command = ("generate", "--prompt", "test", "--dry-run")
        result = self.run_cli("image_gen.py", *command)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["quality"], "low")
        env = {**self.env, "NEBULA_QUALITY": "medium", "NEBULA_SIZE": "3840x2160"}
        result = self.run_cli("image_gen.py", *command, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["quality"], "medium")
        result = self.run_cli("image_gen.py", *command, "--quality", "high", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["quality"], "high")
        env["NEBULA_SIZE"] = "2560x1440"
        result = self.run_cli("image_gen.py", *command, env=env)
        self.assertNotEqual(result.returncode, 0)
        result = self.run_cli("image_gen.py", *command, "--size", "3840x2160", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_cli("image_gen.py", *command, env={**env, "IMAGE_PROVIDER": "openai"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["quality"], "medium")
        self.assertNotIn("NEBULA_QUALITY", load_env_values(self.path, {"IMAGE_PROVIDER": "openai"}))
        self.path.write_text(CONFIG.replace("NEBULA_QUALITY=high", "NEBULA_QUALITY=invalid"), encoding="utf-8")
        self.assertNotEqual(self.run_cli("image_gen.py", *command).returncode, 0)

    def test_nebula_aliases_override_legacy_fields_and_config_migrates(self):
        legacy = CONFIG.replace("NEBULA_API_KEY", "OPENAI_API_KEY").replace(
            "NEBULA_BASE_URL", "OPENAI_BASE_URL").replace("NEBULA_PPT_IMAGE_MODEL", "CODEX_PPT_IMAGE_MODEL")
        self.path.write_text(legacy, encoding="utf-8")
        self.assertEqual(load_env_values(self.path, {})["OPENAI_API_KEY"], "nebula secret # with spaces")
        result = self.run_cli("codex_ppt_runtime.py", "config", "--api-key", "new-secret",
                              "--model", "gpt-image-2-4k")
        self.assertEqual(result.returncode, 0, result.stderr)
        fields = read_env_file(self.path)[1]["nebula"]
        self.assertEqual(fields["NEBULA_API_KEY"], "new-secret")
        self.assertTrue(all(key not in fields for key in ENV_FIELDS))
        self.assertNotIn("new-secret", result.stdout + result.stderr)
        fields_text = self.path.read_text(encoding="utf-8").replace(
            "[nebula]", "[nebula]\nOPENAI_API_KEY=old-secret")
        self.path.write_text(fields_text, encoding="utf-8")
        self.assertEqual(load_env_values(self.path, {})["OPENAI_API_KEY"], "new-secret")
        result = self.run_cli("codex_ppt_runtime.py", "config", "--clear-base-url")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("NEBULA_BASE_URL", read_env_file(self.path)[1]["nebula"])
        self.assertEqual(load_env_values(self.path, {})["OPENAI_BASE_URL"], "https://img-api.apinebula.ai/v1")

    def test_missing_fields_do_not_inherit_other_credentials(self):
        self.path.write_text("IMAGE_PROVIDER=nebula\nOPENAI_API_KEY=legacy\n[nebula]\n[openai]\nOPENAI_API_KEY=other\n", encoding="utf-8")
        values = load_env_values(self.path, {})
        self.assertNotIn("OPENAI_API_KEY", values)
        self.assertEqual(values["OPENAI_BASE_URL"], "https://img-api.apinebula.ai/v1")

    def test_invalid_selection_fails_without_exposing_secrets(self):
        for content in (CONFIG.replace("IMAGE_PROVIDER=nebula", "IMAGE_PROVIDER=unknown"),
                        CONFIG.replace("IMAGE_PROVIDER=nebula", "IMAGE_PROVIDER=atlascloud"),
                        CONFIG.replace("IMAGE_PROVIDER=nebula\n", ""),
                        CONFIG.replace("[nebula]", "[nebula")):
            with self.subTest(content=content[:30]):
                self.path.write_text(content, encoding="utf-8")
                result = self.run_cli("image_gen.py", "generate", "--prompt", "test", "--dry-run")
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Traceback", result.stderr)
                self.assertNotIn("secret", result.stdout + result.stderr)

    def test_generate_edit_batch_and_process_selection(self):
        image = self.home / "input.png"
        from PIL import Image
        Image.new("RGB", (32, 32)).save(image)
        jobs = self.home / "jobs.jsonl"
        jobs.write_text('{"prompt":"test"}\n', encoding="utf-8")
        commands = (("generate", "--prompt", "test"),
                    ("edit", "--prompt", "test", "--image", str(image)),
                    ("generate-batch", "--input", str(jobs), "--out-dir", str(self.home / "output")))
        for command in commands:
            result = self.run_cli("image_gen.py", *command, "--dry-run")
            self.assertEqual(result.returncode, 0, result.stderr)
            preview = json.loads(result.stdout)
            self.assertEqual(preview["model"], "gpt-image-2-4k")
            self.assertEqual(preview["size"], "3840x2160")
            self.assertNotIn("secret", result.stdout + result.stderr)
        env = {**self.env, "IMAGE_PROVIDER": "openai"}
        result = self.run_cli("image_gen.py", "generate", "--prompt", "test", "--dry-run", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["size"], "2560x1440")
        result = self.run_cli("image_gen.py", "generate", "--prompt", "test", "--dry-run", "--model", "gpt-image-2", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["model"], "gpt-image-2")

    def test_provider_url_mismatch_rejected_by_cli_and_doctor(self):
        env = {**self.env, "OPENAI_BASE_URL": "https://example.test/v1"}
        for script, command in (("image_gen.py", ("generate", "--prompt", "test", "--dry-run")),
                                ("codex_ppt_runtime.py", ("doctor",))):
            result = self.run_cli(script, *command, env=env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("适配器不一致", result.stderr)
        result = self.run_cli("image_gen.py", "generate", "--prompt", "test", "--dry-run",
                              "--base-url", "https://example.test/v1")
        self.assertNotEqual(result.returncode, 0)

    def test_teamorouter_generate_edit_batch_and_config(self):
        from PIL import Image
        image = self.home / "input.png"
        Image.new("RGB", (32, 32)).save(image)
        jobs = self.home / "jobs.jsonl"
        jobs.write_text('{"prompt":"test"}\n', encoding="utf-8")
        env = {**self.env, "IMAGE_PROVIDER": "teamorouter"}
        commands = (("generate", "--prompt", "test"),
                    ("edit", "--prompt", "test", "--image", str(image)),
                    ("generate-batch", "--input", str(jobs), "--out-dir", str(self.home / "output")))
        for command in commands:
            result = self.run_cli("image_gen.py", *command, "--dry-run", env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            preview = json.loads(result.stdout)
            self.assertEqual(preview["model"], "gpt-image-2.5-flare")
            self.assertEqual(preview["size"], "3840x2160")
            self.assertEqual(preview["quality"], "high")
            self.assertNotIn("teamorouter-secret", result.stdout + result.stderr)
        override_env = {**env, "TEAMOROUTER_SIZE": "2048x2048", "TEAMOROUTER_QUALITY": "low"}
        result = self.run_cli("image_gen.py", "generate", "--prompt", "test", "--dry-run", env=override_env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["size"], "2048x2048")
        self.assertEqual(json.loads(result.stdout)["quality"], "low")
        result = self.run_cli("image_gen.py", "generate", "--prompt", "test", "--dry-run",
                              "--size", "1024x1024", "--quality", "medium", env=override_env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["size"], "1024x1024")
        self.assertEqual(json.loads(result.stdout)["quality"], "medium")
        result = self.run_cli("image_gen.py", "generate", "--prompt", "test", "--dry-run",
                              env={**override_env, "IMAGE_PROVIDER": "openai"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["size"], "2560x1440")
        self.assertEqual(json.loads(result.stdout)["quality"], "medium")
        result = self.run_cli("codex_ppt_runtime.py", "config", "--provider", "teamorouter")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(read_env_file(self.path)[0]["IMAGE_PROVIDER"], "teamorouter")
        self.assertEqual(read_env_file(self.path)[1]["nebula"]["NEBULA_API_KEY"],
                         "nebula secret # with spaces")

    def test_config_updates_selected_group_and_preserves_others(self):
        before_root, before_sections = read_env_file(self.path)
        result = self.run_cli("codex_ppt_runtime.py", "config", "--provider", "openai", "--model", "gpt-image-2")
        self.assertEqual(result.returncode, 0, result.stderr)
        root, sections = read_env_file(self.path)
        self.assertEqual(root["IMAGE_PROVIDER"], "openai")
        self.assertEqual(root["OPENAI_API_KEY"], before_root["OPENAI_API_KEY"])
        self.assertEqual(sections["nebula"], before_sections["nebula"])
        self.assertEqual(sections["openai"]["OPENAI_API_KEY"], "openai-secret")
        self.assertEqual(sections["openai"]["CODEX_PPT_IMAGE_MODEL"], "gpt-image-2")
        result = self.run_cli("codex_ppt_runtime.py", "config", "--clear-base-url")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("OPENAI_BASE_URL", read_env_file(self.path)[1]["openai"])
        self.assertEqual(load_env_values(self.path, {})["OPENAI_BASE_URL"], "https://api.openai.com/v1")
        self.assertFalse(self.path.read_bytes().startswith(b"\xef\xbb\xbf"))


if __name__ == "__main__":
    unittest.main()
