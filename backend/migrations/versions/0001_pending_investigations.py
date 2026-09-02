"""创建无运行轮的待处理调查。"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "investigations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.String(4000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("investigations")
