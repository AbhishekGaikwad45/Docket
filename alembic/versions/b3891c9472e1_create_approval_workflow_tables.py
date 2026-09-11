"""create_approval_workflow_tables

Revision ID: b3891c9472e1
Revises: f8e08c4e8515
Create Date: 2026-09-11 13:12:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector


# revision identifiers, used by Alembic.
revision: str = 'b3891c9472e1'
down_revision: Union[str, Sequence[str], None] = 'f8e08c4e8515'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    insp = Inspector.from_engine(conn)
    existing_tables = insp.get_table_names()

    # 1. approval_workflows table
    if 'approval_workflows' not in existing_tables:
        op.create_table(
            'approval_workflows',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('name', sa.String(length=150), nullable=False),
            sa.Column('code', sa.String(length=100), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('flow_data', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_approval_workflows_code'), 'approval_workflows', ['code'], unique=True)
        op.create_index(op.f('ix_approval_workflows_name'), 'approval_workflows', ['name'], unique=True)

    # 2. approval_workflow_steps table
    if 'approval_workflow_steps' not in existing_tables:
        op.create_table(
            'approval_workflow_steps',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('workflow_id', sa.Integer(), nullable=False),
            sa.Column('step_order', sa.Integer(), nullable=False, server_default='1'),
            sa.Column('step_name', sa.String(length=150), nullable=False),
            sa.Column('is_final', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('parent_step_id', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(['parent_step_id'], ['approval_workflow_steps.id'], ondelete='SET NULL'),
            sa.ForeignKeyConstraint(['workflow_id'], ['approval_workflows.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_approval_workflow_steps_workflow_id'), 'approval_workflow_steps', ['workflow_id'], unique=False)

    # 3. approval_step_approvers table
    if 'approval_step_approvers' not in existing_tables:
        op.create_table(
            'approval_step_approvers',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('step_id', sa.Integer(), nullable=False),
            sa.Column('employee_id', sa.String(length=50), nullable=False),
            sa.Column('role_label', sa.String(length=100), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(['employee_id'], ['employees.employee_id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['step_id'], ['approval_workflow_steps.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_approval_step_approvers_employee_id'), 'approval_step_approvers', ['employee_id'], unique=False)
        op.create_index(op.f('ix_approval_step_approvers_step_id'), 'approval_step_approvers', ['step_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_approval_step_approvers_step_id'), table_name='approval_step_approvers')
    op.drop_index(op.f('ix_approval_step_approvers_employee_id'), table_name='approval_step_approvers')
    op.drop_table('approval_step_approvers')

    op.drop_index(op.f('ix_approval_workflow_steps_workflow_id'), table_name='approval_workflow_steps')
    op.drop_table('approval_workflow_steps')

    op.drop_index(op.f('ix_approval_workflows_name'), table_name='approval_workflows')
    op.drop_index(op.f('ix_approval_workflows_code'), table_name='approval_workflows')
    op.drop_table('approval_workflows')
