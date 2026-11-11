"""API routes for buyer consent approval snapshots and pre-flight validation."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.api.auth import get_current_user
from backend.api.consent.service import create_approval, validate_before_create
from backend.api.database import get_db
from backend.api.schemas import (
    ApprovalCreateRequest,
    ApprovalResponse,
    ValidationResult,
)

router = APIRouter(prefix="/api/approvals", tags=["Approvals"])


@router.post("", response_model=ApprovalResponse, status_code=201)
def approve_quote(
    payload: ApprovalCreateRequest,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Explicitly consent to and freeze terms of an active quote into an ApprovalSnapshot."""
    approval = create_approval(
        user_id=user_id,
        quote_id=payload.quote_id,
        db=db,
    )
    return ApprovalResponse(
        approval_id=approval.approval_id,
        user_id=approval.user_id,
        quote_id=approval.quote_id,
        merchant_id=approval.merchant_id,
        product_id=approval.product_id,
        product_version_id=approval.product_version_id,
        quantity=approval.quantity,
        total_minor=approval.total_minor,
        currency=approval.currency,
        policy_version=approval.policy_version,
        approved_terms=approval.approved_terms,
        approved_at=approval.approved_at,
        expires_at=approval.expires_at,
        status=approval.status.value,
    )


@router.post("/{approval_id}/validate", response_model=ValidationResult)
def validate_approval_preflight(
    approval_id: str,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Pre-flight dry-run validation for frontend UI before initiating checkout (record_audit=False)."""
    return validate_before_create(
        db=db,
        user_id=user_id,
        approval_id=approval_id,
        record_audit=False,
    )
