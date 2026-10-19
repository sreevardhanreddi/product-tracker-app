"""move keychron.in links to shopify

Revision ID: 033aaced2692
Revises: 63d8f432a599
Create Date: 2026-10-19 08:27:15.034175

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "033aaced2692"
down_revision: Union[str, Sequence[str], None] = "63d8f432a599"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# keychron.in moved from WooCommerce to Shopify. Links were pinned to the
# "keychron" (WooCommerce) platform by hostname, and scrapes trust the stored
# platform, so new-style /products/ URLs kept going to the WooCommerce scraper,
# whose page-text stock check read other variants' "Sold out" as this one's.
# Old /product/ (WooCommerce) URLs now 404 and are left alone for the user to
# replace.
_TABLES = ("product_links", "products")
_SHOPIFY_URL = "url ILIKE '%keychron.in/products/%'"


def upgrade() -> None:
    """Upgrade schema."""
    for table in _TABLES:
        op.execute(
            f"UPDATE {table} SET platform = 'shopify' "
            f"WHERE platform = 'keychron' AND {_SHOPIFY_URL}"
        )


def downgrade() -> None:
    """Downgrade schema."""
    # Exact inverse: before this revision every keychron.in link was "keychron".
    for table in _TABLES:
        op.execute(
            f"UPDATE {table} SET platform = 'keychron' "
            f"WHERE platform = 'shopify' AND {_SHOPIFY_URL}"
        )
