"""GST helpers: GSTIN checksum validation and state codes.

A GSTIN is 15 characters: 2-digit state code + 10-char PAN + entity number
+ 'Z' + a check character computed with a mod-36 Luhn-style algorithm.
Validating the checksum catches typos and fabricated numbers without any API.
"""
import re

CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
GSTIN_RE = re.compile(r"\b(\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b")

STATE_CODES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana", "07": "Delhi",
    "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "19": "West Bengal",
    "24": "Gujarat", "27": "Maharashtra", "29": "Karnataka", "30": "Goa",
    "32": "Kerala", "33": "Tamil Nadu", "36": "Telangana", "37": "Andhra Pradesh",
}


def checksum_char(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14):
        value = CHARSET.index(ch)
        product = value * (1 if i % 2 == 0 else 2)
        total += product // 36 + product % 36
    return CHARSET[(36 - total % 36) % 36]


def make_gstin(state: str, pan: str, entity: str = "1") -> str:
    base = f"{state}{pan}{entity}Z"
    return base + checksum_char(base)


def validate_gstin(gstin: str | None) -> tuple[bool, str]:
    if not gstin:
        return False, "GSTIN missing"
    g = gstin.strip().upper()
    if len(g) != 15 or not re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]", g):
        return False, f"{g} is not in GSTIN format"
    if g[:2] not in STATE_CODES and not (1 <= int(g[:2]) <= 38):
        return False, f"{g[:2]} is not a valid state code"
    expected = checksum_char(g[:14])
    if g[14] != expected:
        return False, f"check digit is {g[14]}, expected {expected}"
    return True, "valid"


def state_of(gstin: str | None) -> str | None:
    if gstin and len(gstin) >= 2 and gstin[:2].isdigit():
        return gstin[:2]
    return None
