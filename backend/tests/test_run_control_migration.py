"""0007 adds control metadata without rewriting old conversations or checkpoints."""

from uuid import uuid4

from alembic import command
from alembic.config import Config
import sqlalchemy as sa

from app.storage.schema import BACKEND_ROOT, HEAD_REVISION, upgrade_schema
import database_support


def test_control_migration_preserves_old_runs_and_initializes_safe_defaults():
    schema = "pause_migration_" + uuid4().hex
    # conftest validated this dedicated base before injecting per-test runtime URLs.
    base = database_support._factory.base
    admin = sa.create_engine(base, poolclass=sa.pool.NullPool)
    url = base.update_query_dict({"options": f"-csearch_path={schema}"})
    engine = sa.create_engine(url, poolclass=sa.pool.NullPool)
    try:
        with admin.begin() as db:
            db.execute(sa.schema.CreateSchema(schema))
        with engine.begin() as db:
            config = Config(str(BACKEND_ROOT / "alembic.ini"))
            config.attributes["connection"] = db
            command.upgrade(config, "0006_long_term_memory")
            db.execute(sa.text("""INSERT INTO chat_sessions(session_id,owner_id,title,created_at,updated_at)
                VALUES('old-session','old-owner','原会话','2026-10-25','2026-10-25')"""))
            db.execute(sa.text("""INSERT INTO react_runs(run_id,owner_id,session_id,message_key,
                request_json,request_hash,started_at,thread_id,runtime_version,run_status)
                VALUES('old-run','old-owner','old-session','old-message','{}','original-hash',
                       '2026-10-25','old-session','durable-v1','interrupted')"""))
            db.execute(sa.text("""INSERT INTO checkpoints(thread_id,checkpoint_id,type,checkpoint,metadata)
                VALUES('old-session','old-cp','json',CAST(:payload AS JSONB),'{}')"""), {"payload": '{"v":4}'})
        upgrade_schema(engine)
        with engine.connect() as db:
            row = db.execute(sa.text("SELECT * FROM react_runs WHERE run_id='old-run'")).mappings().one()
            assert row["request_hash"] == "original-hash" and row["run_status"] == "interrupted" and row["active"] == 1
            assert row["control_revision"] == row["resume_count"] == 0
            assert row["pause_checkpoint_id"] is None and row["pause_mode"] is None
            assert db.execute(sa.text("SELECT checkpoint FROM checkpoints WHERE checkpoint_id='old-cp'")).scalar_one() == {"v": 4}
            assert db.execute(sa.text("SELECT count(*) FROM run_control_operations")).scalar_one() == 0
            assert db.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == HEAD_REVISION
    finally:
        engine.dispose()
        with admin.begin() as db:
            db.execute(sa.schema.DropSchema(schema, cascade=True, if_exists=True))
        admin.dispose()
