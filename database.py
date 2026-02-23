from sqlmodel import Session, SQLModel, create_engine

from config import settings

engine = create_engine(
    settings.DATABASE_URL,
    echo=(settings.APP_ENV == "development"),
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)


def create_db_and_tables() -> None:
    """Dev convenience — creates all tables without Alembic. Prefer alembic upgrade head in prod."""
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
