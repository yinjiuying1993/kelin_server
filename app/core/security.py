"""Strict Supabase JWT/JWKS verification. Spec §4.2. Never decode without verifying."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWK
from jwt.exceptions import InvalidTokenError

from app.core.logging import hash_user_id

ALLOWED_ALGS = frozenset({"RS256"})
JWKS_TTL = timedelta(hours=1)
_NBF_LEEWAY_SECONDS = 30
_BEARER = HTTPBearer(auto_error=False)


class JwtAuthError(Exception):
    """Token missing or invalid. Do not attach the JWT or JWKS body."""


class JwksUnavailable(Exception):
    """JWKS fetch failed. Callers must not fall back to unsigned/HS tokens."""


@dataclass(frozen=True, slots=True)
class CurrentUser:
    id: uuid.UUID
    auth_role: Literal["authenticated"] = "authenticated"
    session_id: str | None = None


class JwksFetcher(Protocol):
    async def fetch_jwks(self) -> Mapping[str, Any]: ...


class HttpxJwksFetcher:
    def __init__(self, url: str) -> None:
        self._url = url

    async def fetch_jwks(self) -> Mapping[str, Any]:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(self._url)
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            raise JwksUnavailable from exc
        if not isinstance(payload, dict):
            raise JwksUnavailable
        return payload


def _parse_rsa_keys(document: Mapping[str, Any]) -> dict[str, Any]:
    keys: dict[str, Any] = {}
    listed = document.get("keys")
    if not isinstance(listed, list):
        return keys
    for item in listed:
        if not isinstance(item, dict):
            continue
        kid = item.get("kid")
        kty = item.get("kty")
        alg = item.get("alg")
        use = item.get("use")
        if not isinstance(kid, str) or not kid:
            continue
        if kty != "RSA":
            continue
        if use is not None and use != "sig":
            continue
        if alg is not None and alg not in ALLOWED_ALGS:
            continue
        try:
            keys[kid] = PyJWK.from_dict(item).key
        except (InvalidTokenError, ValueError, TypeError):
            continue
    return keys


class JwtVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        fetcher: JwksFetcher,
        ttl: timedelta = JWKS_TTL,
        clock: Callable[[], datetime] | None = None,
        leeway: int = _NBF_LEEWAY_SECONDS,
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._fetcher = fetcher
        self._ttl = ttl
        self._clock = clock or (lambda: datetime.now(UTC))
        self._leeway = leeway
        self._keys: dict[str, Any] | None = None
        self._fetched_at: datetime | None = None

    def _fresh(self) -> bool:
        if self._fetched_at is None:
            return False
        return (self._clock() - self._fetched_at) < self._ttl

    async def _refresh(self) -> None:
        document = await self._fetcher.fetch_jwks()
        self._keys = _parse_rsa_keys(document)
        self._fetched_at = self._clock()

    async def _key_for_kid(self, kid: str) -> Any:
        cached = self._keys
        if cached is not None and kid in cached and self._fresh():
            return cached[kid]
        previous = cached
        try:
            await self._refresh()
        except JwksUnavailable:
            if previous is not None and kid in previous:
                return previous[kid]
            raise JwtAuthError from None
        if self._keys is None or kid not in self._keys:
            raise JwtAuthError
        return self._keys[kid]

    async def authenticate(self, token: str) -> CurrentUser:
        try:
            header = jwt.get_unverified_header(token)
        except InvalidTokenError as exc:
            raise JwtAuthError from exc
        alg = header.get("alg")
        kid = header.get("kid")
        if alg not in ALLOWED_ALGS or not isinstance(kid, str) or not kid:
            raise JwtAuthError
        key = await self._key_for_kid(kid)
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=list(ALLOWED_ALGS),
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway,
                options={
                    "require": ["exp", "iss", "aud", "sub"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_aud": True,
                    "verify_iss": True,
                },
            )
        except InvalidTokenError as exc:
            raise JwtAuthError from exc
        try:
            user_id = uuid.UUID(str(claims["sub"]))
        except (ValueError, TypeError, KeyError) as exc:
            raise JwtAuthError from exc
        if claims.get("role") != "authenticated":
            raise JwtAuthError
        session_raw = claims.get("session_id")
        session_id = session_raw if isinstance(session_raw, str) else None
        return CurrentUser(id=user_id, auth_role="authenticated", session_id=session_id)


async def require_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_BEARER),
) -> CurrentUser:
    verifier = getattr(request.app.state, "jwt_verifier", None)
    if (
        not isinstance(verifier, JwtVerifier)
        or credentials is None
        or credentials.scheme.lower() != "bearer"
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="UNAUTHENTICATED",
        )
    try:
        user = await verifier.authenticate(credentials.credentials)
    except JwtAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="UNAUTHENTICATED",
        ) from exc
    request.state.user_id_hash = hash_user_id(user.id)
    return user
