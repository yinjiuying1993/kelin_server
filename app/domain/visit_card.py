"""Visit message card shape. Text, one voice clip, or one image."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_VISIT_CARD_TEXT = "它来看你了。"
CARD_TEXT_MAX = 200
CARD_VOICE_SECONDS_MAX = 30
CARD_OBJECT_KEY_MAX = 512
CARD_TITLE_MAX = 12
CARD_CAPTION_MAX = 40
CARD_MEDIA_MAX_BYTES = 5 * 1024 * 1024
VISIT_CARD_BUCKET = "kelin-visit-cards"
VISIT_CARD_KEY_PREFIX = "visit-cards/"
_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
_CARD_KEY_PREFIX = VISIT_CARD_KEY_PREFIX
_MEDIA_TYPES = (
    (".m4a", "audio/mp4"),
    (".mp3", "audio/mpeg"),
    (".jpg", "image/jpeg"),
    (".jpeg", "image/jpeg"),
    (".png", "image/png"),
    (".webp", "image/webp"),
)


@dataclass(frozen=True, slots=True)
class VisitCardContent:
    body: str | None
    voice_key: str | None
    voice_seconds: int | None
    voice_title: str | None
    image_key: str | None
    image_title: str | None
    image_caption: str | None
    reply_title: str | None = None

    @property
    def ready(self) -> bool:
        return self.body is not None or self.voice_key is not None or self.image_key is not None


def normalize_visit_card_content(
    *,
    body: str | None,
    voice_key: str | None,
    voice_seconds: int | None,
    voice_title: str | None,
    image_key: str | None,
    image_title: str | None,
    image_caption: str | None,
    reply_title: str | None = None,
    allow_empty: bool,
) -> VisitCardContent:
    text = _blank(body)
    voice = _blank(voice_key)
    image = _blank(image_key)
    voice_name = _blank(voice_title)
    image_name = _blank(image_title)
    caption = _blank(image_caption)
    reply_name = _blank(reply_title)
    if text is not None and len(text) > CARD_TEXT_MAX:
        raise ValueError("CARD_TEXT_REJECTED")
    if reply_name is not None and len(reply_name) > CARD_TITLE_MAX:
        raise ValueError("CARD_TEXT_REJECTED")
    if (voice is None) != (voice_seconds is None):
        raise ValueError("CARD_VOICE_REJECTED")
    if voice is None:
        voice_name = None
    elif (
        voice_seconds is None
        or voice_seconds < 1
        or voice_seconds > CARD_VOICE_SECONDS_MAX
        or len(voice) > CARD_OBJECT_KEY_MAX
        or not voice.startswith(_CARD_KEY_PREFIX)
        or (voice_name is not None and len(voice_name) > CARD_TITLE_MAX)
    ):
        raise ValueError("CARD_VOICE_REJECTED")
    if image is None:
        image_name = None
        caption = None
    elif (
        len(image) > CARD_OBJECT_KEY_MAX
        or not image.lower().endswith(_IMAGE_SUFFIXES)
        or not image.startswith(_CARD_KEY_PREFIX)
        or (image_name is not None and len(image_name) > CARD_TITLE_MAX)
        or (caption is not None and len(caption) > CARD_CAPTION_MAX)
    ):
        raise ValueError("CARD_IMAGE_REJECTED")
    seconds = None if voice is None else voice_seconds
    content = VisitCardContent(
        body=text,
        voice_key=voice,
        voice_seconds=seconds,
        voice_title=voice_name,
        image_key=image,
        image_title=image_name,
        image_caption=caption,
        reply_title=reply_name,
    )
    if not content.ready and not allow_empty:
        raise ValueError("CARD_EMPTY")
    return content


def visit_card_media_type(object_path: str) -> str | None:
    lowered = object_path.lower()
    for suffix, mime in _MEDIA_TYPES:
        if lowered.endswith(suffix):
            return mime
    return None


def visit_card_magic_ok(mime: str, body: bytes) -> bool:
    if mime == "image/jpeg":
        return body.startswith(b"\xff\xd8\xff")
    if mime == "image/png":
        return body.startswith(b"\x89PNG\r\n\x1a\n")
    if mime == "image/webp":
        return len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WEBP"
    if mime == "audio/mp4":
        return len(body) >= 8 and body[4:8] == b"ftyp"
    if mime == "audio/mpeg":
        if body.startswith(b"ID3"):
            return True
        return len(body) >= 2 and body[0] == 0xFF and (body[1] & 0xE0) == 0xE0
    return False


def _blank(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
