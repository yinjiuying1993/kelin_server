"""Server-generated spirit invite codes. Spec §§6.2, 8.9. Clients never supply them."""

from __future__ import annotations

import re
import secrets

# 32 chars: A-Z and 2-9, excluding 0/O/1/I.
INVITE_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
INVITE_CODE_LENGTH = 8
INVITE_CODE_PATTERN = r"^[A-HJ-NP-Z2-9]{8}$"
INVITE_CODE_UNIQUE_CONSTRAINT = "spirits_invite_code_key"
MAX_ALLOCATE_ATTEMPTS = 16

_INVITE_CODE_RE = re.compile(INVITE_CODE_PATTERN)


def generate_invite_code() -> str:
    return "".join(secrets.choice(INVITE_CODE_ALPHABET) for _ in range(INVITE_CODE_LENGTH))


def normalize_invite_code(value: str) -> str:
    return value.strip().upper()


def invite_code_is_valid(code: str) -> bool:
    return _INVITE_CODE_RE.fullmatch(code) is not None
