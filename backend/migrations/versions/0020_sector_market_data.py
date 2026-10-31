"""Persist the sector catalog, daily board metrics and constituent cache."""

from alembic import op


revision = "0020_sector_market_data"
down_revision = "0019_review_judge_calls"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE sector_catalog (
        thscode TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        category TEXT NOT NULL CHECK(category IN ('industry','cn_concept')),
        in_summary INTEGER NOT NULL DEFAULT 0,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())""")
    op.create_index(
        "sector_catalog_category_name",
        "sector_catalog",
        ["category", "name"],
    )
    op.create_index(
        "sector_catalog_summary",
        "sector_catalog",
        ["category", "in_summary"],
    )
    op.execute("""CREATE TABLE sector_daily (
        trade_date DATE NOT NULL,
        thscode TEXT NOT NULL REFERENCES sector_catalog(thscode) ON DELETE CASCADE,
        close DOUBLE PRECISION,
        change_pct DOUBLE PRECISION,
        amount_yi DOUBLE PRECISION,
        net_inflow_yi DOUBLE PRECISION,
        up_count INTEGER,
        down_count INTEGER,
        leader_name TEXT,
        leader_price DOUBLE PRECISION,
        leader_change_pct DOUBLE PRECISION,
        source TEXT NOT NULL,
        captured_at TIMESTAMPTZ,
        PRIMARY KEY (trade_date, thscode))""")
    op.create_index(
        "sector_daily_rank",
        "sector_daily",
        ["trade_date", "change_pct"],
    )
    op.execute("""CREATE TABLE sector_constituents (
        thscode TEXT NOT NULL REFERENCES sector_catalog(thscode) ON DELETE CASCADE,
        symbol TEXT NOT NULL,
        name TEXT NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (thscode, symbol))""")
    op.create_index(
        "sector_constituents_symbol",
        "sector_constituents",
        ["symbol"],
    )


def downgrade():
    raise RuntimeError(
        "Sector market data is derived evidence; restore a verified backup to downgrade"
    )
