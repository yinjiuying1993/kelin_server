from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.core.config import Settings
from app.domain.builtin_bank import match_builtin_bank
from app.domain.chat import UNKNOWN_SOURCE_REPLY
from app.domain.retrieval import plan_retrieval
from app.main import create_app
from app.providers.search_safety import MAX_WEB_SOURCES, parse_search_results, trusted_web_refs
from app.providers.types import SearchResult
from app.schemas.jsonb import SourceRef

NOW = datetime(2026, 9, 10, 4, 0, tzinfo=UTC)


def test_no_public_search_api() -> None:
    paths = create_app(Settings(app_env="test")).openapi().get("paths", {})
    assert not any("search" in path for path in paths)
    routers = create_app(Settings(app_env="test")).router.routes
    assert not any(getattr(route, "path", "").endswith("/search") for route in routers)


def test_trusted_https_dedupes_domain_and_caps_at_three() -> None:
    results = [
        SearchResult(url="http://insecure.example/a"),
        SearchResult(url="https://one.example/a"),
        SearchResult(url="https://one.example/b"),
        SearchResult(url="https://user:pass@two.example/c"),
        SearchResult(url="https://localhost/x"),
        SearchResult(url="https://two.example/d"),
        SearchResult(url="https://three.example/e"),
        SearchResult(url="https://four.example/f"),
        SearchResult(url="not-a-url"),
    ]
    refs = trusted_web_refs(results, fetched_at=NOW)
    assert len(refs) == MAX_WEB_SOURCES
    assert [ref.url for ref in refs] == [
        "https://one.example/a",
        "https://two.example/d",
        "https://three.example/e",
    ]
    assert all(ref.type == "web" and ref.id is None for ref in refs)
    assert all(ref.fetched_at == NOW for ref in refs)


def test_invalid_search_payload_is_empty_not_forged() -> None:
    assert parse_search_results("https://example.com") == []
    assert parse_search_results([{"url": "https://example.com", "extra": True}]) == []
    parsed = parse_search_results([{"url": "https://example.com"}])
    assert parsed[0].url == "https://example.com"


def test_retrieval_order_local_before_search_and_unknown_when_disabled() -> None:
    memory = (SourceRef(type="memory", id=uuid4()),)
    assert (
        plan_retrieval(
            memory_refs=memory,
            pact_id=uuid4(),
            builtin_refs=match_builtin_bank("STAR 方法"),
            search_query="需要联网的问题",
            remote_search_on=True,
            onboarding=False,
        ).kind
        == "local"
    )
    pact_id = uuid4()
    pact_plan = plan_retrieval(
        memory_refs=(),
        pact_id=pact_id,
        builtin_refs=match_builtin_bank("STAR 方法"),
        search_query="本周契约",
        remote_search_on=True,
        onboarding=False,
    )
    assert pact_plan.kind == "local"
    assert pact_plan.source_refs[0].type == "pact"
    builtin_plan = plan_retrieval(
        memory_refs=(),
        pact_id=None,
        builtin_refs=match_builtin_bank("STAR 方法是什么"),
        search_query="STAR 方法是什么",
        remote_search_on=True,
        onboarding=False,
    )
    assert builtin_plan.kind == "local"
    assert builtin_plan.source_refs[0].type == "builtin_bank"
    assert (
        plan_retrieval(
            memory_refs=(),
            pact_id=None,
            builtin_refs=(),
            search_query="今天外面发生了什么",
            remote_search_on=False,
            onboarding=False,
        ).kind
        == "unknown"
    )
    assert (
        plan_retrieval(
            memory_refs=(),
            pact_id=None,
            builtin_refs=(),
            search_query="今天外面发生了什么",
            remote_search_on=True,
            onboarding=False,
        ).kind
        == "search"
    )
    assert (
        plan_retrieval(
            memory_refs=(),
            pact_id=None,
            builtin_refs=(),
            search_query="今天外面发生了什么",
            remote_search_on=True,
            onboarding=True,
        ).kind
        == "none"
    )
    assert UNKNOWN_SOURCE_REPLY == "这件事我还不知道"
