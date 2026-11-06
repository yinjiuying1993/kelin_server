"""Homepage label text. Does not choose which label is active."""

from __future__ import annotations

DISPLAY_LABELS: dict[str, str] = {
    "uneasy": "不安",
    "hurt": "委屈",
    "hungry_and_tired": "又饿又困",
    "hungry": "饿了",
    "tired": "困了",
    "irritated": "烦躁",
    "low": "低落",
    "missing": "惦记",
    "happy": "开心",
    "curious": "好奇",
    "close": "亲近",
    "satisfied": "满足",
    "slightly_hungry": "有点饿",
    "slightly_tired": "有点困",
    "calm": "平静",
}


def display_label(label: str | None) -> str | None:
    if not label:
        return None
    return DISPLAY_LABELS.get(label, label)
