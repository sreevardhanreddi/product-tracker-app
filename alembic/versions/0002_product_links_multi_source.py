"""add product links and per-link price history

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-05 00:00:00.000000
"""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=2000), nullable=False),
        sa.Column("platform", sa.String(length=50), nullable=False),
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
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url"),
    )
    op.create_index("ix_product_links_product_id", "product_links", ["product_id"])

    op.add_column(
        "price_history", sa.Column("product_link_id", sa.Integer(), nullable=True)
    )
    op.create_index(
        "ix_price_history_product_link_id", "price_history", ["product_link_id"]
    )
    op.create_foreign_key(
        "fk_price_history_product_link_id_product_links",
        "price_history",
        "product_links",
        ["product_link_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Backfill one link per existing product from legacy single-url columns.
    op.execute(
        """
        INSERT INTO product_links (
            product_id,
            url,
            platform,
            current_price,
            currency,
            image_url,
            is_active,
            check_interval_minutes,
            created_at,
            updated_at,
            last_checked_at
        )
        SELECT
            p.id,
            p.url,
            p.platform,
            p.current_price,
            p.currency,
            p.image_url,
            p.is_active,
            p.check_interval_minutes,
            p.created_at,
            p.updated_at,
            p.last_checked_at
        FROM products p
        """
    )

    op.execute(
        """
        UPDATE price_history ph
        SET product_link_id = pl.id
        FROM product_links pl
        WHERE ph.product_id = pl.product_id
          AND ph.product_link_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_price_history_product_link_id_product_links",
        "price_history",
        type_="foreignkey",
    )
    op.drop_index("ix_price_history_product_link_id", table_name="price_history")
    op.drop_column("price_history", "product_link_id")

    op.drop_index("ix_product_links_product_id", table_name="product_links")
    op.drop_table("product_links")
