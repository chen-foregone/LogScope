"""仅保存脱敏证据与调查独立代号密钥。"""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('attempts', sa.Column('evidence_unavailable', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        'investigation_redaction_keys',
        sa.Column('investigation_id', sa.Uuid(), sa.ForeignKey('investigations.id'), primary_key=True),
        sa.Column('secret', sa.LargeBinary(), nullable=False),
    )
    op.create_table(
        'evidence',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('attempt_id', sa.Uuid(), sa.ForeignKey('attempts.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('material', sa.JSON(), nullable=False),
    )
    op.create_index('ix_evidence_attempt', 'evidence', ['attempt_id'])


def downgrade():
    op.drop_table('evidence')
    op.drop_table('investigation_redaction_keys')
    op.drop_column('attempts', 'evidence_unavailable')
