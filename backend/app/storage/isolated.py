"""Shared validation for disposable PostgreSQL test/evaluation databases."""

import json
import os
from pathlib import Path

from sqlalchemy.engine import make_url

RUNTIME_DATABASE_VARIABLES = ("LIMITUPLAB_DATABASE_URL",)
BACKEND_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


def isolated_database_url(raw, *, runtime_urls=None, env_path=BACKEND_ENV_PATH):
    """Reject business databases and routing overrides without exposing secrets."""
    try:
        selected = make_url(raw)
        if (selected.drivername not in {"postgresql", "postgresql+psycopg"}
                or not selected.host or not selected.database or "," in selected.host
                or set(selected.query) & {"host", "hostaddr", "port", "dbname", "database", "service"}):
            raise ValueError
        forbidden = json.loads(os.getenv("LIMITUPLAB_TEST_FORBIDDEN_DATABASES", "[]"))
        if not isinstance(forbidden, list) or not all(isinstance(name, str) for name in forbidden):
            raise ValueError
        names = {"limituplab", "postgres", "template0", "template1", *forbidden}
        runtime_urls = os.environ if runtime_urls is None else runtime_urls
        for key in RUNTIME_DATABASE_VARIABLES:
            configured = runtime_urls.get(key, "").strip()
            if configured:
                names.add(make_url(configured).database)
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8-sig").splitlines():
                key, separator, value = line.strip().partition("=")
                if separator and key in RUNTIME_DATABASE_VARIABLES and value.strip():
                    names.add(make_url(value.strip().strip("\"'")).database)
        if selected.database.casefold() in {name.casefold() for name in names if name}:
            raise ValueError("The isolated database must be separate from configured business databases")
        return selected.set(drivername="postgresql+psycopg").difference_update_query(["options"])
    except ValueError as error:
        if str(error).startswith("The isolated database"):
            raise
        raise ValueError("An explicit PostgreSQL test/evaluation URL without routing overrides is required") from None
    except Exception:
        raise ValueError("An explicit PostgreSQL test/evaluation URL without routing overrides is required") from None
