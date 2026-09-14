"""Device token hashing, encryption, and environment isolation. Spec §§7.4, 14.6."""

from __future__ import annotations

import hashlib
from typing import Literal

from cryptography.fernet import Fernet

DeviceEnvironment = Literal["sandbox", "production"]
DEVICE_ENVIRONMENTS = frozenset({"sandbox", "production"})
DEVICE_REGISTER_OPERATION = "devices.register"


def required_device_environment(app_env: str) -> DeviceEnvironment:
    return "production" if app_env == "prod" else "sandbox"


def environment_is_compatible(app_env: str, environment: str) -> bool:
    return environment == required_device_environment(app_env)


def hash_apns_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def encrypt_apns_token(token: str, fernet_key: bytes) -> str:
    return Fernet(fernet_key).encrypt(token.encode("utf-8")).decode("ascii")


def decrypt_apns_token(cipher: str, fernet_key: bytes) -> str:
    return Fernet(fernet_key).decrypt(cipher.encode("ascii")).decode("utf-8")
