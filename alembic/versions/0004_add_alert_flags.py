"""add alert enable flags to products and links

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-05 00:00:00.000000
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column(
            "alerts_enabled", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )
    op.add_column(
        "product_links",
        sa.Column(
            "alerts_enabled", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )
    op.alter_column("products", "alerts_enabled", server_default=None)
    op.alter_column("product_links", "alerts_enabled", server_default=None)


def downgrade() -> None:
    op.drop_column("product_links", "alerts_enabled")
    op.drop_column("products", "alerts_enabled")
