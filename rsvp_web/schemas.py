"""Request/response schemas for the RSVP web service (pydantic v2 — FastAPI)."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class RsvpSubmission(BaseModel):
    decision: str = Field(default="yes", pattern="^(yes|no)$")
    attendee_reference: str = Field(default="", max_length=80)

    @field_validator("attendee_reference")
    @classmethod
    def clean_reference(cls, value: str) -> str:
        return value.strip()[:80]


class RsvpPageModel(BaseModel):
    event_id: str
    valid: bool
    message: str = ""


class CountResponse(BaseModel):
    event_id: str
    confirmed_count: int


class HealthResponse(BaseModel):
    status: str
    service: str = "workshoppilot-rsvp"
