"""实例接纳锁；保留所有旧运行事实，升级须停旧版本写入，禁止混跑。"""
from alembic import op
import sqlalchemy as sa

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('instance_admission',
                    sa.Column('id', sa.Integer(), primary_key=True),
                    sa.CheckConstraint('id = 1', name='instance_admission_singleton'))
    op.execute('INSERT INTO instance_admission (id) VALUES (1)')


def downgrade():
    op.drop_table('instance_admission')
