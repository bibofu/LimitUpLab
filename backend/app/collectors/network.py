"""Shared network helpers for market-data collectors."""

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager


_proxy_environment_lock = threading.RLock()


@contextmanager
def without_proxy() -> Iterator[None]:
    """Temporarily clear proxy variables for providers that reject the proxy."""

    with _proxy_environment_lock:
        proxy_names = (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
        )
        previous = {name: os.environ.get(name) for name in proxy_names}
        try:
            for name in proxy_names:
                os.environ.pop(name, None)
            yield
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
