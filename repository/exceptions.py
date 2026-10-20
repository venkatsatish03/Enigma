"""Exceptions for the shared domain repository layer."""

from __future__ import annotations


class DomainRepositoryError(Exception):
    """Base exception for domain repository errors."""


class EntityNotFoundError(DomainRepositoryError):
    """Raised when an entity is not found in the authorized property scope."""


class CrossPropertyReferenceError(DomainRepositoryError):
    """Raised when an entity references a foreign entity from a different property."""


class IntegrityConflictError(DomainRepositoryError):
    """Raised when a unique constraint or database integrity violation occurs."""


class InvalidOperationError(DomainRepositoryError):
    """Raised when an operation violates domain business or relationship rules."""
