"""create_approval_requests_table

Revision ID: dace7e21d19a
Revises: b3891c9472e1
Create Date: 2026-10-03 16:28:19.348651

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'dace7e21d19a'
down_revision: Union[str, Sequence[str], None] = 'b3891c9472e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    from sqlalchemy.engine.reflection import Inspector
    insp = Inspector.from_engine(conn)
    existing_tables = insp.get_table_names()

    if 'approval_requests' not in existing_tables:
        op.create_table(
            'approval_requests',
            sa.Column('id', sa.String(length=50), nullable=False),
            sa.Column('workflow_id', sa.Integer(), nullable=True),
            sa.Column('request_type', sa.String(length=100), nullable=False),
            sa.Column('title', sa.String(length=255), nullable=False),
            sa.Column('department', sa.String(length=150), nullable=True),
            sa.Column('applicant_emp_id', sa.String(length=50), nullable=True),
            sa.Column('applicant_name', sa.String(length=255), nullable=True),
            sa.Column('applicant_email', sa.String(length=150), nullable=True),
            sa.Column('applicant_phone', sa.String(length=50), nullable=True),
            sa.Column('start_date', sa.String(length=50), nullable=True),
            sa.Column('end_date', sa.String(length=50), nullable=True),
            sa.Column('purpose', sa.Text(), nullable=True),
            sa.Column('details', sa.Text(), nullable=True),
            sa.Column('status', sa.String(length=50), nullable=False, server_default='pending'),
            sa.Column('current_stage', sa.String(length=150), nullable=False, server_default='Department Head Review'),
            sa.Column('current_step_order', sa.Integer(), nullable=False, server_default='1'),
            sa.Column('total_steps', sa.Integer(), nullable=False, server_default='3'),
            sa.Column('remarks', sa.Text(), nullable=True),
            sa.Column('action_by', sa.String(length=100), nullable=True),
            sa.Column('action_at', sa.DateTime(), nullable=True),
            sa.Column('approval_history', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(['applicant_emp_id'], ['employees.employee_id'], ondelete='SET NULL'),
            sa.ForeignKeyConstraint(['workflow_id'], ['approval_workflows.id'], ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_approval_requests_applicant_emp_id'), 'approval_requests', ['applicant_emp_id'], unique=False)
        op.create_index(op.f('ix_approval_requests_status'), 'approval_requests', ['status'], unique=False)
        op.create_index(op.f('ix_approval_requests_workflow_id'), 'approval_requests', ['workflow_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_approval_requests_workflow_id'), table_name='approval_requests')
    op.drop_index(op.f('ix_approval_requests_status'), table_name='approval_requests')
    op.drop_index(op.f('ix_approval_requests_applicant_emp_id'), table_name='approval_requests')
    op.drop_table('approval_requests')
