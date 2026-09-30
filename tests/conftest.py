from __future__ import annotations

import os
import tempfile
from pathlib import Path

TEST_ROOT = Path(tempfile.mkdtemp(prefix="minioj-tests-"))
os.environ["MINIOJ_DATABASE_URL"] = f"sqlite:///{TEST_ROOT / 'test.db'}"
os.environ["MINIOJ_DATA_DIR"] = str(TEST_ROOT / "data")
os.environ["MINIOJ_JOB_DIR"] = str(TEST_ROOT / "jobs")
os.environ["MINIOJ_SECRET_KEY"] = "test-secret-key-that-is-long-and-stable"

import pytest
from fastapi.testclient import TestClient

from minioj.database import Base, engine
from minioj.server.main import app


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client
