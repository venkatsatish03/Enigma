from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

import main
from schemas.auth import UserRole
from tools.auth_config import AuthConfig
from tools.auth_crypto import create_access_token, hash_password
from tools.auth_dependencies import get_auth_config, get_auth_repository
from tools.auth_repository import AuthRepository

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def setup_auth_for_api_tests(tmp_path):
    auth_db = tmp_path / "api_test_auth.sqlite3"
    auth_repo = AuthRepository(auth_db)
    config = AuthConfig(
        secret_key="test-secret-at-least-32-chars-long-1234567890",
        algorithm="HS256",
        access_token_expire_minutes=60,
        auth_db_path=auth_db,
    )
    user = auth_repo.create_user(
        email="test.manager@example.com",
        password_hash=hash_password("TestPassword123!"),
    )
    auth_repo.create_property(name="Test Property", property_id="prop-test")
    auth_repo.create_membership(
        property_id="prop-test",
        user_id=user.user_id,
        role=UserRole.PROPERTY_MANAGER,
    )

    main.app.dependency_overrides[get_auth_config] = lambda: config
    main.app.dependency_overrides[get_auth_repository] = lambda: auth_repo

    token, _ = create_access_token(
        user_id=user.user_id,
        email=user.email,
        secret_key=config.secret_key,
    )
    client.headers["Authorization"] = f"Bearer {token}"
    client.headers["X-Property-ID"] = "prop-test"

    try:
        yield
    finally:
        main.app.dependency_overrides.clear()
        client.headers.pop("Authorization", None)
        client.headers.pop("X-Property-ID", None)


def test_health_endpoint():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "enigma-api",
    }


def test_openapi_schema_is_available():
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Enigma API"


def test_upload_valid_pdf_calls_document_agent(monkeypatch):
    fake_result = {
        "status": "completed",
        "source_file": "upload.pdf",
        "page_count": 1,
        "pages": [],
        "documents": [],
        "processing_methods": ["embedded_text"],
        "cross_document_relationships": [],
        "warnings": [],
    }

    fake_agent = Mock()
    fake_agent.process.return_value = fake_result
    monkeypatch.setattr(main, "document_agent", fake_agent)

    response = client.post(
        "/api/v1/documents/analyze",
        files={
            "file": (
                "invoice.pdf",
                b"%PDF-1.4\nfake test PDF content",
                "application/pdf",
            )
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    fake_agent.process.assert_called_once()

    passed_path = fake_agent.process.call_args.args[0]
    assert passed_path.suffix == ".pdf"
    assert not passed_path.exists()  # Temporary directory is cleaned up.


def test_rejects_non_pdf_filename():
    response = client.post(
        "/api/v1/documents/analyze",
        files={"file": ("invoice.txt", b"not a PDF", "text/plain")},
    )

    assert response.status_code == 415


def test_rejects_invalid_pdf_header():
    response = client.post(
        "/api/v1/documents/analyze",
        files={"file": ("invoice.pdf", b"not a PDF", "application/pdf")},
    )

    assert response.status_code == 400


def test_rejects_empty_file():
    response = client.post(
        "/api/v1/documents/analyze",
        files={"file": ("invoice.pdf", b"", "application/pdf")},
    )

    assert response.status_code == 400
