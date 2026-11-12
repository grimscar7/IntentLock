"""Database connection, session management, and health checks."""

from collections.abc import Generator

from backend.api.config import get_settings
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

settings = get_settings()

db_url = settings.DATABASE_URL
# Automatically normalize postgres/postgresql url for psycopg v3 if needed
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+psycopg://", 1)
elif db_url.startswith("postgresql://") and not db_url.startswith("postgresql+"):
    db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)

connect_args = {}
if db_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(
    db_url,
    connect_args=connect_args,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)



def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a managed database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_db_health(db: Session | None = None) -> bool:
    """Execute a simple query to verify database connectivity."""
    close_when_done = False
    if db is None:
        db = SessionLocal()
        close_when_done = True
    try:
        db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
    finally:
        if close_when_done:
            db.close()
