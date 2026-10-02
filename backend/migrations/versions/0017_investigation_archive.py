"""Replace implicit retention with explicit archive lifecycle."""
from alembic import op
import sqlalchemy as sa

revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('investigations', sa.Column('archived_at', sa.DateTime(timezone=True)))
    op.add_column('investigations', sa.Column('archive_version', sa.Integer(), nullable=False, server_default='0'))
    op.create_check_constraint('ck_archive_version', 'investigations', 'archive_version >= 0')
    op.execute('UPDATE investigations SET retention_expires_at = NULL')
    for table in ('evidence', 'conclusions', 'controlled_queries', 'severity_events', 'evidence_checks', 'conclusion_checks'):
        op.execute(f'CREATE INDEX ix_{table}_cleanup_refs ON {table} USING gin ((material::jsonb) jsonb_ops)')
    op.create_index('ix_investigations_archive_due', 'investigations', ['retention_expires_at', 'id'],
                    postgresql_where=sa.text('archived_at IS NOT NULL'))


def downgrade():
    op.drop_index('ix_investigations_archive_due', 'investigations')
    for table in ('evidence', 'conclusions', 'controlled_queries', 'severity_events', 'evidence_checks', 'conclusion_checks'):
        op.drop_index(f'ix_{table}_cleanup_refs', table)
    op.drop_constraint('ck_archive_version', 'investigations')
    op.drop_column('investigations', 'archive_version')
    op.drop_column('investigations', 'archived_at')
    op.execute("""UPDATE investigations i SET retention_expires_at = CASE
        WHEN EXISTS (SELECT 1 FROM attempts a WHERE a.investigation_id = i.id AND a.status = '调查中') THEN NULL
        ELSE COALESCE((SELECT ended_at FROM attempts a WHERE a.investigation_id = i.id
                      ORDER BY attempt_number DESC LIMIT 1), i.created_at) + INTERVAL '30 days' END""")
