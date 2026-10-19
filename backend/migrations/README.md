# PostgreSQL schema and retained SQLite migration

PostgreSQL schema changes are deploy-time Alembic revisions. Application startup
checks the revision and never creates or upgrades PostgreSQL tables. SQLite
remains available through the existing database-path setting; the importer opens
its source read-only and never changes or deletes that database.

The frozen `app/storage/schema_v0001.json` describes all 41 existing business
tables. Do not regenerate it when application models change: add a new Alembic
revision and update head metadata instead. Historical JSON, hashes and timestamps
remain text, SQLite REAL becomes double precision, and integer columns become
bigint. `chat_messages` and `react_calls` preserve SQLite rowid in `legacy_order`;
new records receive database-generated identity values. Revision 0002 adds shared
coordination tables. Revision 0003 enforces one champion policy.

## Prepare and rehearse

Set `LIMITUPLAB_DATABASE_URL` to a dedicated PostgreSQL target, keeping credentials
outside source control. Run these commands from the repository root:

```powershell
backend/.venv/Scripts/python.exe -m alembic -c backend/alembic.ini upgrade head
backend/.venv/Scripts/python.exe backend/scripts/migrate_sqlite_to_postgres.py `
  --source backend/data/limituplab.sqlite `
  --report backend/data/backups/postgresql-import-report.json `
  --retain-legacy-table auction_final_recommendation_snapshots `
  --retain-legacy-table auth_challenges `
  --retain-legacy-table auth_sessions `
  --retain-legacy-table user_identities `
  --retain-legacy-table users
```

Those five legacy names reflect the inspected local database. Other sources may
have different legacy tables. Only acknowledge tables after checking that current
application code no longer uses them. Their schema, row count and content checksum
are included in the report; their complete records remain in the retained SQLite
file. The importer rejects unacknowledged legacy tables and schema differences.

The target must contain no business rows. Import holds destination table locks,
checks every imported row and field using ordered SHA-256 checksums, verifies
foreign keys, resets identity sequences and commits as one transaction. A retry
against an already populated target fails safely; it never truncates anything.
Multiple champion policies require explicit source repair before migration.

Use an externally created consistent SQLite backup for rehearsal if desired.
During final cutover, stop application writes and background workers before
starting the importer, and retain the original SQLite database and any associated
backup. Read-only snapshotting guarantees a consistent import but cannot include
SQLite commits made after the snapshot begins.

## Verification and recovery

Set `LIMITUPLAB_TEST_DATABASE_URL` to a disposable PostgreSQL test database and run
`pytest tests/test_postgresql_schema.py tests/test_postgresql_migration.py` from
`backend`. Integration tests create and drop only uniquely named test schemas.
They exercise row order, literal JSON/hash preservation, identity continuation,
foreign-key rollback, immutable evidence and champion uniqueness.

After import, check the report, run application smoke tests, and only then reopen
writes. Before PostgreSQL receives new writes, revert application configuration
to the retained SQLite database if necessary. After it receives new writes,
SQLite is no longer current: stop writes and reconcile those records before any
rollback. This tool intentionally provides no destructive downgrade or automatic
reverse synchronization. PostgreSQL backup/restore is a separate operational step.
