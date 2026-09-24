"""追加严重程度决定历史，不虚构旧预留数据的评估。"""
from alembic import op
import sqlalchemy as sa

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('investigations', sa.Column('severity_version', sa.Integer(), nullable=False, server_default='0'))
    op.create_check_constraint('ck_severity_version', 'investigations', 'severity_version >= 0')
    op.create_table('severity_events',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('investigation_id', sa.Uuid(), sa.ForeignKey('investigations.id'), nullable=False),
        sa.Column('request_key', sa.Uuid(), nullable=False),
        sa.Column('request_body', sa.Text(), nullable=False),
        sa.Column('material', sa.JSON(), nullable=False),
        sa.Column('sequence', sa.Integer(), nullable=False),
        sa.CheckConstraint('sequence > 0', name='ck_severity_sequence'),
        sa.UniqueConstraint('investigation_id', 'request_key'),
        sa.UniqueConstraint('investigation_id', 'sequence'))


def downgrade():
    op.drop_table('severity_events')
    op.drop_constraint('ck_severity_version', 'investigations')
    op.drop_column('investigations', 'severity_version')
