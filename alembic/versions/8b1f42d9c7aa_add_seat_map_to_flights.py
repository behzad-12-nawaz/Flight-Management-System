"""add seat_map to flights

Revision ID: 8b1f42d9c7aa
Revises: e3113c01cbdd
Create Date: 2026-09-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '8b1f42d9c7aa'
down_revision: Union[str, None] = 'e3113c01cbdd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'flights',
        sa.Column('seat_map', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('flights', 'seat_map')
