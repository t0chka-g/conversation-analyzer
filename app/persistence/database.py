from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def create_database_engine(database_url: str) -> Engine:
    """Configure an engine without opening a connection or creating directories."""
    from app.core.config import Settings

    url = Settings.sqlite_database(database_url)
    return create_engine(
        url,
        connect_args={"check_same_thread": False, "timeout": 10, "autocommit": False},
        hide_parameters=True,
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Use factory.begin() to commit on success, roll back and close on failure."""
    return sessionmaker(bind=engine, expire_on_commit=False)


def initialize_database(engine: Engine) -> None:
    """Explicit, idempotent schema creation, not a schema migration mechanism."""
    from app.persistence import models  # noqa: F401

    if engine.url.database not in {None, "", ":memory:"}:
        Path(engine.url.database).parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)
