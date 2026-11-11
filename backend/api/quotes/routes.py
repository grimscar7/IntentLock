"""API routes for binding quote generation, requote, and timeline lookup."""


from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.api.audit.service import get_quote_timeline
from backend.api.auth import get_current_user
from backend.api.database import get_db
from backend.api.quotes.service import create_quote, get_quote_by_id, requote
from backend.api.schemas import (
    QuoteCreateRequest,
    QuoteResponse,
    RequoteResponse,
    TimelineResponse,
)

router = APIRouter(prefix="/api/quotes", tags=["Quotes"])


@router.post("", response_model=QuoteResponse, status_code=201)
def generate_quote(
    payload: QuoteCreateRequest,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Generate an expiring merchant quote for a selected product."""
    quote = create_quote(
        user_id=user_id,
        intent_id=payload.intent_id,
        product_id=payload.product_id,
        quantity=payload.quantity,
        db=db,
    )
    return QuoteResponse(
        quote_id=quote.quote_id,
        user_id=quote.user_id,
        intent_id=quote.intent_id,
        product_version_id=quote.product_version_id,
        merchant_id=quote.merchant_id,
        quantity=quote.quantity,
        unit_price_minor=quote.unit_price_minor,
        shipping_minor=quote.shipping_minor,
        tax_minor=quote.tax_minor,
        total_minor=quote.total_minor,
        currency=quote.currency,
        status=quote.status.value,
        expires_at=quote.expires_at,
        is_expired=False,
    )


@router.get("/{quote_id}", response_model=QuoteResponse)
def get_quote(
    quote_id: str,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Retrieve quote details and computed expiration flag."""
    quote, is_expired = get_quote_by_id(user_id=user_id, quote_id=quote_id, db=db)
    return QuoteResponse(
        quote_id=quote.quote_id,
        user_id=quote.user_id,
        intent_id=quote.intent_id,
        product_version_id=quote.product_version_id,
        merchant_id=quote.merchant_id,
        quantity=quote.quantity,
        unit_price_minor=quote.unit_price_minor,
        shipping_minor=quote.shipping_minor,
        tax_minor=quote.tax_minor,
        total_minor=quote.total_minor,
        currency=quote.currency,
        status=quote.status.value,
        expires_at=quote.expires_at,
        is_expired=is_expired,
    )


@router.post("/{quote_id}/requote", response_model=RequoteResponse, status_code=201)
def requote_endpoint(
    quote_id: str,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Re-issue a fresh quote following catalog changes, verifying eligibility."""
    new_quote, comparison = requote(db=db, user_id=user_id, quote_id=quote_id)
    quote_resp = QuoteResponse(
        quote_id=new_quote.quote_id,
        user_id=new_quote.user_id,
        intent_id=new_quote.intent_id,
        product_version_id=new_quote.product_version_id,
        merchant_id=new_quote.merchant_id,
        quantity=new_quote.quantity,
        unit_price_minor=new_quote.unit_price_minor,
        shipping_minor=new_quote.shipping_minor,
        tax_minor=new_quote.tax_minor,
        total_minor=new_quote.total_minor,
        currency=new_quote.currency,
        status=new_quote.status.value,
        expires_at=new_quote.expires_at,
        is_expired=False,
    )
    return RequoteResponse(new_quote=quote_resp, comparison=comparison)


@router.get("/{quote_id}/events", response_model=TimelineResponse)
def get_timeline_endpoint(
    quote_id: str,
    limit: int = Query(default=100, ge=1, le=200),
    cursor: str | None = Query(default=None),
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Fetch complete chronological lifecycle audit timeline for a quote."""
    return get_quote_timeline(
        db=db,
        user_id=user_id,
        quote_id=quote_id,
        limit=limit,
        cursor=cursor,
    )
