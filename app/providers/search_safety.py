"""Trusted HTTPS web sources for Chat Search. Spec §16.3."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from urllib.parse import urlparse

from pydantic import TypeAdapter, ValidationError

from app.providers.types import SearchResult
from app.schemas.jsonb import SourceRef

MAX_WEB_SOURCES = 3
_BLOCKED_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})
_RESULTS_ADAPTER: TypeAdapter[list[SearchResult]] = TypeAdapter(list[SearchResult])


def parse_search_results(value: object) -> list[SearchResult]:
    try:
        return _RESULTS_ADAPTER.validate_python(value)
    except ValidationError:
        return []


def trusted_web_refs(
    results: Sequence[SearchResult],
    *,
    fetched_at: datetime,
) -> tuple[SourceRef, ...]:
    seen_hosts: set[str] = set()
    trusted: list[SourceRef] = []
    for result in results:
        host = _trusted_https_host(result.url)
        if host is None or host in seen_hosts:
            continue
        seen_hosts.add(host)
        trusted.append(SourceRef(type="web", id=None, url=result.url, fetched_at=fetched_at))
        if len(trusted) >= MAX_WEB_SOURCES:
            break
    return tuple(trusted)


def _trusted_https_host(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return None
    if parsed.username or parsed.password:
        return None
    host = (parsed.hostname or "").casefold()
    if not host or host in _BLOCKED_HOSTS:
        return None
    if "." not in host:
        return None
    return host
