"""Deterministic, non-AI human readable change explanations for consent validation."""


from backend.api.models.enums import ReasonCode
from backend.api.schemas.validation import Change


def format_minor_units(minor: int, currency: str = "USD") -> str:
    """Format minor currency integer into a display string without floating point conversions."""
    dollars = minor // 100
    cents = minor % 100
    if currency == "USD":
        return f"${dollars}.{cents:02d}"
    return f"{dollars}.{cents:02d} {currency}"


def build_message(changes: list[Change], reason_code: ReasonCode) -> str:
    """Generate clear, deterministic explanation of validation outcome from verified changes."""
    if reason_code == ReasonCode.OK:
        return "Consent verified: all terms match approved snapshot."

    if reason_code == ReasonCode.APPROVAL_NOT_FOUND:
        return "Approval snapshot not found or access denied."

    if reason_code == ReasonCode.APPROVAL_EXPIRED:
        return "Approval has expired. Fresh approval is required."

    if reason_code == ReasonCode.APPROVAL_SUPERSEDED:
        return "Approval was superseded by a newer approval. Fresh approval is required."

    if reason_code == ReasonCode.APPROVAL_INVALIDATED:
        return "Approval was invalidated due to catalog or merchant alterations. Fresh approval is required."

    if reason_code == ReasonCode.APPROVAL_ALREADY_CONSUMED:
        return "Approval has already been consumed by an order. Cannot be reused."

    if reason_code == ReasonCode.PAYMENT_ATTEMPT_NOT_FOUND:
        return "Payment attempt not found or access denied."

    if reason_code == ReasonCode.PAYMENT_ATTEMPT_UNBOUND:
        return "Payment attempt does not have a bound approval."

    if reason_code == ReasonCode.VALIDATION_ERROR:
        return "An unexpected validation error occurred. Payment creation was blocked for safety."

    # Build detailed field-specific messaging from changes
    messages: list[str] = []
    change_map = {c.field: c for c in changes}

    if "total_minor" in change_map:
        c = change_map["total_minor"]
        messages.append(
            f"The approved price was {format_minor_units(int(c.approved))}. "
            f"The current price is {format_minor_units(int(c.current))}."
        )
    elif "price_minor" in change_map:
        c = change_map["price_minor"]
        messages.append(
            f"The approved unit price was {format_minor_units(int(c.approved))}. "
            f"The current unit price is {format_minor_units(int(c.current))}."
        )

    if "product_id" in change_map:
        c = change_map["product_id"]
        messages.append(
            f"Item mismatch: approved item was '{c.approved}', but current item is '{c.current}'."
        )

    if "merchant_id" in change_map:
        c = change_map["merchant_id"]
        messages.append(
            f"Merchant mismatch: approved merchant was '{c.approved}', but current merchant is '{c.current}'."
        )

    if "currency" in change_map:
        c = change_map["currency"]
        messages.append(
            f"Currency mismatch: approved currency was '{c.approved}', but current currency is '{c.current}'."
        )

    if "quantity" in change_map:
        c = change_map["quantity"]
        messages.append(
            f"Quantity mismatch: approved quantity was {c.approved}, but current quantity is {c.current}."
        )

    if "return_days" in change_map:
        c = change_map["return_days"]
        messages.append(
            f"The approved return policy was {c.approved} days. "
            f"The current policy is {c.current} days."
        )

    if "policy_version" in change_map:
        c = change_map["policy_version"]
        messages.append(
            f"Merchant policy version changed from v{c.approved} to v{c.current}."
        )

    if "available" in change_map:
        messages.append("Product is no longer available in inventory.")

    if reason_code == ReasonCode.REQUIREMENTS_NO_LONGER_MET:
        messages.append("Current product specifications no longer meet your search criteria.")

    if not messages:
        if reason_code == ReasonCode.ITEM_MISMATCH:
            messages.append("Item mismatch between approved terms and current product.")
        elif reason_code == ReasonCode.MERCHANT_MISMATCH:
            messages.append("Merchant mismatch between approved terms and current product.")
        elif reason_code == ReasonCode.CURRENCY_MISMATCH:
            messages.append("Currency mismatch between approved terms and current product.")
        elif reason_code == ReasonCode.AMOUNT_MISMATCH or reason_code == ReasonCode.QUOTE_CHANGED:
            messages.append("Price or total amount mismatch.")
        elif reason_code == ReasonCode.POLICY_CHANGED:
            messages.append("Merchant policy has changed.")
        else:
            messages.append(f"Validation failed with code: {reason_code.value}.")

    messages.append("Fresh approval is required.")
    return " ".join(messages)
