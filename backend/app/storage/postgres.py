"""Pooled PostgreSQL transport; only parameter binding is adapted, never SQL semantics."""

from datetime import date, datetime
from functools import lru_cache
import hashlib
import os
import threading
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

_engines = {}
_engine_lock = threading.Lock()


def configured_url():
    return os.getenv("LIMITUPLAB_DATABASE_URL", "").strip()


def get_engine(url=None):
    value = make_url(url or configured_url())
    if value.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise ValueError("LIMITUPLAB_DATABASE_URL must use PostgreSQL")
    value = value.set(drivername="postgresql+psycopg")
    key = (os.getpid(), value)
    with _engine_lock:
        if key not in _engines:
            _engines[key] = create_engine(
                value, pool_size=int(os.getenv("LIMITUPLAB_DB_POOL_SIZE", "5")),
                max_overflow=int(os.getenv("LIMITUPLAB_DB_MAX_OVERFLOW", "5")),
                pool_timeout=float(os.getenv("LIMITUPLAB_DB_POOL_TIMEOUT", "10")),
                pool_pre_ping=True, pool_recycle=1800, hide_parameters=True,
                connect_args={"connect_timeout": 10,
                    "application_name": "limituplab",
                    "options": "-c timezone=UTC -c statement_timeout=30000 -c lock_timeout=10000 -c idle_in_transaction_session_timeout=60000 " + value.query.get("options", "")})
        return _engines[key]


def dispose_engines():
    with _engine_lock:
        for engine in _engines.values():
            engine.dispose()
        _engines.clear()


@lru_cache(maxsize=1024)
def bind_qmarks(sql):
    """Lex strings, quoted names, dollar literals and nested SQL comments."""
    output, count, index = [], 0, 0
    quote, dollar, block = None, None, 0
    while index < len(sql):
        char, pair = sql[index], sql[index:index + 2]
        if dollar:
            if sql.startswith(dollar, index):
                output.append(dollar); index += len(dollar); dollar = None
            else:
                output.append(char); index += 1
            continue
        if block:
            if pair == "/*":
                block += 1; output.append(pair); index += 2
            elif pair == "*/":
                block -= 1; output.append(pair); index += 2
            else:
                output.append(char); index += 1
            continue
        if quote:
            output.append(char); index += 1
            if char == quote:
                if index < len(sql) and sql[index] == quote:
                    output.append(quote); index += 1
                else:
                    quote = None
            continue
        if pair == "--":
            end = sql.find("\n", index)
            end = len(sql) if end == -1 else end
            output.append(sql[index:end]); index = end; continue
        if pair == "/*":
            block = 1; output.append(pair); index += 2; continue
        if char in {"'", '"'}:
            quote = char; output.append(char); index += 1; continue
        if char == "$":
            end = sql.find("$", index + 1)
            tag = sql[index + 1:end] if end != -1 else None
            if tag is not None and (not tag or (not tag[0].isdigit() and all(c.isalnum() or c == "_" for c in tag))):
                dollar = sql[index:end + 1]; output.append(dollar); index = end + 1; continue
        if char == "?":
            output.append("\x00"); count += 1
        else:
            output.append(char)
        index += 1
    if quote or dollar or block:
        raise ValueError("Unterminated SQL literal or comment")
    return "".join(output).replace("%", "%%").replace("\x00", "%s"), count


def _parameters(values):
    return tuple(int(v) if isinstance(v, bool) else v.isoformat()
                 if isinstance(v, (date, datetime)) else v for v in values)


class Record:
    """Support numeric/key access, iteration over values and dict(row)."""
    def __init__(self, names, values):
        self.names, self.values = names, values

    def keys(self):
        return self.names

    def __getitem__(self, key):
        return self.values[key if isinstance(key, (int, slice)) else self.names.index(key)]

    def __iter__(self):
        return iter(self.values)

    def __len__(self):
        return len(self.values)


class Result:
    def __init__(self, cursor):
        self.cursor = cursor
        self.names = tuple(c.name for c in cursor.description) if cursor.description else ()

    @property
    def rowcount(self):
        return self.cursor.rowcount

    def fetchone(self):
        row = self.cursor.fetchone()
        return Record(self.names, row) if row is not None else None

    def fetchall(self):
        return [Record(self.names, row) for row in self.cursor.fetchall()]

    def fetchmany(self, size=1000):
        return [Record(self.names, row) for row in self.cursor.fetchmany(size)]

    def __iter__(self):
        while (row := self.fetchone()) is not None:
            yield row


class PostgresConnection:
    dialect = "postgresql"

    def __init__(self, url=None, *, readonly=False):
        self.sa_engine = get_engine(url)
        self._pooled = self.sa_engine.raw_connection()
        self._db = self._pooled.driver_connection
        self._db.autocommit = True
        self._write_locked, self._readonly, self._closed = False, readonly, False
        if readonly:
            self._db.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")

    @property
    def in_transaction(self):
        from psycopg.pq import TransactionStatus
        return self._db.info.transaction_status != TransactionStatus.IDLE

    def begin_write(self, scope="write"):
        if self._readonly:
            raise PermissionError("Cannot write through a read-only connection")
        if self._write_locked:
            return
        if not self.in_transaction:
            self._db.execute("BEGIN")
        # Initially retain the existing global read-modify-write contract.
        # Narrower scopes require independent concurrency acceptance tests.
        key = int.from_bytes(hashlib.sha256(b"limituplab:write").digest()[:8], "big", signed=True)
        self._db.execute("SELECT pg_advisory_xact_lock(%s)", (key,))
        self._write_locked = True

    def execute(self, sql, parameters=()):
        command = sql.lstrip().split(None, 1)[0].upper()
        if command in {"PRAGMA", "REPLACE"} or sql.strip().upper() == "BEGIN IMMEDIATE":
            raise ValueError("SQLite-specific SQL must be explicitly adapted")
        if command in {"INSERT", "UPDATE", "DELETE"}:
            self.begin_write()
        if self._readonly and command not in {"SELECT", "WITH", "EXPLAIN"}:
            raise PermissionError("Read-only SQL connection")
        if parameters:
            statement, count = bind_qmarks(sql)
            bound = _parameters(parameters)
            if count != len(bound):
                raise ValueError("SQL parameter count mismatch")
            cursor = self._db.execute(statement, bound)
        else:
            cursor = self._db.execute(sql)
        return Result(cursor)

    def executemany(self, sql, parameters):
        self.begin_write()
        statement, count = bind_qmarks(sql)
        def records():
            for values in parameters:
                row = _parameters(values)
                if len(row) != count:
                    raise ValueError("SQL parameter count mismatch")
                yield row
        cursor = self._db.cursor()
        cursor.executemany(statement, records())
        return Result(cursor)

    def commit(self):
        self._db.commit()
        self._write_locked = False

    def rollback(self):
        self._db.rollback()
        self._write_locked = False

    def close(self):
        if not self._closed:
            try:
                self.rollback()
            finally:
                self._closed = True
                if not self._db.closed:
                    self._db.autocommit = False
                self._pooled.close()

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        if kind:
            self.rollback()
        else:
            try:
                self.commit()
            except Exception:
                self.rollback()
                raise
