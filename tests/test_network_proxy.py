from __future__ import annotations

import asyncio
import errno
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from image_providers.openai_compatible import OpenAICompatibleImageProvider
from network_proxy import is_connect_error, open_with_proxy_fallback, validate_proxy


class ProxyFallbackTests(unittest.TestCase):
    def test_urllib_success_does_not_use_proxy(self):
        direct = Mock(return_value="direct")
        with patch("network_proxy.build_opener") as build:
            result = open_with_proxy_fallback(Request("https://example.test"), timeout=5,
                                              proxy="http://127.0.0.1:81", direct_open=direct)
        self.assertEqual(result, "direct")
        build.assert_not_called()

    def test_urllib_retries_only_connect_failure(self):
        direct = Mock(side_effect=URLError(ConnectionRefusedError(errno.ECONNREFUSED, "refused")))
        proxy_open = Mock(return_value="through proxy")
        with patch("network_proxy.build_opener", return_value=SimpleNamespace(open=proxy_open)) as build:
            result = open_with_proxy_fallback(Request("https://example.test"), timeout=5,
                                              proxy="http://127.0.0.1:81", direct_open=direct)
        self.assertEqual(result, "through proxy")
        self.assertEqual(direct.call_count, 1)
        self.assertEqual(proxy_open.call_count, 1)
        self.assertEqual(build.call_count, 1)

    def test_urllib_does_not_retry_http_error_or_read_timeout(self):
        for error in (HTTPError("https://example.test", 503, "unavailable", {}, None), TimeoutError("read timed out")):
            with self.subTest(error=type(error).__name__):
                direct = Mock(side_effect=error)
                with patch("network_proxy.build_opener") as build, self.assertRaises(type(error)):
                    open_with_proxy_fallback(Request("https://example.test"), timeout=5,
                                             proxy="http://127.0.0.1:81", direct_open=direct)
                build.assert_not_called()

    def test_proxy_validation_and_error_classification(self):
        self.assertEqual(validate_proxy("http://127.0.0.1:81"), "http://127.0.0.1:81")
        for proxy in ("socks5://127.0.0.1:81", "http://", "http://localhost:81/path"):
            with self.assertRaises(ValueError):
                validate_proxy(proxy)
        self.assertTrue(is_connect_error(URLError(ConnectionRefusedError(errno.ECONNREFUSED, "refused"))))
        self.assertFalse(is_connect_error(HTTPError("https://example.test", 401, "auth", {}, None)))

    def test_sdk_generation_uses_proxy_once_after_connect_error(self):
        direct_images = SimpleNamespace(generate=Mock(side_effect=ConnectionRefusedError(errno.ECONNREFUSED, "refused")))
        proxy_images = SimpleNamespace(generate=Mock(return_value=SimpleNamespace(data=[SimpleNamespace(b64_json="image")])))
        provider = OpenAICompatibleImageProvider(api_key="fake", base_url="https://example.test/v1",
                                                  client_factory=lambda: SimpleNamespace(images=direct_images),
                                                  proxy="http://127.0.0.1:81")
        with patch.object(provider, "_create_proxy_client", return_value=SimpleNamespace(images=proxy_images)):
            self.assertEqual(provider.generate({"prompt": "test"}), ["image"])
        direct_images.generate.assert_called_once()
        proxy_images.generate.assert_called_once()

    def test_sdk_batch_uses_proxy_after_connect_error(self):
        async def fail(**kwargs):
            raise ConnectionRefusedError(errno.ECONNREFUSED, "refused")

        async def succeed(**kwargs):
            return SimpleNamespace(data=[SimpleNamespace(b64_json="image")])

        provider = OpenAICompatibleImageProvider(api_key="fake", base_url="https://example.test/v1",
                                                  async_client_factory=lambda: SimpleNamespace(images=SimpleNamespace(generate=fail)),
                                                  proxy="http://127.0.0.1:81")
        with patch.object(provider, "_create_proxy_async_client", return_value=SimpleNamespace(images=SimpleNamespace(generate=succeed))):
            result = asyncio.run(provider.generate_batch({"prompt": "test"}, attempts=1, job_label="job"))
        self.assertEqual(result, ["image"])

    def test_sdk_edit_rewinds_image_before_proxy_retry(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            image = Path(directory) / "image.png"
            image.write_bytes(b"image bytes")

            def fail(**kwargs):
                kwargs["image"].read()
                raise ConnectionRefusedError(errno.ECONNREFUSED, "refused")

            def succeed(**kwargs):
                self.assertEqual(kwargs["image"].read(), b"image bytes")
                return SimpleNamespace(data=[SimpleNamespace(b64_json="edited")])

            provider = OpenAICompatibleImageProvider(
                api_key="fake", base_url="https://example.test/v1",
                client_factory=lambda: SimpleNamespace(images=SimpleNamespace(edit=fail)),
                proxy="http://127.0.0.1:81",
            )
            with patch.object(provider, "_create_proxy_client", return_value=SimpleNamespace(images=SimpleNamespace(edit=succeed))):
                self.assertEqual(provider.edit({"prompt": "test"}, [image], None), ["edited"])


if __name__ == "__main__":
    unittest.main()
