"""Validation schemas for consent verification before order creation and capture."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from backend.api.models.enums import ReasonCode


class Change(BaseModel):
    """Structured discrepancy between approved terms and current product/order reality."""

    field: str = Field(
        ...,
        description="Frozen field name: product_id, product_version_id, merchant_id, currency, total_minor, price_minor, return_days, ram_gb, quantity, policy_version, available",
    )
    approved: Any = Field(..., description="The term agreed to in the ApprovalSnapshot")
    current: Any = Field(..., description="The current reality from catalog or order")


class ValidationResult(BaseModel):
    """Complete pre-flight consent validation evaluation."""

    allowed: bool = Field(..., description="Whether payment creation or capture may proceed")
    reason_code: ReasonCode | None = Field(
        default=None, description="Deterministic reason code for validation decision"
    )
    changes: list[Change] = Field(
        default_factory=list, description="All discrepancies identified between approved and current terms"
    )
    requires_new_approval: bool = Field(
        ..., description="Whether buyer must explicitly re-consent via a new quote and approval"
    )
    message: str = Field(..., description="Deterministic, human-readable summary of the validation outcome")
    approval_id: str | None = Field(default=None, description="The bound approval ID evaluated")
    quote_id: str | None = Field(default=None, description="The bound quote ID evaluated")
    checked_at: datetime = Field(..., description="UTC timestamp of when the check was performed")
