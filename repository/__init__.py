"""Repository package for domain entities with property-scoped data access."""

from repository.domain_repository import DomainRepository
from repository.exceptions import (
    CrossPropertyReferenceError,
    DomainRepositoryError,
    EntityNotFoundError,
    IntegrityConflictError,
    InvalidOperationError,
)

__all__ = [
    "DomainRepository",
    "DomainRepositoryError",
    "EntityNotFoundError",
    "CrossPropertyReferenceError",
    "IntegrityConflictError",
    "InvalidOperationError",
]
