"""Phone/email normalization helpers."""
import re

_PHONE_RE = re.compile(r"\d+")


def normalize_phone(raw: str) -> str:
    """Normalize a Russian phone to 11-digit 7XXXXXXXXXX or 10-digit digits string.

    Unknown formats are returned as bare digits (may be empty).
    """
    if not raw:
        return ""
    digits = "".join(_PHONE_RE.findall(raw))
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 10 and digits[0] in "345689":
        # Russian area/mobile code without country prefix
        digits = "7" + digits
    return digits


def is_mobile(digits: str) -> bool:
    """Russian mobile: 7 + 9XX... (or generic 9XX for 10-digit)."""
    if len(digits) == 11 and digits.startswith("7") and digits[1] == "9":
        return True
    if len(digits) == 10 and digits.startswith("9"):
        return True
    return False


def format_phone_intl(digits: str) -> str:
    if len(digits) == 11 and digits.startswith("7"):
        return f"+{digits[0]} ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:]}"
    return raw_or_empty(digits)


def raw_or_empty(digits: str) -> str:
    return f"+{digits}" if digits else ""


def mobile_phone(digits_list) -> str:
    for d in digits_list:
        if is_mobile(d):
            return d
    return ""


def telegram_link(mobile_digits: str) -> str:
    if is_mobile(mobile_digits):
        d = mobile_digits if len(mobile_digits) == 11 else "7" + mobile_digits
        return f"https://t.me/+{d}"
    return ""


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def normalize_email(raw: str) -> str:
    if not raw:
        return ""
    m = _EMAIL_RE.search(raw)
    return m.group(0).lower() if m else ""


def normalize_url(raw: str) -> str:
    if not raw:
        return ""
    raw = raw.strip()
    if not re.match(r"^https?://", raw):
        raw = "https://" + raw
    return raw.rstrip("/")
