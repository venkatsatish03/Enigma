"""FastAPI dependencies for authentication and property-level authorization."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from fastapi import Depends, Header, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from orchestration.checkpoint_store import SqliteCheckpointStore
from repository.domain_repository import DomainRepository
from schemas.auth import (
    ALL_PERMISSIONS,
    ROLE_PERMISSIONS,
    Permission,
    Property,
    PropertyMembership,
    User,
    UserRole,
)
from tools.auth_config import AuthConfig, get_auth_config
from tools.auth_crypto import InvalidTokenError, TokenExpiredError, decode_access_token
from tools.auth_repository import AuthRepository

# HTTPBearer with auto_error=False to allow clean, custom 401 responses
bearer_security = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class PropertyContext:
    user: User
    property: Property
    membership: PropertyMembership
    role: UserRole
    permissions: frozenset[Permission]


def get_auth_repository(
    config: AuthConfig = Depends(get_auth_config),
) -> AuthRepository:
    """Dependency providing the AuthRepository."""
    return AuthRepository(config.auth_db_path)


def get_domain_repository(
    config: AuthConfig = Depends(get_auth_config),
) -> DomainRepository:
    """Dependency providing the DomainRepository."""
    return DomainRepository(config.domain_db_path)


def get_checkpoint_store(
    config: AuthConfig = Depends(get_auth_config),
) -> SqliteCheckpointStore:
    """Dependency providing the SqliteCheckpointStore."""
    return SqliteCheckpointStore(config.workflows_db_path)


def get_current_user(
    auth_header: HTTPAuthorizationCredentials | None = Depends(bearer_security),
    config: AuthConfig = Depends(get_auth_config),
    auth_repo: AuthRepository = Depends(get_auth_repository),
) -> User:
    """Authenticate the caller via JWT Bearer token and verify account status."""
    if not auth_header or not auth_header.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authentication credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = auth_header.credentials
    try:
        payload = decode_access_token(
            token,
            secret_key=config.secret_key,
            algorithms=[config.algorithm],
            issuer=config.issuer,
            audience=config.audience,
        )
    except TokenExpiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token has expired.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token payload is missing subject identity.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = auth_repo.get_user_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated user no longer exists.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled.",
        )

    return user


async def extract_property_id(
    request: Request,
    property_id_query: str | None = Query(None, alias="property_id"),
    property_id_header: str | None = Header(None, alias="X-Property-ID"),
) -> str:
    """Extract property context from path, query, header, or JSON body."""
    # 0. Path parameter (e.g. /api/v1/properties/{property_id}/...)
    if request.path_params and "property_id" in request.path_params:
        val = request.path_params["property_id"]
        if val and str(val).strip():
            return str(val).strip()

    # 1. Query parameter
    if property_id_query and property_id_query.strip():
        return property_id_query.strip()

    # 2. Header
    if property_id_header and property_id_header.strip():
        return property_id_header.strip()

    # 3. Request body (if JSON)
    try:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            body = await request.json()
            if isinstance(body, dict) and "property_id" in body and body["property_id"]:
                return str(body["property_id"]).strip()
    except Exception:
        pass

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Missing required property context. Supply 'property_id' via query parameter, "
        "'X-Property-ID' header, or request body.",
    )


def require_property_permission(required_permission: Permission) -> Callable:
    """Factory creating a dependency that enforces property existence and membership permissions."""

    async def _dependency(
        request: Request,
        current_user: User = Depends(get_current_user),
        property_id: str = Depends(extract_property_id),
        auth_repo: AuthRepository = Depends(get_auth_repository),
    ) -> PropertyContext:
        prop = auth_repo.get_property(property_id)
        if not prop:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Property '{property_id}' was not found.",
            )

        # Superusers hold global administrative access
        if current_user.is_superuser:
            membership = auth_repo.get_membership(property_id, current_user.user_id)
            if not membership:
                membership = PropertyMembership(
                    membership_id=f"super-{current_user.user_id}-{property_id}",
                    property_id=property_id,
                    user_id=current_user.user_id,
                    role=UserRole.ADMIN,
                    created_at=prop.created_at,
                )
            return PropertyContext(
                user=current_user,
                property=prop,
                membership=membership,
                role=UserRole.ADMIN,
                permissions=ALL_PERMISSIONS,
            )

        membership = auth_repo.get_membership(property_id, current_user.user_id)
        if not membership:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: You are not authorized for property '{property_id}'.",
            )

        permissions = ROLE_PERMISSIONS.get(membership.role, frozenset())
        if required_permission not in permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: Role '{membership.role.value}' lacks required permission "
                f"'{required_permission.value}' on property '{property_id}'.",
            )

        return PropertyContext(
            user=current_user,
            property=prop,
            membership=membership,
            role=membership.role,
            permissions=permissions,
        )

    return _dependency
