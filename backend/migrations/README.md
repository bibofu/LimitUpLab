# PostgreSQL schema migrations

PostgreSQL schema changes are deploy-time Alembic revisions. Application startup
checks the revision and never creates or upgrades PostgreSQL tables. Runtime
storage requires a PostgreSQL URL.

The frozen `app/storage/schema_v0001.json` describes the 41-table baseline. Do not
regenerate it when application models change: add a hand-written Alembic revision
and update `HEAD_REVISION` instead. Historical JSON, hashes and timestamps remain text,
SQLite REAL becomes double precision, and integer columns become bigint.
`chat_messages` and `react_calls` preserve SQLite rowid in `legacy_order`; new
records receive database-generated identity values. Revision 0002 adds shared
coordination tables, revision 0003 enforces one champion policy, and revision
0004 drops 12 retired answer-quality tables, 0005 adds native Agent checkpoints,
and 0006 adds owner-scoped long-term memory. Archived `react_calls` and
`react_runs.checkpoint_json` remain historical data only; no current runtime writes them.

There is no ORM metadata model of the current schema. Alembic's `target_metadata`
is deliberately `None`; `revision --autogenerate` and `check` are explicitly
disabled. Comparing the frozen baseline with the live schema would propose
restoring retired tables and removing newer checkpoint/memory tables. Create an
empty revision with `alembic revision -m "describe the change"`, implement its
upgrade explicitly, and verify both fresh installation and upgrade from the
previous revision in an isolated PostgreSQL schema.

SQLite migration scripts and the SQLite importer were removed on 2026-10-24.
Historical SQLite archives remain read-only evidence; the application does not
run against SQLite.

## Prepare

Set `LIMITUPLAB_DATABASE_URL` to a dedicated PostgreSQL target, keeping credentials
outside source control. Run:

```powershell
backend/.venv/Scripts/python.exe -m alembic -c backend/alembic.ini upgrade head
```

## Verification

Set `LIMITUPLAB_TEST_DATABASE_URL` to a disposable PostgreSQL test database and run
`pytest tests/test_postgresql_schema.py tests/test_postgresql_migration.py` from
`backend`. Integration tests create and drop only uniquely named test schemas.
They exercise schema creation, retired quality table removal and champion
uniqueness.

Recovery uses a PostgreSQL backup restored to a separate empty database and
independently verified. There is no destructive downgrade or automatic reverse
synchronization.

## Historical rehearsal evidence, 2026-10-19

A retained local SQLite backup was imported into a separate PostgreSQL 18
database; 691,526 business rows matched source checksums across the 41-table
baseline, and four coordination tables were empty. The custom-format backup was
then restored into another empty database with matching checksums. Generated
evidence remains under ignored `output/pg-migration-report.json` and
`output/pg-restore-verification.json`. The original SQLite database and backup
were not switched or removed.
