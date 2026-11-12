import sys
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.persistence.database import (  # noqa: E402
    create_database_engine,
    initialize_database,
)


def main() -> int:
    engine = create_database_engine(get_settings().database_url)
    try:
        initialize_database(engine)
    except (OSError, SQLAlchemyError):
        print("Database initialization failed: check directory access", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    print("Database initialized; existing data preserved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
