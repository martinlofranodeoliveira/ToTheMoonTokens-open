import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

_DB_PATH = Path(tempfile.gettempdir()) / f"tothemoon_api_test_{os.getpid()}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH.as_posix()}"
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-with-at-least-32-characters")
os.environ.setdefault("ALLOW_IMPORT_TIME_INIT_DB", "true")
os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_dev_secret"


def pytest_sessionstart(session: pytest.Session) -> None:
    _DB_PATH.unlink(missing_ok=True)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    from tothemoon_api.database import engine

    engine.dispose()
    _DB_PATH.unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def reset_rate_limiter() -> None:
    from tothemoon_api.observability import rate_limiter

    rate_limiter.reset()


@pytest.fixture
def db_session():
    from tothemoon_api.database import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = testing_session_local()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
