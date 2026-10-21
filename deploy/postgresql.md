# PostgreSQL deployment and recovery

Application, release and scheduled-job helpers require PostgreSQL. The base
`docker-compose.yml` requires an explicit application URL; combine it with the
PostgreSQL overlay to run the managed database and migration service using their
separate named volume. There is no runtime SQLite mode or missing-URL fallback.
This runbook prepares shared persistence; load-balancer behavior and rolling
multi-instance deployment still require their own acceptance test.

The production single-instance cutover completed on 2026-10-19 with `v1.6.1`.
See the [acceptance record](incidents/2026-10-19-postgresql-cutover.md) for the
actual import, retained SQLite, restart, restore and scheduled-backup evidence.

## Prepare the target

1. Keep the real `.env.production` settings, including one shared session secret
   and admin key for every API instance. Copy `.env.postgresql.example` to
   `.env.postgresql`. For production, override its single-account example:
   set `POSTGRES_USER=limituplab_admin` with a unique bootstrap password, and
   point `LIMITUPLAB_DATABASE_URL` at the separate `limituplab` application role
   with a different password. URL-encode reserved characters in the URL password.
   The official image creates `POSTGRES_USER` as a superuser; do not use that
   bootstrap role for application requests. Keep real files mode `0600`.
2. Build the backend image. It includes PostgreSQL 18 clients, Alembic migrations
   and the independent `scripts/postgres_admin.py` command.
3. Start only the database and schema migration service while preparing import.

```sh
docker compose --env-file .env.postgresql -f docker-compose.yml -f docker-compose.postgresql.yml build backend
docker compose --env-file .env.postgresql -f docker-compose.yml -f docker-compose.postgresql.yml up -d postgres
```

For a new empty target, open `psql` as the bootstrap administrator before running
the migration service:

```sh
docker compose --env-file .env.postgresql -f docker-compose.yml -f docker-compose.postgresql.yml exec postgres \
  psql -X -U limituplab_admin -d postgres
```

```sql
CREATE ROLE limituplab LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER DATABASE limituplab OWNER TO limituplab;
\password limituplab
```

The interactive password prompt avoids embedding a real password in shell or
SQL history. Set the application URL to this role/password, exit `psql`, then
run the migration service. These role-creation steps are for first provisioning;
do not repeat them against an existing deployment without inspecting its roles.

```sh
docker compose --env-file .env.postgresql -f docker-compose.yml -f docker-compose.postgresql.yml run --rm postgres-migrate
```

PostgreSQL is not exposed on a host port. The `limituplab-postgres` volume stores
its data. The existing `limituplab-data` volume still stores reports and migration
evidence and must not be removed as part of SQLite cleanup. Retain a verified
consistent SQLite archive; runtime-file retirement is tracked in
[SQLite retirement](../docs/SQLite_Retirement.md). Never use
`docker compose down -v` during migration or recovery.

PostgreSQL 18's official image stores data under `/var/lib/postgresql/18/docker`,
so the volume is mounted at `/var/lib/postgresql`, following the
[official image layout](https://hub.docker.com/_/postgres).
Runtime, CI service and backup clients use the same major version.

The overlay runs Alembic as a one-shot service before application processes. The
upgrade entry point serializes competing deploy-time migrations. Requests only
verify the schema revision. Future schema changes should use compatible additive
migrations before rolling out code that requires them.

## Import retained SQLite archives

Perform a rehearsal against a separate empty PostgreSQL database. Follow
`backend/migrations/README.md` for table inventory, legacy-table acknowledgments,
and import verification. For final cutover:

1. Stop public write traffic and all SQLite-writing workers and scheduled jobs.
2. Wait for active runs to finish or record their checkpoints. Preserve a
   consistent SQLite archive with `scripts/archive_sqlite.py --source <source>
   --output-dir <archive-directory>`, which uses SQLite's backup API. The general
   backup command backs up PostgreSQL; it does not open a SQLite runtime database.
3. Run the importer against the empty PostgreSQL target. The following command
   mounts the retained SQLite volume via the existing backend service:

```sh
docker compose --env-file .env.postgresql -f docker-compose.yml -f docker-compose.postgresql.yml run --rm --no-deps backend \
  python scripts/migrate_sqlite_to_postgres.py \
  --source /app/data/limituplab.sqlite --report /app/data/backups/postgresql-import-report.json \
  --retain-legacy-table auction_final_recommendation_snapshots \
  --retain-legacy-table auth_challenges --retain-legacy-table auth_sessions \
  --retain-legacy-table user_identities --retain-legacy-table users
```

Inventory the actual source first. The production source also retained
`first_board_discovery_snapshots`; its six retired records were explicitly
acknowledged with another `--retain-legacy-table` argument and remain in SQLite.
Do not blindly acknowledge an unexpected table without checking its role.

4. Review the report. Every imported table must be `verified`; legacy records are
   explicitly retained in the original SQLite file. Reusing a populated target
   or an existing report path is rejected.
5. Start one API instance and perform health/readiness checks, at least three
   actual Agent questions, prediction/history checks, SSE reconnection and task
   completion checks before reopening writes. Then start background workers.

```sh
docker compose --env-file .env.postgresql -f docker-compose.yml -f docker-compose.postgresql.yml up -d backend frontend
docker compose --env-file .env.postgresql -f docker-compose.yml -f docker-compose.postgresql.yml up -d recommendation-refresh
```

`quality-worker` and `quality-judge-worker` are optional model-evaluation services.
Enable them only when explicitly included in the deployment scope and configured;
the `v1.6.1` production cutover did not start them.

Install the reviewed `deploy/release.py` and `deploy/job.py` before changing the
production database mode. These root-owned helpers are not automatically replaced
by a tag deployment. Hold `/var/lock/limituplab-release.lock` and retain the
maintenance marker throughout the first cutover; stop all configured database
writers, including any optional quality workers.

After import and single-instance acceptance, write
`/var/lib/limituplab-deploy/database.json` as `{"engine":"postgresql"}` and set
the active `current.json` record's `engine` to `postgresql` under that same lock.
Keep the real PostgreSQL settings in `/opt/LimitUpLab/.env.postgresql` with
restricted permissions. The release helpers always load both `.env.production`
and `.env.postgresql` plus the PostgreSQL overlay in this mode. Missing settings,
an unsupported release, a conflicting marker or a different running backend
engine blocks the operation. SQLite markers are not accepted by current helpers.

Subsequent tag deployments pin `postgres-migrate` to the release's backend image,
take a verified deployment-specific PostgreSQL archive, compare schema hashes
and Alembic revisions, and require `/ready` to confirm PostgreSQL. Only an
unchanged schema and the same engine permit automatic application rollback.
The release command does not perform the first SQLite data import or reverse a
database cutover. See [tag deployment](Tag_Deployment.md) for the release gates.

## Back up and prove recovery

Run the administrative CLI inside the backend image or a host with matching
PostgreSQL client utilities. It reads connection credentials only from
`LIMITUPLAB_DATABASE_URL` and places client passwords in subprocess environment
variables, never command arguments or reports.

```sh
python scripts/postgres_admin.py status
python scripts/postgres_admin.py backup --output /app/data/backups/limituplab-pg-20261019.dump
python scripts/postgres_admin.py verify-backup --archive /app/data/backups/limituplab-pg-20261019.dump
```

Backup creates a custom-format archive plus a `.sha256` checksum, refuses to
overwrite prior files and verifies that `pg_restore` can read the archive. This
checks archive structure; only a successful restore proves recoverability.
Copy both files to independently retained storage. This command deliberately
does not prune backups or configure scheduling, PITR or high availability.

For a recovery rehearsal, provision a separate empty database, point
`LIMITUPLAB_DATABASE_URL` to it, and run:

```sh
python scripts/postgres_admin.py restore --archive /path/to/limituplab-pg-20261019.dump
python scripts/postgres_admin.py status
```

Restore checks the checksum, rejects databases containing user tables, views or
sequences, and runs in a single transaction. It uses neither `--clean` nor
`--create`; it cannot erase an existing deployment. After restore, compare the
relevant business counts/hashes and run the same Agent/readiness checks.

Retained SQLite is a migration-time archive and excludes subsequent writes.
Current application code cannot run against it. Recover by restoring a
PostgreSQL backup into a new empty database, verify its contents and readiness,
then explicitly switch the application URL. This procedure does not overwrite
the running database or reinterpret old SQLite as current data.

## Prepare additional API instances

Use the same PostgreSQL database and session/admin secrets across instances.
Budget total connections across API processes, collection workers and evaluators:
each process can open `LIMITUPLAB_DB_POOL_SIZE + LIMITUPLAB_DB_MAX_OVERFLOW`
connections. Defaults are 5 + 5; increase instance count only within the server's
connection budget.

Verify two instances with concurrent duplicate requests, shared quotas, worker
termination/lease expiry, fenced completion, SSE reconnection to the other
instance, cancellation, and shared scheduled jobs. Confirm the frontend proxy
actually distributes requests and survives backend replacement. These checks
are required before enabling production scaling; installing PostgreSQL alone
does not establish that acceptance result.
