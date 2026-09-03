"""本轮可靠身份唯一；历史无身份材料保留并安全标记。"""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('evidence', sa.Column('identity_key', sa.String(64), nullable=True))
    op.add_column('evidence', sa.Column('content_key', sa.String(64), nullable=True))
    op.add_column('evidence', sa.Column('identity_status', sa.String(24), nullable=False, server_default='legacy_unknown'))
    op.create_unique_constraint('uq_evidence_attempt_identity', 'evidence', ['attempt_id', 'identity_key'])


def downgrade():
    op.drop_constraint('uq_evidence_attempt_identity', 'evidence', type_='unique')
    op.drop_column('evidence', 'identity_status')
    op.drop_column('evidence', 'content_key')
    op.drop_column('evidence', 'identity_key')
