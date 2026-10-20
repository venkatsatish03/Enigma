"""Exceptions for ENIGMA Master Orchestrator and durable workflows."""

from __future__ import annotations


class WorkflowError(Exception):
    """Base exception for all workflow orchestration errors."""


class WorkflowNotFoundError(WorkflowError):
    """Raised when a requested workflow does not exist."""


class WorkflowStateError(WorkflowError):
    """Raised when an invalid state transition or operation is attempted."""


class WorkflowAuthorizationError(WorkflowError):
    """Raised when an operation violates property or role authorization."""


class StepExecutionError(WorkflowError):
    """Raised when a workflow step handler encounters an unrecoverable failure."""


class TransientStepError(WorkflowError):
    """Raised when a step fails due to a temporary condition eligible for retry."""


class ConcurrentExecutionError(WorkflowError):
    """Raised when another worker is currently executing or claiming the step."""


class PolicyValidationError(WorkflowError):
    """Raised when action policy check blocks the workflow."""
