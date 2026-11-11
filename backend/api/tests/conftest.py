"""Pytest fixtures for IntentLock test suite."""

import os
import sys
from datetime import UTC, datetime, timedelta

# Ensure repository root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.ai.service import FakeAIClient
from backend.api.config import get_settings
from backend.api.database import get_db
from backend.api.main import app
from backend.api.models.base import Base
from backend.scripts.seed import seed_products

# Safety guard: Refuse to run tests if test database equals target database
target_db = os.getenv("DATABASE_URL")
test_db = os.getenv("TEST_DATABASE_URL", "sqlite:///:memory:")

if target_db and test_db and target_db == test_db and not target_db.startswith("sqlite"):
    raise RuntimeError(
        f"CRITICAL SAFETY VIOLATION: TEST_DATABASE_URL ({test_db}) matches DATABASE_URL! "
        "Test suite refused execution to prevent wiping target database."
    )

test_engine = create_engine(
    test_db,
    connect_args={"check_same_thread": False} if "sqlite" in test_db else {},
    poolclass=StaticPool if "sqlite" in test_db else None,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    """Ensure schema is initialized once for the test session if needed."""
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture(scope="function")
def db_session():
    """Create a fresh in-memory or rolled-back database schema for each test."""
    Base.metadata.create_all(bind=test_engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture(scope="function")
def client(db_session):
    """FastAPI test client with database dependency override."""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def seed_catalog(db_session):
    """Seed catalog with standard 5 laptops."""
    seed_products(db_session)
    db_session.commit()


@pytest.fixture
def user_a_headers():
    return {"X-User-Id": "user_a"}


@pytest.fixture
def user_b_headers():
    return {"X-User-Id": "user_b"}


@pytest.fixture
def admin_headers():
    return {"X-Demo-Admin-Token": get_settings().DEMO_ADMIN_TOKEN}


@pytest.fixture
def fake_ai():
    return FakeAIClient()


class ClockController:
    """Helper to simulate clock skew/offset for time-based testing."""
    def __init__(self):
        self._offset = timedelta(0)

    def advance(self, **kwargs):
        self._offset += timedelta(**kwargs)

    def now(self):
        return datetime.now(UTC) + self._offset


@pytest.fixture
def controllable_clock(monkeypatch):
    clock = ClockController()
    return clock
