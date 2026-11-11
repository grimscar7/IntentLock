"""Catalog exploration and grounded options evaluation routes."""


from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.api.ai.service import get_ai_client, validate_and_ground_explanations
from backend.api.auth import get_current_user, require_demo_admin
from backend.api.catalog.service import (
    create_product_version,
    filter_products,
    get_current_products,
    reset_demo_catalog,
)
from backend.api.database import get_db
from backend.api.errors import AppError
from backend.api.intents.service import get_intent_by_id
from backend.api.models.enums import IntentStatus
from backend.api.schemas import (
    DemoResetResponse,
    IntentOptionsResponse,
    ProductOptionResponse,
    ProductUpdateRequest,
    ProductUpdateResponse,
    ProductVersionResponse,
)

router = APIRouter(prefix="/api", tags=["Catalog & Options"])


@router.get("/catalog/products", response_model=list[ProductVersionResponse])
def list_products(db: Session = Depends(get_db)):
    """List all current, active catalog product versions."""
    products = get_current_products(db)
    return [
        ProductVersionResponse(
            product_version_id=p.product_version_id,
            product_id=p.product_id,
            version=p.version,
            merchant_id=p.merchant_id,
            title=p.title,
            price_minor=p.price_minor,
            currency=p.currency,
            ram_gb=p.ram_gb,
            return_days=p.return_days,
            policy_version=p.policy_version,
            available=p.available,
            is_current=p.is_current,
        )
        for p in products
    ]


@router.get("/intents/{intent_id}/options", response_model=IntentOptionsResponse)
def get_intent_options(
    intent_id: str,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return verified eligible products with AI-grounded or deterministic template explanations."""
    intent = get_intent_by_id(user_id=user_id, intent_id=intent_id, db=db)
    if intent.status != IntentStatus.VALIDATED:
        raise AppError(
            code="INTENT_NOT_VALIDATED",
            message=f"Intent must be VALIDATED before requesting options (current: {intent.status.value})",
            status_code=400,
        )

    # 1. Strict code-based filtering
    eligible, _ = filter_products(intent.requirements, db)

    # 2. Grounded explanations
    ai_client = get_ai_client()
    raw_explanations = ai_client.explain_options(intent.requirements, eligible)
    grounded = validate_and_ground_explanations(raw_explanations, eligible, intent.requirements)

    options_list = []
    grounded_map = {g["product_id"]: g for g in grounded}

    for prod in eligible:
        g = grounded_map.get(prod["product_id"], {})
        options_list.append(
            ProductOptionResponse(
                product_id=prod["product_id"],
                product_version_id=prod["product_version_id"],
                title=prod["title"],
                price_minor=prod["price_minor"],
                currency=prod["currency"],
                ram_gb=prod["ram_gb"],
                return_days=prod["return_days"],
                explanation=g.get("explanation", ""),
                source_fields=g.get("source_fields", []),
                explanation_source=g.get("explanation_source", "template"),
            )
        )

    return IntentOptionsResponse(
        intent_id=intent_id,
        eligible_count=len(options_list),
        eligible_products=options_list,
    )


@router.patch("/demo/products/{product_id}", response_model=ProductUpdateResponse, tags=["Demo Admin"])
def update_product_demo(
    product_id: str,
    payload: ProductUpdateRequest,
    _admin: bool = Depends(require_demo_admin),
    db: Session = Depends(get_db),
):
    """Demo merchant endpoint: atomically create new product version and invalidate affected quotes/approvals."""
    changes = payload.model_dump(exclude_unset=True)
    res = create_product_version(db=db, product_id=product_id, changes=changes)
    return ProductUpdateResponse(**res)


@router.post("/demo/reset", response_model=DemoResetResponse, tags=["Demo Admin"])
def reset_demo(
    _admin: bool = Depends(require_demo_admin),
    db: Session = Depends(get_db),
):
    """Reset catalog products to baseline seeded version 1 state for reproducible demos."""
    reset_count = reset_demo_catalog(db)
    return DemoResetResponse(
        status="ok",
        message="Demo catalog restored to baseline seed v1 products.",
        reset_products=reset_count,
    )

