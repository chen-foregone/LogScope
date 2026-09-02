from alembic import context
from sqlalchemy import create_engine
from app.core.config import settings

engine = create_engine(settings.database_url)
with engine.connect() as connection:
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
