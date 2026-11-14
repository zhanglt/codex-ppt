from __future__ import annotations

import os
from typing import Optional
from urllib.parse import urlparse

from network_proxy import validate_proxy

from .atlascloud import AtlasCloudImageProvider
from .base import ImageProvider
from .nebula import NebulaImageProvider, is_nebula_base_url
from .openai_compatible import OpenAICompatibleImageProvider


def create_image_provider(*, api_key: Optional[str], base_url: Optional[str]) -> ImageProvider:
    proxy = validate_proxy(os.getenv("PROXY", ""))
    if is_nebula_base_url(base_url):
        return NebulaImageProvider(api_key=api_key, base_url=base_url, proxy=proxy)
    if _is_atlascloud_base_url(base_url):
        return AtlasCloudImageProvider(api_key=api_key, base_url=base_url, proxy=proxy)
    return OpenAICompatibleImageProvider(api_key=api_key, base_url=base_url, proxy=proxy)


def _is_atlascloud_base_url(base_url: Optional[str]) -> bool:
    if not base_url:
        return False
    hostname = urlparse(base_url).hostname or ""
    return "atlascloud.ai" in hostname.lower()
