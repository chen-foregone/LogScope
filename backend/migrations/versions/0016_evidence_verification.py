"""Persist independent evidence checks and append-only conclusion verification changes."""
from alembic import op
import sqlalchemy as sa

revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('attempts', sa.Column('reverify_conclusion_id', sa.Uuid(),
                                      sa.ForeignKey('conclusions.id'), nullable=True))
    op.create_table('evidence_checks',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('evidence_id', sa.Uuid(), sa.ForeignKey('evidence.id'), nullable=False),
        sa.Column('request_key', sa.Uuid(), nullable=False),
        sa.Column('request_body', sa.Text(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('material', sa.JSON(), nullable=False),
        sa.CheckConstraint('version > 0', name='ck_evidence_check_version'),
        sa.UniqueConstraint('evidence_id', 'version'),
        sa.UniqueConstraint('evidence_id', 'request_key'))
    op.create_table('conclusion_checks',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('conclusion_id', sa.Uuid(), sa.ForeignKey('conclusions.id'), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('material', sa.JSON(), nullable=False),
        sa.CheckConstraint('version > 0', name='ck_conclusion_check_version'),
        sa.UniqueConstraint('conclusion_id', 'version'))


def downgrade():
    op.drop_table('conclusion_checks')
    op.drop_table('evidence_checks')
    op.drop_column('attempts', 'reverify_conclusion_id')
