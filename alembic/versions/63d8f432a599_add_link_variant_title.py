"""add link variant title

Revision ID: 63d8f432a599
Revises: 8a351f14c5dd
Create Date: 2026-10-19 07:35:36.041435

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "63d8f432a599"
down_revision: Union[str, Sequence[str], None] = "8a351f14c5dd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "product_links",
        sa.Column("variant_title", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("product_links", "variant_title")
