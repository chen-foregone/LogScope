"""持久轮次顺序与严重程度评估元数据。"""
from alembic import op
import sqlalchemy as sa

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('attempts', sa.Column('attempt_number', sa.Integer(), nullable=True))
    op.execute('''WITH ordered AS (
        SELECT id, row_number() OVER (PARTITION BY investigation_id ORDER BY created_at, id) AS number
        FROM attempts
    ) UPDATE attempts SET attempt_number = ordered.number FROM ordered WHERE attempts.id = ordered.id''')
    op.alter_column('attempts', 'attempt_number', nullable=False)
    op.create_unique_constraint('uq_attempt_number', 'attempts', ['investigation_id', 'attempt_number'])
    op.create_check_constraint('ck_attempt_number', 'attempts', 'attempt_number > 0')
    op.add_column('investigations', sa.Column('severity', sa.String(20), nullable=False, server_default='未评估'))
    op.add_column('investigations', sa.Column('severity_source', sa.String(20), nullable=True))
    op.add_column('investigations', sa.Column('severity_assessed_at', sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint('ck_severity_assessment', 'investigations',
        "(severity = '未评估' AND severity_source IS NULL AND severity_assessed_at IS NULL) OR "
        "(severity IN ('高', '中', '低') AND severity_source IN ('human', 'agent') AND severity_source IS NOT NULL AND severity_assessed_at IS NOT NULL)")


def downgrade():
    op.drop_constraint('ck_severity_assessment', 'investigations')
    for column in ('severity_assessed_at', 'severity_source', 'severity'):
        op.drop_column('investigations', column)
    op.drop_constraint('ck_attempt_number', 'attempts')
    op.drop_constraint('uq_attempt_number', 'attempts')
    op.drop_column('attempts', 'attempt_number')
