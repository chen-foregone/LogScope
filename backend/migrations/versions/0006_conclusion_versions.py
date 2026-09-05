"""追加结论版本与正常结束事实；保留既有证据。"""
from alembic import op
import sqlalchemy as sa

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('attempts', sa.Column('ended_at', sa.DateTime(timezone=True)))
    op.add_column('attempts', sa.Column('result', sa.String(24)))
    op.add_column('attempts', sa.Column('end_reason', sa.String(32)))
    op.create_table('conclusions',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('attempt_id', sa.Uuid(), sa.ForeignKey('attempts.id'), nullable=False),
        sa.Column('execution_id', sa.Uuid(), sa.ForeignKey('controlled_executions.id'), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('request_key', sa.Uuid(), nullable=False),
        sa.Column('request_body', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('material', sa.JSON(), nullable=False),
        sa.UniqueConstraint('attempt_id', 'version', name='uq_conclusion_version'),
        sa.UniqueConstraint('attempt_id', 'request_key', name='uq_conclusion_request'))


def downgrade():
    op.drop_table('conclusions')
    for column in ('end_reason', 'result', 'ended_at'):
        op.drop_column('attempts', column)
