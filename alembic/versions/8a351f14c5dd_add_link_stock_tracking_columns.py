"""add link stock tracking columns

Revision ID: 8a351f14c5dd
Revises: 0004
Create Date: 2026-07-23 05:53:14.606518

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8a351f14c5dd"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("product_links", sa.Column("in_stock", sa.Boolean(), nullable=True))
    op.add_column(
        "product_links", sa.Column("last_in_stock_price", sa.Float(), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("product_links", "last_in_stock_price")
    op.drop_column("product_links", "in_stock")
