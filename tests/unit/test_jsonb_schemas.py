from uuid import uuid4

import pytest
from app.schemas.jsonb import parse_source_refs
from pydantic import ValidationError


def test_source_refs_only_allow_verified_citation_summaries() -> None:
    parse_source_refs([])
    parse_source_refs([{"type": "memory", "id": str(uuid4())}])
    with pytest.raises(ValidationError):
        parse_source_refs({"type": "memory"})
    with pytest.raises(ValidationError):
        parse_source_refs([{"type": "memory", "id": str(uuid4()), "content": "秘密"}])
    with pytest.raises(ValidationError):
        parse_source_refs([{"type": "search", "url": "https://example.com"}])
    with pytest.raises(ValidationError):
        parse_source_refs([{"type": "web", "url": "http://example.com"}])
    web = parse_source_refs(
        [
            {
                "type": "web",
                "url": "https://example.com/a",
                "fetched_at": "2026-09-10T04:00:00Z",
            }
        ]
    )
    assert web[0].url == "https://example.com/a"
    with pytest.raises(ValidationError):
        parse_source_refs([{"type": "memory", "id": str(uuid4()), "url": "https://example.com"}])
