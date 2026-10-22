"""Authentication and authorization schemas."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class UserRole(str, Enum):
    ADMIN = "admin"
    PROPERTY_MANAGER = "property_manager"
    ACCOUNTANT = "accountant"
    MAINTENANCE = "maintenance"
    RESIDENT = "resident"


class Permission(str, Enum):
    DOCUMENTS_ANALYZE = "documents:analyze"
    FINANCIAL_OPERATIONS = "financial:operations"
    APPROVALS_READ = "approvals:read"
    APPROVALS_DECIDE = "approvals:decide"
    AUDIT_READ = "audit:read"
    AUDIT_VERIFY = "audit:verify"
    PROPERTY_MANAGE = "property:manage"
    RESIDENT_READ = "resident:read"


ALL_PERMISSIONS: frozenset[Permission] = frozenset(Permission)

ROLE_PERMISSIONS: dict[UserRole, frozenset[Permission]] = {
    UserRole.ADMIN: ALL_PERMISSIONS,
    UserRole.PROPERTY_MANAGER: frozenset(
        {
            Permission.DOCUMENTS_ANALYZE,
            Permission.FINANCIAL_OPERATIONS,
            Permission.APPROVALS_READ,
            Permission.APPROVALS_DECIDE,
            Permission.AUDIT_READ,
            Permission.AUDIT_VERIFY,
            Permission.PROPERTY_MANAGE,
            Permission.RESIDENT_READ,
        }
    ),
    UserRole.ACCOUNTANT: frozenset(
        {
            Permission.DOCUMENTS_ANALYZE,
            Permission.FINANCIAL_OPERATIONS,
            Permission.APPROVALS_READ,
            Permission.APPROVALS_DECIDE,
            Permission.AUDIT_READ,
        }
    ),
    UserRole.MAINTENANCE: frozenset(
        {
            Permission.APPROVALS_READ,
        }
    ),
    # Resident users use self-scoped tenant portal endpoints. They must not
    # receive property-wide resident list access through this management API.
    UserRole.RESIDENT: frozenset(),
}


class User(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: str
    email: str
    is_active: bool = True
    is_superuser: bool = False
    google_sub: str | None = None
    google_email_verified: bool = False
    password_setup_required: bool = False
    created_at: datetime
    updated_at: datetime


class Property(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    property_id: str
    name: str
    created_at: datetime


class PropertyMembership(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    membership_id: str
    property_id: str
    user_id: str
    role: UserRole
    created_at: datetime


class LoginRequest(BaseModel):
    email: str = Field(min_length=3)
    password: str = Field(min_length=1)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or len(v) < 3:
            raise ValueError("Invalid email format")
        return v


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class UserMembershipInfo(BaseModel):
    property_id: str
    property_name: str | None = None
    role: str
    permissions: list[str]


class CurrentUserResponse(BaseModel):
    user_id: str
    email: str
    is_active: bool
    is_superuser: bool = False
    memberships: list[UserMembershipInfo] = Field(default_factory=list)
    portal_roles: list[str] = Field(default_factory=lambda: ["tenant"])
