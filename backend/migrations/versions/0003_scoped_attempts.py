"""固定范围轮次与事务内可控执行接纳。"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("investigations", "retention_expires_at", nullable=True)
    op.create_table(
        "attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("investigation_id", sa.Uuid(), sa.ForeignKey("investigations.id"), nullable=False),
        sa.Column("request_key", sa.Uuid(), nullable=False),
        sa.Column("request_body", sa.Text(), nullable=False),
        sa.Column("service_id", sa.String(80), nullable=False),
        sa.Column("source_ids", sa.JSON(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.CheckConstraint("ends_at > starts_at AND ends_at <= starts_at + interval '24 hours'", name="ck_attempt_range"),
        sa.UniqueConstraint("investigation_id", "request_key", name="uq_attempt_request"),
    )
    op.create_index("uq_running_attempt", "attempts", ["investigation_id"], unique=True,
                    postgresql_where=sa.text("status = '调查中'"))
    op.create_table(
        "controlled_executions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("attempt_id", sa.Uuid(), sa.ForeignKey("attempts.id"), nullable=False, unique=True),
        sa.Column("valid", sa.Boolean(), nullable=False),
    )


def downgrade():
    op.drop_table("controlled_executions")
    op.drop_index("uq_running_attempt", table_name="attempts")
    op.drop_table("attempts")
    op.execute("UPDATE investigations SET retention_expires_at = created_at + interval '30 days' WHERE retention_expires_at IS NULL")
    op.alter_column("investigations", "retention_expires_at", nullable=False)
