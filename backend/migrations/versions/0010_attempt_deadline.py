"""固定启动总期限；旧执行不补造退出证明。"""
from alembic import op
import sqlalchemy as sa

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('attempts', sa.Column('deadline_at', sa.DateTime(timezone=True)))
    op.add_column('attempts', sa.Column('timeout_requested_at', sa.DateTime(timezone=True)))
    op.execute("UPDATE attempts SET deadline_at = created_at + interval '10 minutes'")
    op.alter_column('attempts', 'deadline_at', nullable=False)


def downgrade():
    op.drop_column('attempts', 'timeout_requested_at')
    op.drop_column('attempts', 'deadline_at')
