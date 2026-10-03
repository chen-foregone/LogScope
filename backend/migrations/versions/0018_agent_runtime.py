"""Bounded real-agent execution and shared budget accounting."""
from alembic import op
import sqlalchemy as sa

revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('agent_budget', sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('spent', sa.BigInteger, nullable=False), sa.Column('reserved', sa.BigInteger, nullable=False),
        sa.CheckConstraint('id = 1 AND spent >= 0 AND reserved >= 0'))
    op.create_table('agent_runs', sa.Column('id', sa.Uuid, primary_key=True),
        sa.Column('attempt_id', sa.Uuid, sa.ForeignKey('attempts.id'), unique=True, nullable=False),
        sa.Column('execution_id', sa.Uuid, sa.ForeignKey('controlled_executions.id'), unique=True, nullable=False),
        sa.Column('model', sa.String(80)), sa.Column('phase', sa.String(40)), sa.Column('reason', sa.String(64)),
        sa.Column('active', sa.Boolean), sa.Column('calls', sa.Integer),
        sa.Column('spent_microyuan', sa.BigInteger), sa.Column('reserved_microyuan', sa.BigInteger),
        sa.Column('created_at', sa.DateTime(timezone=True)), sa.Column('updated_at', sa.DateTime(timezone=True)),
        sa.CheckConstraint('calls >= 0 AND spent_microyuan >= 0 AND reserved_microyuan >= 0'))
    op.create_table('agent_calls', sa.Column('id', sa.Uuid, primary_key=True),
        sa.Column('run_id', sa.Uuid, sa.ForeignKey('agent_runs.id'), nullable=False),
        sa.Column('number', sa.Integer), sa.Column('status', sa.String(32)),
        sa.Column('reserved', sa.BigInteger), sa.Column('charged', sa.BigInteger),
        sa.Column('input_tokens', sa.Integer), sa.Column('output_tokens', sa.Integer),
        sa.Column('price_version', sa.String(100)), sa.Column('created_at', sa.DateTime(timezone=True)),
        sa.UniqueConstraint('run_id', 'number'), sa.CheckConstraint('reserved >= 0 AND (charged IS NULL OR charged >= 0)'))
    op.create_table('agent_checkpoints', sa.Column('id', sa.Uuid, primary_key=True),
        sa.Column('run_id', sa.Uuid, sa.ForeignKey('agent_runs.id'), nullable=False),
        sa.Column('step', sa.Integer), sa.Column('material', sa.JSON),
        sa.Column('created_at', sa.DateTime(timezone=True)), sa.UniqueConstraint('run_id', 'step'))


def downgrade():
    for name in ('agent_checkpoints', 'agent_calls', 'agent_runs', 'agent_budget'):
        op.drop_table(name)
