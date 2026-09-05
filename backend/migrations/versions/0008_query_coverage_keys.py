"""为尚无脱敏密钥的旧调查补齐安全来源覆盖代号能力。"""
import secrets

from alembic import op
import sqlalchemy as sa

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    identities = connection.execute(sa.text(
        'SELECT DISTINCT attempts.investigation_id FROM attempts '
        'LEFT JOIN investigation_redaction_keys ON investigation_redaction_keys.investigation_id = attempts.investigation_id '
        'WHERE investigation_redaction_keys.investigation_id IS NULL')).scalars().all()
    for identity in identities:
        connection.execute(sa.text(
            'INSERT INTO investigation_redaction_keys (investigation_id, secret) VALUES (:id, :secret)'),
            {'id': identity, 'secret': secrets.token_bytes(32)})


def downgrade():
    # 密钥可能已被证据引用，不删除或改写既有脱敏身份。
    pass
