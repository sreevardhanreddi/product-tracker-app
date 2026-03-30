"""add last_scrape_method to product_links

Revision ID: 0003
Revises: 0002
Create Date: 2026-03-30 00:00:00.000000
"""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "product_links",
        sa.Column("last_scrape_method", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("product_links", "last_scrape_method")
