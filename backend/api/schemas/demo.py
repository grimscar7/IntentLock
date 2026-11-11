"""Schemas for demo merchant modifications, timeline events, and reapproval workflows."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.api.schemas.validation import Change


class ProductUpdateRequest(BaseModel):
    """Payload for updating product terms via demo merchant PATCH endpoint."""

    price_minor: int | None = Field(default=None, description="New price in minor integer units")
    return_days: int | None = Field(default=None, description="New return policy in days")
    available: bool | None = Field(default=None, description="Catalog availability flag")

    model_config = ConfigDict(extra="forbid")

    @field_validator("price_minor")
    @classmethod
    def validate_price(cls, v: int | None) -> int | None:
        if v is not None:
            if not isinstance(v, int) or isinstance(v, bool):
                raise ValueError("price_minor must be an integer")
            if v < 0:
                raise ValueError("price_minor must be non-negative")
        return v

    @field_validator("return_days")
    @classmethod
    def validate_returns(cls, v: int | None) -> int | None:
        if v is not None:
            if not isinstance(v, int) or isinstance(v, bool):
                raise ValueError("return_days must be an integer")
            if v < 0:
                raise ValueError("return_days must be non-negative")
        return v


class ProductVersionChange(BaseModel):
    """Structured detail of a single changed field on product versioning."""

    field: str
    old: Any
    new: Any


class ProductUpdateResponse(BaseModel):
    """Response returned upon catalog item versioning."""

    product_id: str
    old_version: int
    new_version: int
    changes: list[ProductVersionChange]
    material: bool
    invalidated_quotes: int
    invalidated_approvals: int


class DemoResetResponse(BaseModel):
    """Response returned upon catalog reset to initial seeded state."""

    status: str
    message: str
    reset_products: int


class RequoteComparison(BaseModel):
    """Structured comparison between previous quote and re-quoted terms."""

    old_total_minor: int
    new_total_minor: int
    changes: list[Change]
    base_message: str | None = None
    message: str
    explanation_source: str = "template"


class RequoteResponse(BaseModel):
    """Response returned upon creating a new quote from an existing quote."""

    new_quote: Any  # QuoteResponse
    comparison: RequoteComparison


class IntentRequirementsUpdateRequest(BaseModel):
    """Buyer action requesting updated search requirements (e.g. increased budget)."""

    max_total_minor: int | None = Field(default=None, ge=0)
    minimum_ram_gb: int | None = Field(default=None, ge=0)
    minimum_return_days: int | None = Field(default=None, ge=0)
    currency: str | None = None
    category: str | None = None
    raw_text: str | None = None


class TimelineItem(BaseModel):
    """Single item in the unified quote audit and lifecycle timeline."""

    event_id: str
    event_type: str
    created_at: datetime
    approval_id: str | None = None
    payment_attempt_id: str | None = None
    title: str
    description: str
    payload: dict[str, Any]


class TimelineResponse(BaseModel):
    """Paginated collection of lifecycle events for a quote."""

    quote_id: str
    total_events: int
    events: list[TimelineItem]
    next_cursor: str | None = None
