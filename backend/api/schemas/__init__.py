"""Pydantic v2 schemas for all requests and responses."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from backend.api.schemas.demo import (
    DemoResetResponse,
    IntentRequirementsUpdateRequest,
    ProductUpdateRequest,
    ProductUpdateResponse,
    ProductVersionChange,
    RequoteComparison,
    RequoteResponse,
    TimelineItem,
    TimelineResponse,
)
from backend.api.schemas.validation import Change, ValidationResult


# ---------------------------------------------------------
# Intent Schemas
# ---------------------------------------------------------
class IntentCreateRequest(BaseModel):
    """Payload for submitting buyer shopping intent."""
    raw_text: str = Field(..., min_length=3, description="Natural language shopping request")


class ExtractedRequirements(BaseModel):
    """Structured search constraints extracted from buyer intent."""
    category: str = Field(..., description="Target product category")
    max_total_minor: int = Field(..., ge=0, description="Maximum budget in minor units")
    currency: str = Field(default="USD", description="3-letter currency code")
    minimum_ram_gb: int = Field(default=0, ge=0, description="Minimum RAM capacity in GB")
    minimum_return_days: int = Field(default=0, ge=0, description="Minimum return policy days")


class IntentResponse(BaseModel):
    """Response payload for intent operations."""
    intent_id: str
    user_id: str
    status: str
    raw_text: str
    requirements: dict[str, Any]
    clarification_questions: list[str] | None = None
    previous_intent_id: str | None = None
    created_at: datetime


# ---------------------------------------------------------
# Catalog & Explanation Schemas
# ---------------------------------------------------------
class ProductVersionResponse(BaseModel):
    """Catalog product version summary."""
    product_version_id: str
    product_id: str
    version: int
    merchant_id: str
    title: str
    price_minor: int
    currency: str
    ram_gb: int
    return_days: int
    policy_version: int
    available: bool
    is_current: bool


class ProductOptionResponse(BaseModel):
    """Eligible product item with grounded explanation."""
    product_id: str
    product_version_id: str
    title: str
    price_minor: int
    currency: str
    ram_gb: int
    return_days: int
    explanation: str
    source_fields: list[str]
    explanation_source: str  # "ai" or "template"


class IntentOptionsResponse(BaseModel):
    """Options result for a validated intent."""
    intent_id: str
    eligible_count: int
    eligible_products: list[ProductOptionResponse]


# ---------------------------------------------------------
# Quote Schemas
# ---------------------------------------------------------
class QuoteCreateRequest(BaseModel):
    """Request payload to create a binding merchant quote."""
    intent_id: str
    product_id: str
    quantity: int = Field(default=1, gt=0, description="Quantity to purchase")


class QuoteResponse(BaseModel):
    """Response payload for quote details."""
    quote_id: str
    user_id: str
    intent_id: str
    product_version_id: str
    merchant_id: str
    quantity: int
    unit_price_minor: int
    shipping_minor: int
    tax_minor: int
    total_minor: int
    currency: str
    status: str
    expires_at: datetime
    is_expired: bool
    fee_note: str = "no shipping or tax applied in this demo"


# ---------------------------------------------------------
# Approval Schemas
# ---------------------------------------------------------
class ApprovalCreateRequest(BaseModel):
    """Request payload to consent and approve a quote."""
    quote_id: str


class ApprovalResponse(BaseModel):
    """Response payload for approval snapshot."""
    approval_id: str
    user_id: str
    quote_id: str
    merchant_id: str
    product_id: str
    product_version_id: str
    quantity: int
    total_minor: int
    currency: str
    policy_version: int
    approved_terms: dict[str, Any]
    approved_at: datetime
    expires_at: datetime
    status: str
