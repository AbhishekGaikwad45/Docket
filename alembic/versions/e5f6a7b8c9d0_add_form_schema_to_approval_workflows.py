"""add_form_schema_to_approval_workflows

Revision ID: e5f6a7b8c9d0
Revises: dace7e21d19a
Create Date: 2026-10-06 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'dace7e21d19a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    from sqlalchemy.engine.reflection import Inspector
    insp = Inspector.from_engine(conn)
    cols = [c['name'] for c in insp.get_columns('approval_workflows')]
    if 'form_schema' not in cols:
        op.add_column('approval_workflows', sa.Column('form_schema', sa.Text(), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    from sqlalchemy.engine.reflection import Inspector
    insp = Inspector.from_engine(conn)
    cols = [c['name'] for c in insp.get_columns('approval_workflows')]
    if 'form_schema' in cols:
        op.drop_column('approval_workflows', 'form_schema')
