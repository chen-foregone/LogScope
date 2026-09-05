"""持久化受控查询及来源覆盖，不连接真实日志源。"""
from alembic import op
import sqlalchemy as sa

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('query_source_gates',
        sa.Column('source_key', sa.String(64), primary_key=True),
        sa.Column('query_id', sa.Uuid()),
        sa.Column('blocked', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('retry_at', sa.DateTime(timezone=True)))
    op.create_table('controlled_queries',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('attempt_id', sa.Uuid(), sa.ForeignKey('attempts.id'), nullable=False),
        sa.Column('execution_id', sa.Uuid(), sa.ForeignKey('controlled_executions.id'), nullable=False),
        sa.Column('request_key', sa.Uuid(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('material', sa.JSON(), nullable=False),
        sa.UniqueConstraint('attempt_id', 'request_key', name='uq_query_request'))
    op.create_index('uq_active_query', 'controlled_queries', ['attempt_id'], unique=True,
                    postgresql_where=sa.text('active'))


def downgrade():
    op.drop_table('controlled_queries')
    op.drop_table('query_source_gates')
