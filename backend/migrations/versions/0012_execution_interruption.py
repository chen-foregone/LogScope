"""保守保存失联及执行进程证明，不回填旧执行身份。"""
from alembic import op
import sqlalchemy as sa

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('attempts', sa.Column('lost_requested_at', sa.DateTime(timezone=True)))
    op.add_column('attempts', sa.Column('interruption_confirmation', sa.JSON()))
    op.add_column('controlled_executions', sa.Column('process_identity', sa.JSON()))
    op.add_column('controlled_queries', sa.Column('process_identity', sa.JSON()))


def downgrade():
    op.drop_column('controlled_queries', 'process_identity')
    op.drop_column('controlled_executions', 'process_identity')
    op.drop_column('attempts', 'interruption_confirmation')
    op.drop_column('attempts', 'lost_requested_at')
