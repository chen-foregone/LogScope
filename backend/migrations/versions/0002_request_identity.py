"""持久化创建请求身份。"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("investigations", sa.Column("request_key", sa.Uuid(), nullable=True))
    op.create_unique_constraint("uq_investigations_request_key", "investigations", ["request_key"])


def downgrade():
    op.drop_constraint("uq_investigations_request_key", "investigations", type_="unique")
    op.drop_column("investigations", "request_key")
