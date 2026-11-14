"""Shared flat/sectioned .env configuration for image providers."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Mapping, Tuple
from urllib.parse import urlparse


ENV_FIELDS = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_PPT_IMAGE_MODEL")
NEBULA_OPTIONS = ("NEBULA_SIZE", "NEBULA_QUALITY")
TEAMOROUTER_OPTIONS = ("TEAMOROUTER_SIZE", "TEAMOROUTER_QUALITY")
PROVIDERS = ("nebula", "openai", "atlascloud", "teamorouter")
NEBULA_FIELDS = {
    "OPENAI_API_KEY": "NEBULA_API_KEY",
    "OPENAI_BASE_URL": "NEBULA_BASE_URL",
    "CODEX_PPT_IMAGE_MODEL": "NEBULA_PPT_IMAGE_MODEL",
}
DEFAULT_URLS = {
    "nebula": "https://img-api.apinebula.ai/v1",
    "openai": "https://api.openai.com/v1",
    "atlascloud": "https://api.atlascloud.ai/api/v1/model",
    "teamorouter": "https://api.teamorouter.com/v1",
}


def read_env_file(path: Path) -> Tuple[Dict[str, str], Dict[str, Dict[str, str]]]:
    root: Dict[str, str] = {}
    sections: Dict[str, Dict[str, str]] = {}
    if not path.exists():
        return root, sections
    current = root
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("["):
            if not line.endswith("]") or not line[1:-1].strip():
                raise ValueError(f".env 第 {number} 行的分组格式无效")
            name = line[1:-1].strip().lower()
            current = sections.setdefault(name, {})
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            if value[0] == '"':
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    value = value[1:-1]
            else:
                value = value[1:-1]
        current[key.strip()] = value
    return root, sections


def provider_for_url(base_url: str) -> str:
    hostname = (urlparse(base_url).hostname or "").lower()
    if hostname == "img-api.apinebula.ai":
        return "nebula"
    if hostname == "api.teamorouter.com":
        return "teamorouter"
    if "atlascloud.ai" in hostname:
        return "atlascloud"
    return "openai"


def validate_provider(values: Mapping[str, str]) -> None:
    provider = values.get("IMAGE_PROVIDER", "").strip().lower()
    if not provider:
        return
    if provider not in PROVIDERS:
        raise ValueError(f"IMAGE_PROVIDER 仅支持 {', '.join(PROVIDERS)}")
    if provider_for_url(values.get("OPENAI_BASE_URL", "")) != provider:
        raise ValueError("IMAGE_PROVIDER 与 OPENAI_BASE_URL 对应的适配器不一致，请检查配置或命令行覆盖参数")


def load_env_values(path: Path, environ: Mapping[str, str] | None = None) -> Dict[str, str]:
    env = os.environ if environ is None else environ
    root, sections = read_env_file(path)
    provider = (env.get("IMAGE_PROVIDER") or root.get("IMAGE_PROVIDER", "")).strip().lower()
    if provider:
        if provider not in PROVIDERS:
            raise ValueError(f"IMAGE_PROVIDER 仅支持 {', '.join(PROVIDERS)}")
        if provider not in sections:
            raise ValueError(f"IMAGE_PROVIDER 指定的 [{provider}] 分组不存在")
        # Grouped credentials never fall back to legacy root credentials.
        values = {key: value for key, value in sections[provider].items() if key in ENV_FIELDS}
        if provider in {"nebula", "teamorouter"}:
            options = NEBULA_OPTIONS if provider == "nebula" else TEAMOROUTER_OPTIONS
            values.update({key: sections[provider][key] for key in options if key in sections[provider]})
        if provider == "nebula":
            for key, alias in NEBULA_FIELDS.items():
                if alias in sections[provider]:
                    values[key] = sections[provider][alias]
        values["IMAGE_PROVIDER"] = provider
        values.setdefault("OPENAI_BASE_URL", DEFAULT_URLS[provider])
    else:
        if sections:
            raise ValueError(".env 含服务商分组，请在文件顶部设置 IMAGE_PROVIDER")
        values = {key: value for key, value in root.items() if key in ENV_FIELDS}
    for key in ENV_FIELDS:
        if env.get(key):
            values[key] = env[key]
    active_provider = provider or provider_for_url(values.get("OPENAI_BASE_URL", ""))
    if active_provider in {"nebula", "teamorouter"}:
        options = NEBULA_OPTIONS if active_provider == "nebula" else TEAMOROUTER_OPTIONS
        for key in options:
            if env.get(key):
                values[key] = env[key]
    return values
