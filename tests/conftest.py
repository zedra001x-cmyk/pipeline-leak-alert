import os
import re
import tempfile

# Throw-away database and quiet background simulator. Must be set BEFORE the app is imported.
_TMP_DIR = tempfile.mkdtemp(prefix="pipeline_test_")
os.environ.update(
    DATABASE_URL=f"sqlite:///{_TMP_DIR}/test.db",
    SIMULATION="1",
    SIM_INTERVAL="3600",
    PBKDF2_ITERATIONS="1000",
    ADMIN_USERNAME="admin",
    ADMIN_PASSWORD="test-admin-password",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.auth as auth  # noqa: E402
import app.models  # noqa: E402,F401
from app.database import Base, SessionLocal, engine, seed_sections  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402


def login(client, username="admin", password="test-admin-password"):
    client.get("/login")
    token = client.cookies.get("csrf_token")
    response = client.post(
        "/login", data={"username": username, "password": password, "csrf_token": token},
        follow_redirects=False,
    )
    client.headers["X-CSRF-Token"] = token
    return response


def add_user(username, role, password="user-password-123"):
    with SessionLocal() as db:
        db.add(User(username=username, password_hash=auth.hash_password(password), role=role))
        db.commit()
    return password


@pytest.fixture()
def client():
    """Fresh, seeded database; NOT logged in."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    auth._FAILS.clear()
    auth._DEVICE_REQUESTS.clear()
    with SessionLocal() as db:
        seed_sections(db)
        auth.ensure_admin(db)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def admin(client):
    assert login(client).status_code == 303
    return client


@pytest.fixture()
def db_session():
    with SessionLocal() as db:
        yield db
