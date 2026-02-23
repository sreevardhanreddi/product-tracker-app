"""initial

Revision ID: 0001
Revises:
Create Date: 2024-01-01 00:00:00.000000
"""

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("url", sa.String(length=2000), nullable=False),
        sa.Column("platform", sa.String(length=50), nullable=False),
        sa.Column("target_price", sa.Float(), nullable=True),
        sa.Column("current_price", sa.Float(), nullable=True),
        sa.Column(
            "currency", sa.String(length=10), nullable=False, server_default="INR"
        ),
        sa.Column("image_url", sa.String(length=2000), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "check_interval_minutes", sa.Integer(), nullable=False, server_default="60"
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url"),
    )

    op.create_table(
        "price_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column(
            "currency", sa.String(length=10), nullable=False, server_default="INR"
        ),
        sa.Column("in_stock", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("scraped_at", sa.DateTime(), nullable=False),
        sa.Column("raw_price_text", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_price_history_product_id", "price_history", ["product_id"])

    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("triggered_price", sa.Float(), nullable=False),
        sa.Column("target_price", sa.Float(), nullable=False),
        sa.Column("channel", sa.String(length=50), nullable=False),
        sa.Column("message", sa.String(length=1000), nullable=False),
        sa.Column("sent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_alerts_product_id", "alerts", ["product_id"])


def downgrade() -> None:
    op.drop_table("alerts")
    op.drop_table("price_history")
    op.drop_table("products")
