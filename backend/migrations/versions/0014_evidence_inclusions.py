"""分离证据原归属和逐轮纳入；旧记录逐条保留，不猜测身份。"""
from alembic import op
import sqlalchemy as sa

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('attempt_evidence',
        sa.Column('attempt_id', sa.Uuid(), sa.ForeignKey('attempts.id'), primary_key=True),
        sa.Column('evidence_id', sa.Uuid(), sa.ForeignKey('evidence.id'), primary_key=True),
        sa.Column('included_at', sa.DateTime(timezone=True), nullable=False))
    op.execute('INSERT INTO attempt_evidence (attempt_id, evidence_id, included_at) SELECT attempt_id, id, created_at FROM evidence')


def downgrade():
    op.drop_table('attempt_evidence')
