"""Shared execution leases, rate limits and request slots."""

import sqlalchemy as sa
from alembic import op

from app.storage.schema import add_coordination_metadata

revision = "0002_runtime_coordination"
down_revision = "0001_postgresql_baseline"
branch_labels = None
depends_on = None


def upgrade():
    metadata = sa.MetaData()
    add_coordination_metadata(metadata)
    metadata.create_all(op.get_bind(), checkfirst=False)


def downgrade():
    raise RuntimeError("Coordination downgrade needs a drained deployment and verified backup")
