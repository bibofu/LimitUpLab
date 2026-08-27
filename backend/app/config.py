"""Local environment configuration helpers."""

from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse


TRUE_VALUES = {"1", "true", "yes", "on"}
DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)
PROXY_ENV_NAMES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


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


def configure_runtime_environment(path: str | Path | None = None) -> list[Path]:
    """Load local settings and make direct backend launches LLM-ready."""

    loaded_paths = load_local_env(path)
    hydrate_windows_environment(("DEEPSEEK_API_KEY", "OPENAI_API_KEY"))
    api_key = (
        os.getenv("DEEPSEEK_API_KEY", "").strip()
        or os.getenv("OPENAI_API_KEY", "").strip()
    )
    if api_key:
        _set_default_if_blank("LIMITUPLAB_LLM_ENABLED", "true")
        _set_default_if_blank("LIMITUPLAB_LLM_BASE_URL", "https://api.deepseek.com")
        _set_default_if_blank("LIMITUPLAB_LLM_MODEL", "deepseek-v4-flash")
    clear_unreachable_local_proxy()
    return loaded_paths


def env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean environment variable using common truthy strings."""

    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in TRUE_VALUES


def configured_cors_origins() -> list[str]:
    """Return explicit browser origins allowed to call the API."""

    raw_value = os.getenv("LIMITUPLAB_CORS_ORIGINS", "").strip()
    if not raw_value:
        return list(DEFAULT_CORS_ORIGINS)
    origins = list(dict.fromkeys(_split_env_list(raw_value)))
    if "*" in origins:
        raise ValueError(
            "LIMITUPLAB_CORS_ORIGINS must list explicit origins when credentials are enabled"
        )
    return origins


def hydrate_windows_environment(names: Iterable[str]) -> list[str]:
    """Load missing values from Windows User/Machine environment scopes.

    New terminals do not automatically inherit environment variables written
    after the parent process started. This keeps CLI tools consistent with the
    Windows startup scripts without logging secret values.
    """

    loaded: list[str] = []
    for name in names:
        if os.getenv(name, "").strip():
            continue
        value = _read_windows_environment_value(name)
        if value:
            os.environ[name] = value
            loaded.append(name)
    return loaded


def replace_proxy_environment(proxy_url: str | None = None) -> None:
    """Remove inherited proxy variables and optionally install one known proxy."""

    for name in PROXY_ENV_NAMES:
        os.environ.pop(name, None)
    normalized = (proxy_url or "").strip()
    if not normalized:
        return
    for name in PROXY_ENV_NAMES:
        os.environ[name] = normalized


def clear_unreachable_local_proxy() -> bool:
    """Remove inherited localhost proxies that are not accepting connections."""

    proxy_values = [
        os.getenv("LIMITUPLAB_PROXY_URL", ""),
        *(os.getenv(name, "") for name in PROXY_ENV_NAMES),
    ]
    for value in proxy_values:
        endpoint = _local_proxy_endpoint(value)
        if endpoint is None or _proxy_endpoint_reachable(*endpoint):
            continue
        os.environ.pop("LIMITUPLAB_PROXY_URL", None)
        replace_proxy_environment()
        return True
    return False


def detect_local_proxy() -> str:
    """Return the first supported local HTTP proxy that accepts connections."""

    for port in (17891, 7890, 10809, 1080):
        if _proxy_endpoint_reachable("127.0.0.1", port):
            return f"http://127.0.0.1:{port}"
    return ""


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
    replace_proxy_environment(proxy_url)


def _set_default_if_blank(name: str, value: str) -> None:
    if not os.getenv(name, "").strip():
        os.environ[name] = value


def _split_env_list(value: str) -> list[str]:
    return [item.strip().rstrip("/") for item in value.split(",") if item.strip()]


def _local_proxy_endpoint(value: str) -> tuple[str, int] | None:
    normalized = value.strip()
    if not normalized:
        return None
    try:
        parsed = urlparse(
            normalized if "://" in normalized else f"http://{normalized}"
        )
        if parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.port is None:
            return None
        return parsed.hostname, parsed.port
    except ValueError:
        return None


def _proxy_endpoint_reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


def _read_windows_environment_value(name: str) -> str:
    if os.name != "nt":
        return ""
    try:
        import winreg
    except ImportError:
        return ""

    locations = (
        (winreg.HKEY_CURRENT_USER, "Environment"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
    )
    for hive, subkey in locations:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                value, _value_type = winreg.QueryValueEx(key, name)
        except OSError:
            continue
        normalized = str(value).strip()
        if normalized:
            return normalized
    return ""
