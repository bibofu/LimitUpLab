"""Local environment configuration helpers."""

from __future__ import annotations

import os
from pathlib import Path


TRUE_VALUES = {"1", "true", "yes", "on"}


def load_local_env(path: str | Path | None = None, *, override: bool = False) -> list[Path]:
    """Load local .env files without requiring an external dependency.

    Existing process environment variables win by default so CI, shell exports,
    and deployment platform settings remain authoritative.
    """

    loaded_paths: list[Path] = []
    for env_path in _candidate_env_paths(path):
        if not env_path.exists() or not env_path.is_file():
            continue
        _load_env_file(env_path, override=override)
        loaded_paths.append(env_path)

    _apply_proxy_alias()
    return loaded_paths


def env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean environment variable using common truthy strings."""

    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in TRUE_VALUES


def _candidate_env_paths(path: str | Path | None) -> list[Path]:
    if path is not None:
        return [Path(path).expanduser().resolve()]

    explicit = os.getenv("LIMITUPLAB_ENV_FILE", "").strip()
    if explicit:
        return [Path(explicit).expanduser().resolve()]

    backend_root = Path(__file__).resolve().parents[1]
    project_root = backend_root.parent
    return [backend_root / ".env", project_root / ".env"]


def _load_env_file(path: Path, *, override: bool) -> None:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if not key or (not override and key in os.environ):
            continue
        os.environ[key] = _normalize_env_value(value.strip())


def _normalize_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value


def _apply_proxy_alias() -> None:
    proxy_url = os.getenv("LIMITUPLAB_PROXY_URL", "").strip()
    if not proxy_url:
        return
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        _set_proxy_if_missing_or_invalid(key, proxy_url)
        _set_proxy_if_missing_or_invalid(key.lower(), proxy_url)


def _set_proxy_if_missing_or_invalid(key: str, proxy_url: str) -> None:
    current = os.getenv(key, "").strip()
    if not current or current == "http://127.0.0.1:9":
        os.environ[key] = proxy_url
