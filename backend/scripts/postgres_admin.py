"""Explicit PostgreSQL status, upgrade, backup and empty-database restore tools."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy as sa
from sqlalchemy.engine import make_url

from app.storage.schema import assert_schema_current, upgrade_schema


class AdminError(ValueError):
    pass


def connection_environment(raw_url):
    """Keep passwords out of process arguments, logs and returned metadata."""
    url = make_url(raw_url)
    if url.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise AdminError("LIMITUPLAB_DATABASE_URL must select PostgreSQL")
    if not url.database:
        raise AdminError("An explicit PostgreSQL database name is required")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    environment.update(PGHOST=url.host or "localhost", PGPORT=str(url.port or 5432),
                       PGDATABASE=url.database, PGUSER=url.username or "postgres",
                       PGPASSWORD=url.password or "", PGCONNECT_TIMEOUT="10")
    allowed = {"sslmode": "PGSSLMODE", "sslrootcert": "PGSSLROOTCERT", "sslcert": "PGSSLCERT",
               "sslkey": "PGSSLKEY", "connect_timeout": "PGCONNECT_TIMEOUT",
               "target_session_attrs": "PGTARGETSESSIONATTRS", "options": "PGOPTIONS"}
    for key, value in url.query.items():
        if key not in allowed or not isinstance(value, str):
            raise AdminError(f"Unsupported PostgreSQL URL option: {key}")
        environment[allowed[key]] = value
    return url.set(drivername="postgresql+psycopg"), environment


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def client(command, environment, *, output=None):
    executable = shutil.which(command[0], path=environment.get("PATH"))
    if executable is None:
        raise AdminError(f"Required PostgreSQL client is unavailable: {command[0]}")
    # Windows process creation does not resolve executables using the child's
    # custom PATH. Resolve first so portable client installations work there too.
    result = subprocess.run([executable, *command[1:]], env=environment, stdout=output or subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False)
    if result.returncode:
        # Server/client error strings may contain endpoints, SQL or private rows.
        raise AdminError(f"{command[0]} failed with exit status {result.returncode}")
    return result


def backup_database(output, environment):
    output = Path(output).resolve()
    checksum = output.with_suffix(output.suffix + ".sha256")
    if output.exists() or checksum.exists():
        raise AdminError("Backup or checksum file already exists; choose a new filename")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents accidentally overwriting an earlier backup.
    with output.open("xb") as archive:
        output.chmod(0o600)
        client(["pg_dump", "--format=custom", "--no-owner", "--no-privileges"], environment, output=archive)
    digest = file_hash(output)
    with checksum.open("x", encoding="ascii") as manifest:
        manifest.write(digest + "\n")
    checksum.chmod(0o600)
    client(["pg_restore", "--list", str(output)], environment)
    return {"backup": str(output), "sha256": digest, "archive_readable": True}


def verify_backup(archive, environment):
    archive = Path(archive).resolve(strict=True)
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    if not checksum.is_file() or checksum.read_text(encoding="ascii").strip() != file_hash(archive):
        raise AdminError("Backup checksum is missing or does not match")
    client(["pg_restore", "--list", str(archive)], environment)
    return archive


def restore_database(archive, engine, environment):
    archive = verify_backup(archive, environment)
    with engine.connect() as connection:
        count = connection.scalar(sa.text("""SELECT count(*) FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname NOT IN ('pg_catalog','information_schema')
              AND n.nspname NOT LIKE 'pg_toast%' AND c.relkind IN ('r','p','v','m','f','S')"""))
        if count:
            raise AdminError("Restore destination is not empty; this tool never drops existing objects")
    # Provision a separate empty database for recovery. No --clean/--create flags.
    client(["pg_restore", "--dbname", environment["PGDATABASE"], "--single-transaction",
            "--exit-on-error", "--no-owner", "--no-privileges", str(archive)], environment)
    with engine.connect() as connection:
        assert_schema_current(connection)
    return {"restored": True, "schema_current": True}


def main():
    from app.config import load_local_env
    load_local_env()
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("upgrade")
    commands.add_parser("backup").add_argument("--output", type=Path, required=True)
    commands.add_parser("verify-backup").add_argument("--archive", type=Path, required=True)
    commands.add_parser("restore").add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    engine = None
    try:
        url, environment = connection_environment(os.environ.get("LIMITUPLAB_DATABASE_URL", ""))
        engine = sa.create_engine(url, poolclass=sa.pool.NullPool, hide_parameters=True)
        if args.command == "upgrade":
            upgrade_schema(engine)
            result = {"schema_current": True}
        elif args.command == "backup":
            result = backup_database(args.output, environment)
        elif args.command == "verify-backup":
            result = {"archive": str(verify_backup(args.archive, environment)), "verified": True}
        elif args.command == "restore":
            result = restore_database(args.archive, engine, environment)
        else:
            with engine.connect() as connection:
                assert_schema_current(connection)
                version = connection.scalar(sa.text("SHOW server_version"))
            result = {"schema_current": True, "server_version": version}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except AdminError as error:
        print(str(error), file=sys.stderr)
    except Exception as error:
        print(f"PostgreSQL operation failed ({type(error).__name__}); no credentials printed.", file=sys.stderr)
    finally:
        if engine is not None:
            engine.dispose()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
