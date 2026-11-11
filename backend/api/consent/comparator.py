"""Pure functional comparison engine for validating consent snapshots against current reality."""

from typing import Any

from backend.api.models.approval_snapshot import ApprovalSnapshot
from backend.api.models.enums import ReasonCode
from backend.api.models.product_version import ProductVersion
from backend.api.models.quote import Quote
from backend.api.schemas.validation import Change


def compare(
    approval: ApprovalSnapshot,
    quote: Quote,
    approved_pv: ProductVersion,
    current_pv: ProductVersion,
    requirements: dict[str, Any],
    shipping_minor: int = 0,
    tax_minor: int = 0,
) -> tuple[list[Change], ReasonCode]:
    """Pure comparison function returning all detected discrepancies and prioritized reason code."""
    changes: list[Change] = []
    detected_codes: list[ReasonCode] = []

    # 1. Product ID check
    if approval.product_id != current_pv.product_id:
        changes.append(
            Change(
                field="product_id",
                approved=approval.product_id,
                current=current_pv.product_id,
            )
        )
        detected_codes.append(ReasonCode.ITEM_MISMATCH)

    # 2. Merchant ID check
    if approval.merchant_id != current_pv.merchant_id:
        changes.append(
            Change(
                field="merchant_id",
                approved=approval.merchant_id,
                current=current_pv.merchant_id,
            )
        )
        detected_codes.append(ReasonCode.MERCHANT_MISMATCH)

    # 3. Currency check
    if approval.currency != current_pv.currency:
        changes.append(
            Change(
                field="currency",
                approved=approval.currency,
                current=current_pv.currency,
            )
        )
        detected_codes.append(ReasonCode.CURRENCY_MISMATCH)

    # 4. Quantity check
    if approval.quantity != quote.quantity:
        changes.append(
            Change(
                field="quantity",
                approved=approval.quantity,
                current=quote.quantity,
            )
        )
        detected_codes.append(ReasonCode.QUANTITY_MISMATCH)

    # 5. Availability check
    if not current_pv.available:
        changes.append(
            Change(
                field="available",
                approved=True,
                current=False,
            )
        )
        detected_codes.append(ReasonCode.PRODUCT_VERSION_CHANGED)

    # 6. Pricing check (recompute server-side integer total)
    current_unit_price = current_pv.price_minor
    approved_terms = approval.approved_terms or {}
    approved_unit_price = approved_terms.get("price_minor", approved_terms.get("unit_price_minor", approved_pv.price_minor))
    if current_unit_price != approved_unit_price:
        changes.append(
            Change(
                field="price_minor",
                approved=approved_unit_price,
                current=current_unit_price,
            )
        )
        detected_codes.append(ReasonCode.QUOTE_CHANGED)

    current_calculated_total = (current_unit_price * approval.quantity) + shipping_minor + tax_minor
    if current_calculated_total != approval.total_minor:
        changes.append(
            Change(
                field="total_minor",
                approved=approval.total_minor,
                current=current_calculated_total,
            )
        )
        detected_codes.append(ReasonCode.QUOTE_CHANGED)

    # 7. Policy version check
    if approval.policy_version != current_pv.policy_version:
        changes.append(
            Change(
                field="policy_version",
                approved=approval.policy_version,
                current=current_pv.policy_version,
            )
        )
        detected_codes.append(ReasonCode.POLICY_CHANGED)

    # 8. Return days check
    approved_returns = approved_terms.get("return_days", approved_pv.return_days)
    if current_pv.return_days != approved_returns:
        changes.append(
            Change(
                field="return_days",
                approved=approved_returns,
                current=current_pv.return_days,
            )
        )
        detected_codes.append(ReasonCode.POLICY_CHANGED)

    # 9. RAM check
    approved_ram = approved_terms.get("ram_gb", approved_pv.ram_gb)
    if current_pv.ram_gb != approved_ram:
        changes.append(
            Change(
                field="ram_gb",
                approved=approved_ram,
                current=current_pv.ram_gb,
            )
        )
        detected_codes.append(ReasonCode.QUOTE_CHANGED)

    # 10. Re-check current product against stored intent requirements
    meets_requirements = True
    req_budget = requirements.get("max_total_minor")
    if req_budget is not None and (current_pv.price_minor is None or current_pv.price_minor > req_budget):
        meets_requirements = False

    req_ram = requirements.get("minimum_ram_gb", 0)
    if current_pv.ram_gb is None or current_pv.ram_gb < req_ram:
        meets_requirements = False

    req_returns = requirements.get("minimum_return_days", 0)
    # Missing or null never silently satisfies requirement
    if current_pv.return_days is None or current_pv.return_days < req_returns:
        meets_requirements = False

    req_currency = requirements.get("currency")
    if req_currency is not None and current_pv.currency != req_currency:
        meets_requirements = False

    if not meets_requirements:
        detected_codes.append(ReasonCode.REQUIREMENTS_NO_LONGER_MET)

    # 11. Product version ID check: only considered a change if terms changed or explicitly tracking version
    if approval.product_version_id != current_pv.product_version_id:
        if changes:
            changes.append(
                Change(
                    field="product_version_id",
                    approved=approval.product_version_id,
                    current=current_pv.product_version_id,
                )
            )

    # Priority order resolution
    priority_order = [
        ReasonCode.ITEM_MISMATCH,
        ReasonCode.MERCHANT_MISMATCH,
        ReasonCode.CURRENCY_MISMATCH,
        ReasonCode.QUANTITY_MISMATCH,
        ReasonCode.AMOUNT_MISMATCH,
        ReasonCode.QUOTE_CHANGED,
        ReasonCode.POLICY_CHANGED,
        ReasonCode.PRODUCT_VERSION_CHANGED,
        ReasonCode.REQUIREMENTS_NO_LONGER_MET,
    ]

    for p_code in priority_order:
        if p_code in detected_codes:
            return changes, p_code

    if detected_codes:
        return changes, detected_codes[0]

    return changes, ReasonCode.OK
