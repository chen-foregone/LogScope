"""保存取消请求、写入隔离及受控查询退出确认。"""
from alembic import op
import sqlalchemy as sa

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('controlled_executions', sa.Column('stop_protocol', sa.String(40)))
    op.add_column('attempts', sa.Column('cancel_requested_at', sa.DateTime(timezone=True)))
    op.add_column('attempts', sa.Column('stop_confirmed_at', sa.DateTime(timezone=True)))
    op.add_column('attempts', sa.Column('stop_confirmation', sa.String(64)))


def downgrade():
    op.drop_column('controlled_executions', 'stop_protocol')
    op.drop_column('attempts', 'stop_confirmation')
    op.drop_column('attempts', 'stop_confirmed_at')
    op.drop_column('attempts', 'cancel_requested_at')
