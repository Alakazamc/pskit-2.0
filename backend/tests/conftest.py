import os
import tempfile
from pathlib import Path

test_root = Path(tempfile.mkdtemp(prefix="pskit2-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(test_root / 'test.sqlite3').as_posix()}"
os.environ["DATA_DIR"] = str(test_root)
os.environ["ARTIFACT_DIR"] = str(test_root / "artifacts")
os.environ["REGISTRATION_MODE"] = "open"
os.environ["READINESS_REQUIRE_QDRANT"] = "false"

import pytest

from app.db.base import Base
from app.db.session import engine, init_db


@pytest.fixture(autouse=True)
def clean_database():
    init_db()
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    yield
