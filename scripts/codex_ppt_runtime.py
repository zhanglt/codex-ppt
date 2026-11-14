#!/usr/bin/env python3
"""Manage the cross-agent runtime for codex-ppt.

The runtime lives outside any specific agent installation so Codex, Claude Code,
OpenClaw, Hermes Agent, and other local agents can share one virtual
environment and one API configuration.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from typing import Dict, Iterable, Optional
import urllib.error
import urllib.parse
import urllib.request
import venv

from image_providers.nebula import NEBULA_MODEL, is_nebula_base_url
from env_config import ENV_FIELDS, NEBULA_FIELDS, PROVIDERS, load_env_values, read_env_file, validate_provider
from network_proxy import open_with_proxy_fallback


DEFAULT_RUNTIME_HOME = "~/.codex-ppt-skill"
DEFAULT_MODEL = "gpt-image-2.5-flare"


def _runtime_home() -> Path:
    return Path(os.getenv("CODEX_PPT_HOME", DEFAULT_RUNTIME_HOME)).expanduser()


def _skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _venv_python(home: Path) -> Path:
    if os.name == "nt":
        return home / ".venv" / "Scripts" / "python.exe"
    return home / ".venv" / "bin" / "python"


def _env_path(home: Path) -> Path:
    return home / ".env"


def _requirements_path() -> Path:
    return _skill_root() / "requirements.txt"


def _die(message: str, code: int = 1) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(code)


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}...{value[-4:]}"


def _parse_env_file(path: Path) -> Dict[str, str]:
    return load_env_values(path, {})


def _quote_env_value(value: str) -> str:
    if not value:
        return ""
    if any(ch.isspace() for ch in value) or "#" in value or '"' in value:
        return json.dumps(value)
    return value


def _write_env_file(path: Path, values: Dict[str, str], sections: Optional[Dict[str, Dict[str, str]]] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# codex-ppt shared runtime configuration",
        "# Used by Codex, Claude Code, OpenClaw, Hermes Agent, and other local agents.",
    ]
    for key, value in values.items():
        lines.append(f"{key}={_quote_env_value(value)}")
    for name, fields in (sections or {}).items():
        lines.extend(("", f"[{name}]"))
        for key, value in fields.items():
            lines.append(f"{key}={_quote_env_value(value)}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def _load_env_values(home: Path) -> Dict[str, str]:
    values = load_env_values(_env_path(home))
    validate_provider(values)
    return values


def _ensure_dirs(home: Path) -> None:
    for child in (home, home / "cache", home / "logs"):
        child.mkdir(parents=True, exist_ok=True)


def _bootstrap(args: argparse.Namespace) -> int:
    home = _runtime_home()
    _ensure_dirs(home)
    python = _venv_python(home)
    if not python.exists():
        print(f"Creating virtual environment: {home / '.venv'}")
        venv.EnvBuilder(with_pip=True, clear=False).create(home / ".venv")
    else:
        print(f"Virtual environment already exists: {home / '.venv'}")

    requirements = _requirements_path()
    if not requirements.exists():
        _die(f"requirements.txt not found: {requirements}")

    cmd = [str(python), "-m", "pip", "install", "-r", str(requirements)]
    if args.upgrade:
        cmd.insert(4, "-U")
    print(f"Installing dependencies from: {requirements}")
    subprocess.run(cmd, check=True)
    print(f"Runtime ready: {home}")
    return 0


def _config(args: argparse.Namespace) -> int:
    home = _runtime_home()
    _ensure_dirs(home)
    env_file = _env_path(home)
    root, sections = read_env_file(env_file)
    provider = args.provider or root.get("IMAGE_PROVIDER", "").strip().lower()
    if provider:
        if provider not in PROVIDERS:
            _die(f"IMAGE_PROVIDER 仅支持 {', '.join(PROVIDERS)}")
        root["IMAGE_PROVIDER"] = provider
        values = sections.setdefault(provider, {})
    else:
        if sections:
            _die(".env 含服务商分组，请使用 config --provider 选择分组")
        values = root

    fields = NEBULA_FIELDS if provider == "nebula" else {key: key for key in ENV_FIELDS}
    if provider == "nebula":
        for key, alias in fields.items():
            if key in values:
                values.setdefault(alias, values.pop(key))

    if args.api_key:
        values[fields["OPENAI_API_KEY"]] = args.api_key

    if args.base_url is not None:
        values[fields["OPENAI_BASE_URL"]] = args.base_url.strip()
    if args.model is not None:
        values[fields["CODEX_PPT_IMAGE_MODEL"]] = args.model.strip()

    if args.clear_base_url:
        values.pop(fields["OPENAI_BASE_URL"], None)

    _write_env_file(env_file, root, sections)
    print(f"Wrote {env_file}")
    if provider:
        print(f"IMAGE_PROVIDER={provider}")
    for key in ENV_FIELDS:
        value = values.get(fields[key], "")
        if key == "OPENAI_API_KEY":
            value = _mask_secret(value)
        print(f"{fields[key]}={value or '<unset>'}")
    return 0


def _check_python_imports(python: Path) -> bool:
    if not python.exists():
        print(f"venv: missing ({python})")
        return False
    code = "import openai, PIL, pptx; print('imports ok')"
    proc = subprocess.run(
        [str(python), "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        print("dependencies: missing or broken")
        if proc.stderr.strip():
            print(proc.stderr.strip())
        return False
    print("dependencies: ok")
    return True


def _models_request(base_url: str, api_key: str, timeout: int, proxy: str = "") -> bool:
    endpoint = base_url.rstrip("/") + "/models"
    req = urllib.request.Request(
        endpoint,
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    try:
        with open_with_proxy_fallback(req, timeout=timeout, proxy=proxy) as resp:
            print(f"models endpoint: HTTP {resp.status}")
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as exc:
        body = exc.read(800).decode("utf-8", "replace")
        print(f"models endpoint: HTTP {exc.code}")
        if body:
            print(body)
        return False
    except Exception as exc:
        print(f"models endpoint: {exc.__class__.__name__}: {exc}")
        return False


def _is_atlascloud_base_url(base_url: str) -> bool:
    hostname = urllib.parse.urlparse(base_url).hostname or ""
    return "atlascloud.ai" in hostname.lower()


def _doctor(args: argparse.Namespace) -> int:
    home = _runtime_home()
    env_file = _env_path(home)
    print(f"runtime home: {home}")
    print(f"env file: {env_file} ({'exists' if env_file.exists() else 'missing'})")
    print(f"venv python: {_venv_python(home)}")

    ok = True
    ok = _check_python_imports(_venv_python(home)) and ok

    values = _load_env_values(home)
    print(f"IMAGE_PROVIDER={values.get('IMAGE_PROVIDER') or '<auto>'}")
    api_key = values.get("OPENAI_API_KEY", "")
    base_url = values.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model = values.get("CODEX_PPT_IMAGE_MODEL", NEBULA_MODEL if is_nebula_base_url(base_url) else DEFAULT_MODEL)

    print(f"OPENAI_API_KEY={'set (' + _mask_secret(api_key) + ')' if api_key else '<unset>'}")
    print(f"OPENAI_BASE_URL={base_url}")
    print(f"CODEX_PPT_IMAGE_MODEL={model}")
    if is_nebula_base_url(base_url):
        print("provider: Nebula image2-4k（生图与参考图编辑）")
        if model != NEBULA_MODEL:
            print("model check: Nebula image2-4k 须配置 gpt-image-2-4k")
            ok = False

    if "gpt-image-" not in model:
        print("model check: warning, model name should contain 'gpt-image-'")
        ok = False
    else:
        print("model check: ok")

    if args.check_api:
        if not api_key:
            print("api check: skipped, OPENAI_API_KEY is unset")
            ok = False
        elif is_nebula_base_url(base_url):
            print("api check: Nebula 不探测 /models；未提交计费图片请求，生图与编辑能力尚未实测")
        elif _is_atlascloud_base_url(base_url):
            print("api check: AtlasCloud provider detected; skipping /models probe")
        else:
            ok = _models_request(base_url, api_key, args.timeout, values.get("PROXY", "")) and ok

    return 0 if ok else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the codex-ppt shared runtime")
    subparsers = parser.add_subparsers(dest="command", required=True)

    bootstrap = subparsers.add_parser("bootstrap", help="Create shared venv and install deps")
    bootstrap.add_argument("--upgrade", action="store_true")
    bootstrap.set_defaults(func=_bootstrap)

    config = subparsers.add_parser("config", help="Write or update shared .env")
    config.add_argument("--api-key")
    config.add_argument("--base-url")
    config.add_argument("--clear-base-url", action="store_true")
    config.add_argument("--model")
    config.add_argument("--provider", choices=PROVIDERS, help="选择并更新对应服务商分组，保留其他分组")
    config.set_defaults(func=_config)

    doctor = subparsers.add_parser("doctor", help="Check runtime and optional API access")
    doctor.add_argument("--check-api", action="store_true")
    doctor.add_argument("--timeout", type=int, default=30)
    doctor.set_defaults(func=_doctor)

    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return args.func(args)
    except ValueError as exc:
        _die(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
