from __future__ import annotations

import asyncio
import base64
from email.parser import BytesParser
from email.policy import default
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from urllib.error import HTTPError
import warnings

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from image_providers.atlascloud import AtlasCloudImageProvider
from image_providers.factory import create_image_provider
from image_providers.nebula import NebulaImageProvider, nebula_payload
from image_providers.openai_compatible import OpenAICompatibleImageProvider
import codex_ppt_runtime
import image_gen


def image_bytes(fmt="JPEG", size=(3840, 2160)):
    output = BytesIO()
    Image.new("RGB", size, (20, 60, 100)).save(output, format=fmt)
    return output.getvalue()


class Response(BytesIO):
    status = 200


class NebulaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = image_bytes()
        cls.encoded = base64.b64encode(cls.raw).decode("ascii")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.cache_dir = Path(self.temporary.name)
        self.calls = []
        self.payload = {"model": "gpt-image-2-4k", "prompt": "中文测试", "n": 1,
                        "size": "3840x2160", "quality": "high", "output_format": "png"}
        self.items = [{"url": "https://cdn.example.test/result.jpeg"}]

    def opener(self, request, timeout):
        self.calls.append((request, timeout))
        if request.get_method() == "POST":
            return Response(json.dumps({"data": self.items}).encode("utf-8"))
        return Response(self.raw)

    def provider(self, opener=None):
        return NebulaImageProvider(api_key="fake-secret", base_url="https://img-api.apinebula.ai/v1",
                                   urlopen=opener or self.opener, cache_dir=self.cache_dir)

    def test_factory_preserves_other_providers_and_exact_host(self):
        for url, expected in [
            ("https://img-api.apinebula.ai/v1", NebulaImageProvider),
            ("https://IMG-API.APINEBULA.AI/v1/", NebulaImageProvider),
            (None, OpenAICompatibleImageProvider),
            ("https://img-api.apinebula.ai.example.test/v1", OpenAICompatibleImageProvider),
            ("https://api.atlascloud.ai/api/v1/model", AtlasCloudImageProvider),
        ]:
            with self.subTest(url=url):
                self.assertIsInstance(create_image_provider(api_key="fake", base_url=url), expected)

    def test_json_generation_download_auth_and_png_conversion(self):
        result = self.provider().generate(self.payload)
        request, timeout = self.calls[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, "https://img-api.apinebula.ai/v1/images/generations")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(request.get_header("Authorization"), "Bearer fake-secret")
        self.assertEqual(timeout, 1800)
        self.assertEqual(body, {"model": "gpt-image-2-4k", "prompt": "中文测试", "n": 1,
                                "size": "3840x2160", "quality": "high",
                                "output_format": "jpeg", "response_format": "url"})
        download, download_timeout = self.calls[1]
        self.assertIsNone(download.get_header("Authorization"))
        self.assertEqual(download_timeout, 120)
        with Image.open(BytesIO(base64.b64decode(result[0]))) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (3840, 2160))

    def test_base64_only_and_requested_preference(self):
        self.items = [{"b64_json": self.encoded}]
        result = self.provider().generate({**self.payload, "output_format": "jpeg"})
        self.assertEqual(base64.b64decode(result[0]), self.raw)
        self.assertEqual(len(self.calls), 1)
        self.items = [{"url": "https://cdn.example.test/x", "b64_json": self.encoded}]
        self.calls.clear()
        self.provider().generate({**self.payload, "response_format": "b64_json"})
        self.assertEqual(len(self.calls), 1)
        self.calls.clear()
        self.provider().generate(self.payload)
        self.assertEqual(len(self.calls), 2)

    def test_base64_requested_can_fall_back_to_url(self):
        self.provider().generate({**self.payload, "response_format": "b64_json"})
        self.assertEqual(len(self.calls), 2)

    def test_edit_multipart_repeats_image_and_preserves_file_bytes(self):
        self.items = [{"b64_json": self.encoded}]
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "参考图.jpg"
            second = Path(directory) / "other.png"
            first.write_bytes(self.raw)
            second_raw = image_bytes("PNG", (64, 64))
            second.write_bytes(second_raw)
            self.provider().edit({**self.payload, "input_fidelity": "high"}, [first, second], None)
        request, timeout = self.calls[0]
        self.assertEqual(request.full_url, "https://img-api.apinebula.ai/v1/images/edits")
        self.assertEqual(timeout, 1800)
        message = BytesParser(policy=default).parsebytes(
            f"Content-Type: {request.get_header('Content-type')}\r\nMIME-Version: 1.0\r\n\r\n".encode()
            + request.data
        )
        parts = list(message.iter_parts())
        files = [part for part in parts if part.get_param("name", header="content-disposition") == "image"]
        self.assertEqual([part.get_payload(decode=True) for part in files], [self.raw, second_raw])
        fields = {part.get_param("name", header="content-disposition"): part.get_payload(decode=True).decode("utf-8")
                  for part in parts if not part.get_filename()}
        self.assertEqual(fields["input_fidelity"], "high")
        self.assertEqual(fields["prompt"], "中文测试")
        self.assertEqual(fields["output_format"], "jpeg")

    def test_invalid_options_fail_before_network(self):
        cases = [{"model": "gpt-image-2.5-flare"}, {"size": "2560x1440"},
                 {"background": "transparent"}, {"quality": "max"}, {"moderation": "bad"},
                 {"response_format": "bad"}, {"output_compression": 101}, {"n": 0}, {"n": 11},
                 {"prompt": ""}]
        for overrides in cases:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.provider().generate({**self.payload, **overrides})
        with self.assertRaises(ValueError):
            self.provider().edit(self.payload, [], Path("mask.png"))
        with self.assertRaises(ValueError):
            self.provider().edit(self.payload, [], None)
        with self.assertRaises(ValueError):
            self.provider().edit({**self.payload, "input_fidelity": "low"}, [], None)
        self.assertFalse(self.calls)

    def test_optional_fields_and_background_auto(self):
        body = nebula_payload({**self.payload, "background": "auto", "moderation": "low",
                               "output_compression": 65}, operation="generate")
        self.assertNotIn("background", body)
        self.assertEqual(body["moderation"], "low")
        self.assertEqual(body["output_compression"], 65)

    def test_n_is_one_submission(self):
        self.items = [{"b64_json": self.encoded}] * 2
        self.assertEqual(len(self.provider().generate({**self.payload, "n": 2})), 2)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(json.loads(self.calls[0][0].data)["n"], 2)

    def test_malformed_responses_and_corrupt_image(self):
        for result in [{}, {"error": {"message": "failure"}}, {"data": []},
                       {"data": [{}]}, {"data": ["wrong"]}, {"data": [{"b64_json": "!!!"}]},
                       {"data": [{"b64_json": base64.b64encode(b"not an image").decode()}]},
                       {"data": [{"url": "file:///secret"}]}, []]:
            with self.subTest(result=result), self.assertRaises(RuntimeError):
                self.provider(lambda request, timeout: Response(json.dumps(result).encode())).generate(self.payload)
        with self.assertRaises(RuntimeError):
            self.provider(lambda request, timeout: Response(b"<html>error</html>")).generate(self.payload)

    def test_empty_and_oversized_download(self):
        for raw in [b"", b"x" * 33]:
            self.raw = raw
            with patch("image_providers.nebula.MAX_IMAGE_BYTES", 32), self.assertRaises(RuntimeError):
                self.provider().generate(self.payload)

    def test_warn_actual_resolution_and_convert_webp(self):
        self.raw = image_bytes(size=(64, 64))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = self.provider().generate({**self.payload, "output_format": "webp"})
        self.assertTrue(any("64x64" in str(item.message) for item in caught))
        with Image.open(BytesIO(base64.b64decode(result[0]))) as image:
            self.assertEqual(image.format, "WEBP")

    def test_batch_does_not_retry_submission_or_download(self):
        def fail(request, timeout):
            self.calls.append((request, timeout))
            raise TimeoutError("simulated")
        with self.assertRaises(RuntimeError):
            asyncio.run(self.provider(fail).generate_batch(self.payload, attempts=3, job_label="test"))
        self.assertEqual(len(self.calls), 1)
        self.calls.clear()

        def fail_download(request, timeout):
            if request.get_method() == "GET":
                self.calls.append((request, timeout))
                raise HTTPError(request.full_url, 403, "expired", {}, None)
            return self.opener(request, timeout)
        with self.assertRaisesRegex(RuntimeError, "下载失败"):
            asyncio.run(self.provider(fail_download).generate_batch(self.payload, attempts=3, job_label="test"))
        self.assertEqual([r.get_method() for r, _ in self.calls], ["POST", "GET"])

    def test_http_failure_no_response_body_disclosure(self):
        def fail(request, timeout):
            raise HTTPError(request.full_url, 401, "Unauthorized", {}, BytesIO(b"secret diagnostic"))
        with self.assertRaisesRegex(RuntimeError, "HTTP 401") as caught:
            self.provider(fail).generate(self.payload)
        self.assertNotIn("secret", str(caught.exception))

    def test_download_failure_keeps_response_and_resume_only_downloads(self):
        def failing_download(request, timeout):
            if request.get_method() == "GET":
                raise HTTPError(request.full_url, 403, "blocked", {}, None)
            return self.opener(request, timeout)
        with self.assertRaisesRegex(RuntimeError, "原始响应已保存"):
            self.provider(failing_download).generate(self.payload)
        cached = list(self.cache_dir.glob("*.json"))
        self.assertEqual(len(cached), 1)
        record = json.loads(cached[0].read_text(encoding="utf-8"))
        self.assertEqual(record["response"]["data"], self.items)
        self.assertNotIn("fake-secret", cached[0].read_text(encoding="utf-8"))
        self.calls.clear()
        images = self.provider().resume_response(cached[0])
        self.assertEqual(len(images), 1)
        self.assertEqual([r.get_method() for r, _ in self.calls], ["GET"])
        self.assertEqual(self.calls[0][0].get_header("Referer"), "https://cdn.example.test/")

    def test_download_failure_uses_base64_from_same_response(self):
        self.items = [{"url": "https://cdn.example.test/blocked", "b64_json": self.encoded}]

        def failing_download(request, timeout):
            if request.get_method() == "GET":
                raise HTTPError(request.full_url, 403, "blocked", {}, None)
            return self.opener(request, timeout)
        result = self.provider(failing_download).generate(self.payload)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(self.calls), 1)


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.env = {key: value for key, value in os.environ.items()
                    if key not in {"OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_PPT_IMAGE_MODEL", "CODEX_PPT_HOME"}}
        self.env["CODEX_PPT_HOME"] = str(self.directory)

    def cli(self, *args, env=None):
        return subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "scripts/image_gen.py"), *args],
                              env=env or self.env, capture_output=True, encoding="utf-8")

    def test_shared_env_and_process_and_cli_precedence(self):
        (self.directory / ".env").write_text(
            "OPENAI_API_KEY=fake-key\nOPENAI_BASE_URL=https://img-api.apinebula.ai/v1\nCODEX_PPT_IMAGE_MODEL=gpt-image-2-4k\n",
            encoding="utf-8",
        )
        result = self.cli("generate", "--prompt", "test", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        preview = json.loads(result.stdout)
        self.assertEqual((preview["model"], preview["size"], preview["quality"]),
                         ("gpt-image-2-4k", "3840x2160", "high"))
        self.assertNotIn("fake-key", result.stdout + result.stderr)
        env = {**self.env, "CODEX_PPT_IMAGE_MODEL": "gpt-image-2.5-flare", "OPENAI_BASE_URL": "https://example.test/v1"}
        result = self.cli("generate", "--prompt", "test", "--dry-run", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["size"], "2560x1440")
        result = self.cli("generate", "--base-url", "https://img-api.apinebula.ai/v1", "--model", "gpt-image-2-4k",
                          "--prompt", "test", "--dry-run", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["model"], "gpt-image-2-4k")

    def test_nebula_default_and_edit_dry_run(self):
        image = self.directory / "input.png"
        image.write_bytes(image_bytes("PNG", (64, 64)))
        result = self.cli("edit", "--base-url", "https://img-api.apinebula.ai/v1", "--image", str(image),
                          "--input-fidelity", "high", "--prompt", "edit", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        preview = json.loads(result.stdout)
        self.assertEqual(preview["endpoint"], "/v1/images/edits")
        self.assertEqual(preview["output_format"], "jpeg")
        self.assertEqual(preview["local_output_format"], "png")

    def test_cli_generate_and_edit_save_decodable_images(self):
        raw = image_bytes()
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return Response(json.dumps({"data": [{"b64_json": base64.b64encode(raw).decode()}]}).encode())

        def factory(**kwargs):
            return NebulaImageProvider(**kwargs, urlopen=opener)

        generated = self.directory / "generated.png"
        edited = self.directory / "edited.png"
        env = {**self.env, "OPENAI_API_KEY": "fake-key"}
        commands = [
            ["generate", "--prompt", "test", "--out", str(generated)],
            ["edit", "--prompt", "edit", "--image", str(generated), "--input-fidelity", "high",
             "--out", str(edited)],
        ]
        with patch.dict(os.environ, env, clear=True), patch.object(image_gen, "create_image_provider", side_effect=factory):
            for command in commands:
                with patch.object(sys, "argv", ["image_gen.py", *command, "--base-url", "https://img-api.apinebula.ai/v1"]), \
                        redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    self.assertEqual(image_gen.main(), 0)
        for path in [generated, edited]:
            with Image.open(path) as image:
                image.load()
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.size, (3840, 2160))
        self.assertEqual([r.full_url.rsplit("/", 1)[1] for r in requests], ["generations", "edits"])

    def test_batch_dry_run_and_invalid_size(self):
        jobs = self.directory / "jobs.jsonl"
        jobs.write_text('{"prompt":"test"}\n', encoding="utf-8")
        result = self.cli("generate-batch", "--base-url", "https://img-api.apinebula.ai/v1", "--input", str(jobs),
                          "--out-dir", str(self.directory / "output"), "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["size"], "3840x2160")
        result = self.cli("generate", "--base-url", "https://img-api.apinebula.ai/v1", "--size", "2560x1440",
                          "--prompt", "test", "--dry-run")
        self.assertNotEqual(result.returncode, 0)

    def test_resume_cli_needs_no_prompt_or_api_key(self):
        cached = self.directory / "response.json"
        cached.write_text(json.dumps({"schema_version": 1, "request": {"n": 1, "output_format": "png"},
            "response": {"data": [{"b64_json": base64.b64encode(image_bytes()).decode()}]}}), encoding="utf-8")
        output = self.directory / "resumed.png"
        result = self.cli("resume", "--response-file", str(cached), "--out", str(output))
        self.assertEqual(result.returncode, 0, result.stderr)
        with Image.open(output) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (3840, 2160))

    def test_default_openai_and_atlascloud_preview(self):
        result = self.cli("generate", "--prompt", "test", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        preview = json.loads(result.stdout)
        self.assertEqual(preview["model"], "gpt-image-2.5-flare")
        self.assertEqual(preview["size"], "2560x1440")
        result = self.cli("generate", "--base-url", "https://api.atlascloud.ai/api/v1/model", "--prompt", "test", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["endpoint"], "/api/v1/model/generateImage")

    def test_doctor_nebula_does_not_probe_models_or_submit_images(self):
        (self.directory / ".env").write_text(
            "OPENAI_API_KEY=fake-key\nOPENAI_BASE_URL=https://img-api.apinebula.ai/v1\n", encoding="utf-8")
        with patch.dict(os.environ, self.env, clear=True), \
                patch.object(codex_ppt_runtime, "_check_python_imports", return_value=True), \
                patch.object(codex_ppt_runtime, "_models_request") as models, \
                patch("sys.stdout", new_callable=StringIO) as output:
            self.assertEqual(codex_ppt_runtime.main(["doctor", "--check-api"]), 0)
        models.assert_not_called()
        self.assertIn("尚未实测", output.getvalue())


if __name__ == "__main__":
    unittest.main()
