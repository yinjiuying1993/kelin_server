"""Location sight privacy filters. Spec §8.4; no coordinates stored."""

from __future__ import annotations

import re
import unicodedata

SIGHT_SALIENCE = 70
SIGHT_CONFIDENCE = 1.0
SIGHT_SUMMARY_MAX = 500

_URL_RE = re.compile(
    r"(https?://|ftp://|www\.)|\b[a-z0-9-]+\.(com|net|org|cn|io|app)\b",
    re.IGNORECASE,
)
_COORD_RE = re.compile(
    r"(?i)(\b(lat|latitude|lng|lon|longitude)\b)|"
    r"([+-]?\d{1,3}\.\d+)\s*[,，]\s*([+-]?\d{1,3}\.\d+)|"
    r"\d{1,3}\s*[°º]\s*\d{1,2}",
)
_STREET_RE = re.compile(
    r"(?i)(\d+\s*号)|(([路街巷弄大道])\s*\d+)|"
    r"(\b(address|street|road|provider|place_id|amap|mapbox)\b)|门牌",
)


def location_sight_summary(*, label: str, city: str) -> str:
    return f"{label.strip()} · {city.strip()}"[:SIGHT_SUMMARY_MAX]


def location_privacy_reason(label: str, city: str) -> str | None:
    if _has_control_char(label) or _has_control_char(city):
        return "control_char"
    blob = f"{label} {city}"
    if _URL_RE.search(blob):
        return "url"
    if _COORD_RE.search(blob):
        return "coordinates"
    if _STREET_RE.search(blob):
        return "street_address"
    return None


def _has_control_char(value: str) -> bool:
    return any(unicodedata.category(char) in {"Cc", "Cf"} for char in value)
