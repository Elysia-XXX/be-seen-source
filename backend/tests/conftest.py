import os
import tempfile
from pathlib import Path

# Isolated test database, never the user's reports.
test_dir = tempfile.TemporaryDirectory(prefix="beseen-tests-")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(test_dir.name) / "tests.db")
os.environ["DEMO_PASSWORD"] = "test-access"
os.environ["BASE_URL"] = "https://model.example.test/v1"
os.environ["API_KEY"] = "test-model-key"
os.environ["MODEL"] = "test-model"
os.environ["TENCENTCLOUD_SECRET_ID"] = "test-search-id"
os.environ["TENCENTCLOUD_SECRET_KEY"] = "test-search-key"

import pytest
from fastapi.testclient import TestClient
from app.db import Base, engine, init_db
from app.main import app


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(engine)
    init_db()
    yield


@pytest.fixture(scope="session", autouse=True)
def close_database():
    yield
    engine.dispose()
    test_dir.cleanup()


@pytest.fixture
def client():
    with TestClient(app) as c:
        response = c.post("/api/v1/auth/session", json={"password": "test-access"})
        c.headers["Authorization"] = "Bearer " + response.json()["token"]
        yield c
