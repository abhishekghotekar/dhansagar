import re

def normalize_phone(phone: str) -> str:
    """
    Extracts only digits and returns the canonical 10-digit number.
    Handles +91, leading 0s, dashes, and spaces.
    """
    if not phone:
        return ""
    digits = re.sub(r"\D", "", str(phone))
    if len(digits) >= 10:
        return digits[-10:]
    return digits

def format_currency(amount: float) -> str:
    """Formats amount into standard currency string."""
    try:
        return f"Rs. {float(amount):.2f}"
    except (ValueError, TypeError):
        return f"Rs. {amount}"
