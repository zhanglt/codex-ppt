from __future__ import annotations

import asyncio
import base64
import binascii
from io import BytesIO
import json
import mimetypes
import os
from pathlib import Path
import sys
from typing import Any, Callable, Dict, List, Optional
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen as default_urlopen
from uuid import uuid4
import warnings

from .base import ImageProvider
from network_proxy import open_with_proxy_fallback


NEBULA_MODEL = "gpt-image-2-4k"
NEBULA_SIZE = "3840x2160"
NEBULA_BASE_URL = "https://img-api.apinebula.ai/v1"
MAX_IMAGE_BYTES = 50 * 1024 * 1024
USER_AGENT = "Mozilla/5.0 (compatible; codex-ppt-skill/0.1)"


def is_nebula_base_url(base_url: Optional[str]) -> bool:
    return (urlparse(base_url or "").hostname or "").lower() == "img-api.apinebula.ai"


def nebula_payload(payload: Dict[str, Any], *, operation: str) -> Dict[str, Any]:
    if payload.get("model") != NEBULA_MODEL:
        raise ValueError("Nebula image2-4k 接口须使用 --model gpt-image-2-4k。")
    if payload.get("size") != NEBULA_SIZE:
        raise ValueError("Nebula image2-4k 仅支持 --size 3840x2160。")
    if payload.get("quality", "high") not in {"low", "medium", "high", "auto"}:
        raise ValueError("Nebula quality 仅支持 low、medium、high、auto；4K 建议 high。")
    if not str(payload.get("prompt", "")).strip():
        raise ValueError("Nebula prompt 不可为空。")
    if not 1 <= int(payload.get("n", 1)) <= 10:
        raise ValueError("Nebula n 必须在 1 到 10 之间。")
    if payload.get("background") not in {None, "auto", "opaque"}:
        raise ValueError("Nebula image2-4k 不支持透明背景。")
    if payload.get("input_fidelity") not in {None, "high"}:
        raise ValueError("Nebula input_fidelity 仅支持 high。")
    if operation != "edit" and payload.get("input_fidelity") is not None:
        raise ValueError("input_fidelity 仅适用于编辑图接口。")
    if payload.get("moderation") not in {None, "auto", "low"}:
        raise ValueError("Nebula moderation 仅支持 auto、low。")
    if payload.get("response_format", "url") not in {"url", "b64_json"}:
        raise ValueError("Nebula response_format 仅支持 url、b64_json。")
    output_format = payload.get("output_format", "png")
    if output_format not in {"png", "jpeg", "jpg", "webp"}:
        raise ValueError("本地输出格式须为 png、jpeg、jpg 或 webp。")
    compression = payload.get("output_compression")
    if compression is not None and not 0 <= int(compression) <= 100:
        raise ValueError("output_compression 必须在 0 到 100 之间。")

    body = {
        key: payload[key]
        for key in ("model", "prompt", "n", "size", "quality", "output_compression",
                    "moderation", "user", "input_fidelity")
        if payload.get(key) is not None
    }
    body.setdefault("n", 1)
    body.setdefault("quality", "high")
    body["response_format"] = payload.get("response_format", "url")
    # 此分组仅声明支持 JPEG；其他本地输出格式在收到图片后转换。
    body["output_format"] = "jpeg"
    if payload.get("background") == "opaque":
        body["background"] = "opaque"
    return body


class NebulaImageProvider(ImageProvider):
    def __init__(
        self,
        *,
        api_key: Optional[str],
        base_url: Optional[str],
        urlopen: Callable[..., Any] = default_urlopen,
        cache_dir: Optional[Path] = None,
        proxy: str = "",
    ) -> None:
        self.api_key = api_key
        self.base_url = (base_url or NEBULA_BASE_URL).rstrip("/")
        if not is_nebula_base_url(self.base_url) or urlparse(self.base_url).scheme != "https":
            raise ValueError("Nebula 必须使用 https://img-api.apinebula.ai/v1。")
        if urlparse(self.base_url).path != "/v1" or urlparse(self.base_url).query:
            raise ValueError("Nebula base URL 应以 /v1 结尾，不含具体接口路径或查询参数。")
        self._urlopen = urlopen
        self.proxy = proxy
        self.cache_dir = cache_dir if cache_dir is not None else (
            Path(os.getenv("CODEX_PPT_HOME", "~/.codex-ppt-skill")).expanduser()
            / "cache" / "nebula-responses"
        )

    def generate(self, payload: Dict[str, Any]) -> List[str]:
        body = nebula_payload(payload, operation="generate")
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        return self._submit("generations", raw, "application/json", payload)

    def edit(
        self,
        payload: Dict[str, Any],
        image_paths: List[Path],
        mask_path: Optional[Path],
    ) -> List[str]:
        if mask_path is not None:
            raise ValueError("Nebula image2-4k 文档未声明支持 mask；请使用参考图编辑。")
        body = nebula_payload(payload, operation="edit")
        if not image_paths:
            raise ValueError("Nebula 编辑图至少需要一张参考图。")
        boundary = "codex-ppt-" + uuid4().hex
        chunks: List[bytes] = []
        for key, value in body.items():
            chunks.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode("utf-8")
            )
        for index, path in enumerate(image_paths, start=1):
            if path.stat().st_size > MAX_IMAGE_BYTES:
                raise ValueError("Nebula 参考图超过本工具的 50 MB 限制。")
            mime = mimetypes.guess_type(path.name)[0]
            if mime not in {"image/png", "image/jpeg", "image/webp"}:
                raise ValueError("参考图须为 PNG、JPEG 或 WebP。")
            # 使用安全文件名；多图按官方协议重复 image 字段，不使用 image[]。
            header = (
                f'--{boundary}\r\nContent-Disposition: form-data; name="image"; '
                f'filename="reference_{index}{path.suffix.lower()}"\r\nContent-Type: {mime}\r\n\r\n'
            )
            chunks.extend((header.encode("ascii"), path.read_bytes(), b"\r\n"))
        chunks.append(f"--{boundary}--\r\n".encode("ascii"))
        return self._submit("edits", b"".join(chunks), f"multipart/form-data; boundary={boundary}", payload)

    async def generate_batch(
        self, payload: Dict[str, Any], *, attempts: int, job_label: str,
    ) -> List[str]:
        # 超时或下载失败不代表未计费，批量任务也不自动重新提交。
        return await asyncio.to_thread(self.generate, payload)

    def _submit(
        self, endpoint: str, data: bytes, content_type: str, payload: Dict[str, Any],
    ) -> List[str]:
        if not self.api_key:
            raise ValueError("Nebula 缺少 OPENAI_API_KEY。")
        request = Request(
            f"{self.base_url}/images/{endpoint}", data=data,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": content_type,
                     "Accept": "application/json", "User-Agent": USER_AGENT},
            method="POST",
        )
        try:
            with open_with_proxy_fallback(request, timeout=1800, proxy=self.proxy, direct_open=self._urlopen) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise RuntimeError(f"Nebula API 返回 HTTP {exc.code}；未自动重新提交。") from None
        except (TimeoutError, OSError) as exc:
            raise RuntimeError("Nebula 请求超时或连接失败；请核对服务端任务，未自动重新提交。") from exc
        except (ValueError, UnicodeError):
            raise RuntimeError("Nebula 返回的内容不是有效 JSON；未自动重新提交。") from None
        if not isinstance(result, dict) or result.get("error"):
            raise RuntimeError("Nebula 返回 API 错误或非对象响应；未自动重新提交。")
        cache_path = self._cache_response(result, payload)
        try:
            return self._collect_response(result, payload)
        except RuntimeError as exc:
            raise RuntimeError(f"{exc} 原始响应已保存：{cache_path}；可用 resume 命令重试取图。") from None

    def _cache_response(self, result: Dict[str, Any], payload: Dict[str, Any]) -> Path:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / f"{uuid4().hex}.json"
        request = {key: payload[key] for key in (
            "model", "size", "quality", "n", "output_format", "response_format"
        ) if key in payload}
        record = {"schema_version": 1, "request": request, "response": result}
        # 下载前保存，恢复只读取此响应，不触发新的计费请求。
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        print(f"Nebula 原始响应缓存：{path}", file=sys.stderr)
        return path

    def resume_response(self, path: Path, *, output_format: Optional[str] = None) -> List[str]:
        record = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("schema_version") != 1:
            raise ValueError("不是可恢复的 Nebula 响应缓存。")
        payload = record.get("request")
        result = record.get("response")
        if not isinstance(payload, dict) or not isinstance(result, dict):
            raise ValueError("Nebula 响应缓存缺少 request 或 response。")
        payload = dict(payload)
        if output_format is not None:
            payload["output_format"] = output_format
        if payload.get("output_format", "png") not in {"png", "jpeg", "jpg", "webp"}:
            raise ValueError("本地输出格式须为 png、jpeg、jpg 或 webp。")
        return self._collect_response(result, payload)

    def _collect_response(self, result: Dict[str, Any], payload: Dict[str, Any]) -> List[str]:
        items = result.get("data")
        if not isinstance(items, list) or len(items) != int(payload.get("n", 1)):
            raise RuntimeError("Nebula 返回图片数量与请求不符；未自动重新提交。")
        return [self._image_to_b64(item, payload) for item in items]

    def _image_to_b64(self, item: Any, payload: Dict[str, Any]) -> str:
        if not isinstance(item, dict):
            raise RuntimeError("Nebula 图片响应项不是对象。")
        url = item.get("url")
        encoded = item.get("b64_json")
        prefer_url = payload.get("response_format", "url") == "url"
        if url and (prefer_url or not encoded):
            if not isinstance(url, str) or urlparse(url).scheme not in {"https", "http"}:
                raise RuntimeError("Nebula 图片 URL 必须使用 HTTP(S)。")
            # CDN 下载不携带 API Authorization，也不触发新的生成请求。
            parsed = urlparse(url)
            request = Request(url, headers={"User-Agent": USER_AGENT,
                                            "Referer": f"{parsed.scheme}://{parsed.netloc}/"})
            try:
                with open_with_proxy_fallback(request, timeout=120, proxy=self.proxy, direct_open=self._urlopen) as response:
                    raw = response.read(MAX_IMAGE_BYTES + 1)
            except (OSError, TimeoutError):
                if encoded:
                    raw = self._decode_base64(encoded)
                else:
                    raise RuntimeError("Nebula 图片下载失败；未自动重新生成，请核对 CDN 链接有效期。") from None
        elif encoded:
            raw = self._decode_base64(encoded)
        else:
            raise RuntimeError("Nebula 响应中缺少 url 和 b64_json。")
        if not raw or len(raw) > MAX_IMAGE_BYTES:
            raise RuntimeError("Nebula 图片为空或超过 50 MB。")

        from PIL import Image

        try:
            with Image.open(BytesIO(raw)) as image:
                image.load()
                if image.size != (3840, 2160):
                    warnings.warn(f"Nebula 实际图片尺寸为 {image.width}x{image.height}，未达到请求的 3840x2160。")
                fmt = str(payload.get("output_format", "png")).lower()
                fmt = "jpeg" if fmt == "jpg" else fmt
                if image.format != fmt.upper():
                    output = BytesIO()
                    image = image.convert("RGB") if fmt == "jpeg" else image
                    image.save(output, format=fmt.upper())
                    raw = output.getvalue()
        except (OSError, ValueError):
            raise RuntimeError("Nebula 返回的内容无法解码为有效图片。") from None
        return base64.b64encode(raw).decode("ascii")

    @staticmethod
    def _decode_base64(encoded: Any) -> bytes:
        try:
            if not isinstance(encoded, str) or len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
                raise ValueError()
            return base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise RuntimeError("Nebula 返回无效或过大的 Base64 图片。") from None
