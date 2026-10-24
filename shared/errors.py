"""Error taxonomy shared by every layer.

Errors carry a stable machine-readable ``code`` so agents can report honest,
structured failures instead of stringly-typed exceptions.
"""

from __future__ import annotations


class EventPilotError(Exception):
    code = "internal_error"
    http_status = 500

    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "details": self.details}


class ValidationError(EventPilotError):
    code = "validation_error"
    http_status = 400


class MissingFieldError(ValidationError):
    code = "missing_field"


class NotFoundError(EventPilotError):
    code = "not_found"
    http_status = 404


class StateTransitionError(EventPilotError):
    code = "illegal_state_transition"
    http_status = 409


class DuplicateOperationError(EventPilotError):
    code = "duplicate_operation"
    http_status = 409


class ExpiredError(EventPilotError):
    code = "expired"
    http_status = 410


class AuthorizationError(EventPilotError):
    code = "not_authorized"
    http_status = 403


class BudgetExceededError(EventPilotError):
    code = "budget_exceeded"
    http_status = 422


class CapacityExceededError(EventPilotError):
    code = "capacity_exceeded"
    http_status = 422


class EquipmentMismatchError(EventPilotError):
    code = "equipment_mismatch"
    http_status = 422


class IntegrationError(EventPilotError):
    code = "integration_error"
    http_status = 502


class IntegrationUnavailableError(IntegrationError):
    code = "integration_unavailable"


class RateLimitError(EventPilotError):
    code = "rate_limited"
    http_status = 429


class ConfigurationError(EventPilotError):
    code = "configuration_error"
    http_status = 500
