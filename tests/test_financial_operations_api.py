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
def setup_auth_for_financial_api_tests(tmp_path):
    auth_db = tmp_path / "fin_test_auth.sqlite3"
    auth_repo = AuthRepository(auth_db)
    config = AuthConfig(
        secret_key="test-secret-at-least-32-chars-long-1234567890",
        algorithm="HS256",
        access_token_expire_minutes=60,
        auth_db_path=auth_db,
    )
    user = auth_repo.create_user(
        email="accountant@example.com",
        password_hash=hash_password("AccountantPassword123!"),
    )
    auth_repo.create_property(name="Finance Property", property_id="prop-fin")
    auth_repo.create_membership(
        property_id="prop-fin",
        user_id=user.user_id,
        role=UserRole.ACCOUNTANT,
    )

    main.app.dependency_overrides[get_auth_config] = lambda: config
    main.app.dependency_overrides[get_auth_repository] = lambda: auth_repo

    token, _ = create_access_token(
        user_id=user.user_id,
        email=user.email,
        secret_key=config.secret_key,
    )
    client.headers["Authorization"] = f"Bearer {token}"
    client.headers["X-Property-ID"] = "prop-fin"

    try:
        yield
    finally:
        main.app.dependency_overrides.clear()
        client.headers.pop("Authorization", None)
        client.headers.pop("X-Property-ID", None)


def test_financial_operations_endpoint_calls_orchestrator(monkeypatch):
    fake_result = {
        "operation_id": "test-operation-001",
        "records": [],
        "matches": [],
        "findings": [],
        "balance_assessments": [],
        "proposed_actions": [],
        "payment_allocations": [],
        "unallocated_payment_amounts": {},
        "unresolved_allocation_issues": [],
    }

    orchestrator = Mock(return_value=fake_result)
    monkeypatch.setattr(main, "run_financial_operations", orchestrator)

    response = client.post(
        "/api/v1/financial-operations",
        json={"document_result": {"status": "completed", "documents": []}},
    )

    assert response.status_code == 200
    orchestrator.assert_called_once()


def test_financial_operations_rejects_missing_document_result():
    response = client.post(
        "/api/v1/financial-operations",
        json={},
    )

    assert response.status_code == 422


def test_financial_operations_rejects_invalid_request_body():
    response = client.post(
        "/api/v1/financial-operations",
        json={"document_result": "not-an-object"},
    )

    assert response.status_code == 422


def test_financial_operations_maps_input_errors_to_422(monkeypatch):
    orchestrator = Mock(side_effect=ValueError("invalid financial data"))
    monkeypatch.setattr(main, "run_financial_operations", orchestrator)

    response = client.post(
        "/api/v1/financial-operations",
        json={"document_result": {"status": "completed", "documents": []}},
    )

    assert response.status_code == 422
    assert "could not process" in response.json()["detail"]
