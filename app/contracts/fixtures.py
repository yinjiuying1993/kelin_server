"""Fixture harness for Envelope success / business_error / missing field. Spec §20.2.

Does not invent unimplemented business endpoints. Health routes are not Envelope.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from app.contracts.breaking import HTTP_METHODS
from app.contracts.openapi import SOURCE_APP, OpenApiExport
from app.core.errors import KNOWN_ERROR_CODES
from app.schemas.account import DeleteAccountRequest, DeletionResult
from app.schemas.bootstrap import BootstrapSnapshot
from app.schemas.chat import ChatRequest, ChatTurnResult
from app.schemas.devices import DeviceRegistration, RegisterDeviceRequest
from app.schemas.envelope import Envelope
from app.schemas.extract import ExtractRequest, ExtractResult
from app.schemas.feed import (
    FeedCreateRequest,
    FeedMutationResult,
    PromiseActionRequest,
    PromisePatchRequest,
)
from app.schemas.memory import (
    MemoryClearRequest,
    MemoryDeleteRequest,
    MemoryListQuery,
    MemoryMutationResult,
    MemoryPage,
    MemoryPatchRequest,
    MemoryPublic,
    MemoryTombstone,
)
from app.schemas.messages import MessagePage, MessagePublic
from app.schemas.moderate import ModerateSightRequest, ModerateSightResult
from app.schemas.onboarding import CompleteOnboardingRequest, OnboardingCompleteResult
from app.schemas.pact import (
    CreatePactRequest,
    CreatePactResult,
    PactAnswerRequest,
    PactAnswerResult,
    PactSessionRequest,
    PactSessionResult,
    PactSkipRequest,
    PactSkipResult,
)
from app.schemas.recall import RecallMutationResult, RecallRequest
from app.schemas.report import ReportLineRequest, ReportLineResult, ReportSnapshot
from app.schemas.settings import PatchSpiritRequest, SpiritPatchResult
from app.schemas.social_api import (
    AddFriendRequest,
    AddFriendResult,
    FriendPage,
    PostcardPage,
    PostcardReadRequest,
    PostcardReadResult,
    RemoveFriendRequest,
    RemoveFriendResult,
)
from app.schemas.speech import SynthesizeRequest, SynthesizeResult, TranscribeForm, TranscribeResult
from app.schemas.spirit import CreateSpiritRequest, MutationResult
from app.schemas.storage import SightUploadUrlRequest, SightUploadUrlResult

BASE_SCENARIOS = ("success", "business_error", "missing_required_field")
LIST_PAGE_SCENARIOS = (
    "page_empty",
    "page_first",
    "page_middle",
    "page_last",
    "invalid_or_expired_cursor",
)
ENVELOPE_ENTRY_ID = "envelope"
SPIRITS_ENTRY_ID = "POST_api_v1_spirits"
BOOTSTRAP_ENTRY_ID = "GET_api_v1_bootstrap"
CHAT_ENTRY_ID = "POST_api_v1_chat"
EXTRACT_ENTRY_ID = "POST_api_v1_extract"
FEED_ENTRY_ID = "POST_api_v1_feed"
FEED_PATCH_ENTRY_ID = "PATCH_api_v1_feeds_{feed_id}"
FEED_COMPLETE_ENTRY_ID = "POST_api_v1_feeds_{feed_id}_complete"
FEED_CANCEL_ENTRY_ID = "POST_api_v1_feeds_{feed_id}_cancel"
MESSAGES_ENTRY_ID = "GET_api_v1_messages"
MEMORIES_LIST_ENTRY_ID = "GET_api_v1_memories"
MEMORIES_PATCH_ENTRY_ID = "PATCH_api_v1_memories_{memory_id}"
MEMORIES_DELETE_ONE_ENTRY_ID = "DELETE_api_v1_memories_{memory_id}"
MEMORIES_CLEAR_ENTRY_ID = "DELETE_api_v1_memories"
COMPLETE_ENTRY_ID = "POST_api_v1_onboarding_complete"
STORAGE_UPLOAD_ENTRY_ID = "POST_api_v1_storage_sight-upload-url"
MODERATE_SIGHT_ENTRY_ID = "POST_api_v1_moderate-sight"
TRANSCRIBE_ENTRY_ID = "POST_api_v1_transcribe"
SYNTHESIZE_ENTRY_ID = "POST_api_v1_synthesize"
PACTS_ENTRY_ID = "POST_api_v1_pacts"
PACT_SESSION_ENTRY_ID = "POST_api_v1_pact-session"
PACT_ANSWER_ENTRY_ID = "POST_api_v1_pact-answer"
PACT_SKIP_ENTRY_ID = "POST_api_v1_pact-skip"
FRIENDS_LIST_ENTRY_ID = "GET_api_v1_friends"
FRIENDS_ADD_ENTRY_ID = "POST_api_v1_friends"
FRIENDS_REMOVE_ENTRY_ID = "DELETE_api_v1_friends_{friend_id}"
POSTCARDS_LIST_ENTRY_ID = "GET_api_v1_postcards"
POSTCARDS_READ_ENTRY_ID = "PATCH_api_v1_postcards_{postcard_id}_read"
DEVICES_ENTRY_ID = "POST_api_v1_devices"
REPORT_ENTRY_ID = "GET_api_v1_report"
REPORT_LINE_ENTRY_ID = "POST_api_v1_report_line"
SPIRIT_PATCH_ENTRY_ID = "PATCH_api_v1_spirit"
ACCOUNT_DELETE_ENTRY_ID = "DELETE_api_v1_account"
RECALL_ENTRY_ID = "POST_api_v1_recall"
SPIRITS_BUSINESS_CODES = frozenset({"CONFLICT", "IDEMPOTENCY_CONFLICT"})
BOOTSTRAP_BUSINESS_CODES = frozenset({"DEPENDENCY_UNAVAILABLE"})
CHAT_BUSINESS_CODES = frozenset({"MODEL_UNAVAILABLE"})
EXTRACT_BUSINESS_CODES = frozenset({"MODEL_UNAVAILABLE"})
FEED_BUSINESS_CODES = frozenset({"QUOTA_EXCEEDED"})
PROMISE_MUTATION_BUSINESS_CODES = frozenset({"PROMISE_NOT_ACTIVE"})
MESSAGES_BUSINESS_CODES = frozenset({"INVALID_CURSOR"})
MEMORIES_LIST_BUSINESS_CODES = frozenset({"INVALID_CURSOR"})
MEMORIES_PATCH_BUSINESS_CODES = frozenset({"CONFLICT", "MEMORY_NOT_ACTIVE"})
MEMORIES_DELETE_ONE_BUSINESS_CODES = frozenset({"CONFLICT", "NOT_FOUND"})
MEMORIES_CLEAR_BUSINESS_CODES = frozenset({"INVALID_INPUT", "IDEMPOTENCY_CONFLICT"})
MEMORY_LIST_EXTRA_SCENARIOS = ("filter_mismatch_cursor",)
MEMORY_PATCH_EXTRA_SCENARIOS = (
    "idempotency_conflict",
    "version_conflict",
    "not_found",
    "illegal_state",
)
MEMORY_DELETE_ONE_EXTRA_SCENARIOS = (
    "idempotency_conflict",
    "version_conflict",
    "not_found",
)
MEMORY_CLEAR_EXTRA_SCENARIOS = ("idempotency_conflict",)
COMPLETE_BUSINESS_CODES = frozenset({"ONBOARDING_INCOMPLETE"})
STORAGE_UPLOAD_BUSINESS_CODES = frozenset({"UPLOAD_EXPIRED"})
MODERATE_SIGHT_BUSINESS_CODES = frozenset({"UPLOAD_NOT_READY"})
TRANSCRIBE_BUSINESS_CODES = frozenset({"ASR_EMPTY_RESULT"})
SYNTHESIZE_BUSINESS_CODES = frozenset({"NOT_FOUND"})
PACTS_BUSINESS_CODES = frozenset({"PACT_ALREADY_ACTIVE"})
PACT_SESSION_BUSINESS_CODES = frozenset({"PACT_DAY_CLOSED"})
PACT_ANSWER_BUSINESS_CODES = frozenset({"PACT_QUESTION_ALREADY_ANSWERED"})
PACT_SKIP_BUSINESS_CODES = frozenset({"PACT_SESSION_ALREADY_SUBMITTED"})
FRIENDS_LIST_BUSINESS_CODES = frozenset({"INVALID_CURSOR"})
FRIENDS_ADD_BUSINESS_CODES = frozenset({"SELF_FRIEND_NOT_ALLOWED"})
FRIENDS_REMOVE_BUSINESS_CODES = frozenset({"NOT_FOUND"})
POSTCARDS_LIST_BUSINESS_CODES = frozenset({"INVALID_CURSOR"})
POSTCARDS_READ_BUSINESS_CODES = frozenset({"NOT_FOUND"})
TRANSCRIBE_EXTRA_SCENARIOS = (
    "empty_result",
    "invalid_mime",
    "too_large",
    "duration_exceeded",
)
SYNTHESIZE_EXTRA_SCENARIOS = ("cache_hit", "quota_exceeded")
PACTS_EXTRA_SCENARIOS = ("active_conflict", "notes_not_found")
PACT_SESSION_EXTRA_SCENARIOS = ("day_closed", "idempotent_replay")
PACT_ANSWER_EXTRA_SCENARIOS = (
    "last_question_finalize",
    "already_answered",
    "question_not_found",
    "version_conflict",
    "out_of_order",
    "last_question_in_progress",
    "provider_fallback",
)
PACT_SKIP_EXTRA_SCENARIOS = ("already_submitted", "skip_replay", "completeness")
FRIENDS_ADD_EXTRA_SCENARIOS = ("self_friend", "invalid_invite", "idempotent_replay")
FRIENDS_REMOVE_EXTRA_SCENARIOS = ("not_found", "idempotency_conflict")
POSTCARDS_READ_EXTRA_SCENARIOS = ("not_found", "read_replay")
DEVICES_BUSINESS_CODES = frozenset({"INVALID_INPUT"})
DEVICES_EXTRA_SCENARIOS = (
    "token_rotate",
    "environment_mismatch",
    "enabled_false",
    "idempotent_replay",
)
REPORT_BUSINESS_CODES = frozenset({"DEPENDENCY_UNAVAILABLE"})
REPORT_EXTRA_SCENARIOS = (
    "generating",
    "partial",
    "ready",
    "failed",
)
REPORT_LINE_BUSINESS_CODES = frozenset({"REPORT_LINE_UNAVAILABLE"})
REPORT_LINE_EXTRA_SCENARIOS = (
    "idempotent_replay",
    "version_conflict",
    "max_attempts",
    "provider_fail",
)
SPIRIT_PATCH_BUSINESS_CODES = frozenset({"CONFLICT"})
SPIRIT_PATCH_EXTRA_SCENARIOS = (
    "idempotent_replay",
    "version_conflict",
    "illegal_timezone",
    "illegal_dnd",
    "name_too_long",
)
ACCOUNT_DELETE_BUSINESS_CODES = frozenset({"ACCOUNT_DELETE_PENDING"})
ACCOUNT_DELETE_EXTRA_SCENARIOS = (
    "idempotent_replay",
    "confirm_mismatch",
    "pending",
)
RECALL_BUSINESS_CODES = frozenset({"SPIRIT_NOT_LOST"})
RECALL_EXTRA_SCENARIOS = (
    "idempotent_replay",
    "not_lost",
    "sealed_memory",
    "deleted_memory",
    "wrong_owner",
    "non_sight",
    "quota_full_food",
)
FORBIDDEN_KEY_FRAGMENTS = (
    "jwt",
    "password",
    "secret",
    "prompt",
    "access_token",
    "refresh_token",
    "authorization",
    "signed_url",
    "latitude",
    "longitude",
    "apns_token",
)
FORBIDDEN_SUBSTRINGS = (
    "bearer ",
    "eyj",
    "-----begin",
    "postgresql://",
)


class FixtureKind(StrEnum):
    SUCCESS = "success"
    BUSINESS_ERROR = "business_error"
    MISSING_REQUIRED_FIELD = "missing_required_field"


class FixtureError(Exception):
    pass


class FixtureMissingError(FixtureError):
    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(f"missing fixture: {path}")


class FixtureValidationError(FixtureError):
    pass


class FixtureManifestError(FixtureError):
    pass


@dataclass(frozen=True)
class CatalogEntry:
    id: str
    kind: str
    endpoint: str | None
    scenarios: tuple[str, ...] = BASE_SCENARIOS


@dataclass(frozen=True)
class SuccessFixture:
    data: Any
    request_id: str
    server_time: str


@dataclass(frozen=True)
class BusinessErrorFixture:
    code: str
    message: str
    retryable: bool
    request_id: str
    server_time: str


@dataclass(frozen=True)
class MissingFieldFixture:
    missing_fields: tuple[str, ...]


def envelope_catalog() -> tuple[CatalogEntry, ...]:
    return (
        CatalogEntry(
            id=ENVELOPE_ENTRY_ID,
            kind="envelope_schema",
            endpoint=None,
            scenarios=BASE_SCENARIOS,
        ),
    )


def catalog_for_openapi(document: dict[str, Any]) -> tuple[CatalogEntry, ...]:
    entries = list(envelope_catalog())
    paths = document.get("paths")
    if not isinstance(paths, dict):
        return tuple(entries)
    for path, item in sorted(paths.items()):
        if not str(path).startswith("/api/") or not isinstance(item, dict):
            continue
        for method, operation in item.items():
            if method not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            endpoint = f"{method.upper()} {path}"
            scenarios: tuple[str, ...] = BASE_SCENARIOS
            if endpoint == "GET /api/v1/messages":
                scenarios = BASE_SCENARIOS + LIST_PAGE_SCENARIOS
            elif endpoint == "GET /api/v1/memories":
                scenarios = BASE_SCENARIOS + LIST_PAGE_SCENARIOS + MEMORY_LIST_EXTRA_SCENARIOS
            elif endpoint == "PATCH /api/v1/memories/{memory_id}":
                scenarios = BASE_SCENARIOS + MEMORY_PATCH_EXTRA_SCENARIOS
            elif endpoint == "DELETE /api/v1/memories/{memory_id}":
                scenarios = BASE_SCENARIOS + MEMORY_DELETE_ONE_EXTRA_SCENARIOS
            elif endpoint == "DELETE /api/v1/memories":
                scenarios = BASE_SCENARIOS + MEMORY_CLEAR_EXTRA_SCENARIOS
            elif endpoint == "POST /api/v1/transcribe":
                scenarios = BASE_SCENARIOS + TRANSCRIBE_EXTRA_SCENARIOS
            elif endpoint == "POST /api/v1/synthesize":
                scenarios = BASE_SCENARIOS + SYNTHESIZE_EXTRA_SCENARIOS
            elif endpoint == "POST /api/v1/pacts":
                scenarios = BASE_SCENARIOS + PACTS_EXTRA_SCENARIOS
            elif endpoint == "POST /api/v1/pact-session":
                scenarios = BASE_SCENARIOS + PACT_SESSION_EXTRA_SCENARIOS
            elif endpoint == "POST /api/v1/pact-answer":
                scenarios = BASE_SCENARIOS + PACT_ANSWER_EXTRA_SCENARIOS
            elif endpoint == "POST /api/v1/pact-skip":
                scenarios = BASE_SCENARIOS + PACT_SKIP_EXTRA_SCENARIOS
            elif endpoint == "GET /api/v1/friends":
                scenarios = BASE_SCENARIOS + LIST_PAGE_SCENARIOS
            elif endpoint == "POST /api/v1/friends":
                scenarios = BASE_SCENARIOS + FRIENDS_ADD_EXTRA_SCENARIOS
            elif endpoint == "DELETE /api/v1/friends/{friend_id}":
                scenarios = BASE_SCENARIOS + FRIENDS_REMOVE_EXTRA_SCENARIOS
            elif endpoint == "GET /api/v1/postcards":
                scenarios = BASE_SCENARIOS + LIST_PAGE_SCENARIOS
            elif endpoint == "PATCH /api/v1/postcards/{postcard_id}/read":
                scenarios = BASE_SCENARIOS + POSTCARDS_READ_EXTRA_SCENARIOS
            elif endpoint == "POST /api/v1/devices":
                scenarios = BASE_SCENARIOS + DEVICES_EXTRA_SCENARIOS
            elif endpoint == "GET /api/v1/report":
                scenarios = BASE_SCENARIOS + REPORT_EXTRA_SCENARIOS
            elif endpoint == "POST /api/v1/report/line":
                scenarios = BASE_SCENARIOS + REPORT_LINE_EXTRA_SCENARIOS
            elif endpoint == "PATCH /api/v1/spirit":
                scenarios = BASE_SCENARIOS + SPIRIT_PATCH_EXTRA_SCENARIOS
            elif endpoint == "DELETE /api/v1/account":
                scenarios = BASE_SCENARIOS + ACCOUNT_DELETE_EXTRA_SCENARIOS
            elif endpoint == "POST /api/v1/recall":
                scenarios = BASE_SCENARIOS + RECALL_EXTRA_SCENARIOS
            entries.append(
                CatalogEntry(
                    id=_endpoint_entry_id(str(method), str(path)),
                    kind="endpoint",
                    endpoint=endpoint,
                    scenarios=scenarios,
                )
            )
    return tuple(entries)


def _endpoint_entry_id(method: str, path: str) -> str:
    slug = path.strip("/").replace("/", "_") or "root"
    return f"{method.upper()}_{slug}"


def fixture_path(root: Path, entry_id: str, scenario: str) -> Path:
    return root / entry_id / f"{scenario}.json"


def load_json_fixture(root: Path, entry_id: str, scenario: str) -> dict[str, Any]:
    path = fixture_path(root, entry_id, scenario)
    if not path.is_file():
        raise FixtureMissingError(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise FixtureValidationError(f"{path} must be a JSON object")
    assert_fixture_is_desensitized(raw, path)
    return raw


def required_fixture_paths(
    root: Path, entries: tuple[CatalogEntry, ...] | None = None
) -> list[Path]:
    catalog = entries if entries is not None else envelope_catalog()
    return [
        fixture_path(root, entry.id, scenario) for entry in catalog for scenario in entry.scenarios
    ]


def assert_required_fixtures_exist(
    root: Path, entries: tuple[CatalogEntry, ...] | None = None
) -> None:
    missing = [path for path in required_fixture_paths(root, entries) if not path.is_file()]
    if missing:
        raise FixtureMissingError(missing[0])


def load_success(root: Path, entry_id: str = ENVELOPE_ENTRY_ID) -> SuccessFixture:
    payload = load_json_fixture(root, entry_id, FixtureKind.SUCCESS)
    return parse_success(payload)


def load_business_error(root: Path, entry_id: str = ENVELOPE_ENTRY_ID) -> BusinessErrorFixture:
    payload = load_json_fixture(root, entry_id, FixtureKind.BUSINESS_ERROR)
    return parse_business_error(payload)


def load_missing_required_field(
    root: Path, entry_id: str = ENVELOPE_ENTRY_ID
) -> MissingFieldFixture:
    payload = load_json_fixture(root, entry_id, FixtureKind.MISSING_REQUIRED_FIELD)
    return parse_missing_required_field(payload)


def parse_success(payload: dict[str, Any]) -> SuccessFixture:
    assert_fixture_is_desensitized(payload)
    envelope = _parse_envelope(payload)
    if envelope.ok is not True or envelope.error is not None:
        raise FixtureValidationError("success fixture must have ok=true and error=null")
    return SuccessFixture(
        data=envelope.data,
        request_id=envelope.request_id,
        server_time=envelope.server_time,
    )


def parse_business_error(payload: dict[str, Any]) -> BusinessErrorFixture:
    assert_fixture_is_desensitized(payload)
    envelope = _parse_envelope(payload)
    if envelope.ok is not False or envelope.data is not None or envelope.error is None:
        raise FixtureValidationError(
            "business_error fixture must have ok=false, data=null, and an error object"
        )
    code = envelope.error.code
    if code not in KNOWN_ERROR_CODES:
        raise FixtureValidationError(f"unknown business error code: {code}")
    return BusinessErrorFixture(
        code=code,
        message=envelope.error.message,
        retryable=envelope.error.retryable,
        request_id=envelope.request_id,
        server_time=envelope.server_time,
    )


def parse_missing_required_field(payload: dict[str, Any]) -> MissingFieldFixture:
    assert_fixture_is_desensitized(payload)
    try:
        Envelope.model_validate(payload)
    except ValidationError as exc:
        missing = _missing_field_names(exc)
        if not missing:
            raise FixtureValidationError(
                "missing_required_field fixture must omit a required field, not use a wrong type"
            ) from exc
        return MissingFieldFixture(missing_fields=tuple(missing))
    raise FixtureValidationError("missing_required_field fixture unexpectedly validated")


def evaluate_entry(root: Path, entry: CatalogEntry) -> None:
    if entry.id == ENVELOPE_ENTRY_ID:
        load_success(root, entry.id)
        load_business_error(root, entry.id)
        load_missing_required_field(root, entry.id)
        return
    if entry.id == SPIRITS_ENTRY_ID:
        _evaluate_spirits_entry(root)
        return
    if entry.id == BOOTSTRAP_ENTRY_ID:
        _evaluate_bootstrap_entry(root)
        return
    if entry.id == CHAT_ENTRY_ID:
        _evaluate_chat_entry(root)
        return
    if entry.id == EXTRACT_ENTRY_ID:
        _evaluate_extract_entry(root)
        return
    if entry.id == FEED_ENTRY_ID:
        _evaluate_feed_entry(root)
        return
    if entry.id == FEED_PATCH_ENTRY_ID:
        _evaluate_promise_mutation_entry(root, FEED_PATCH_ENTRY_ID, PromisePatchRequest)
        return
    if entry.id == FEED_COMPLETE_ENTRY_ID:
        _evaluate_promise_mutation_entry(root, FEED_COMPLETE_ENTRY_ID, PromiseActionRequest)
        return
    if entry.id == FEED_CANCEL_ENTRY_ID:
        _evaluate_promise_mutation_entry(root, FEED_CANCEL_ENTRY_ID, PromiseActionRequest)
        return
    if entry.id == MESSAGES_ENTRY_ID:
        _evaluate_messages_entry(root)
        return
    if entry.id == MEMORIES_LIST_ENTRY_ID:
        _evaluate_memories_list_entry(root)
        return
    if entry.id == MEMORIES_PATCH_ENTRY_ID:
        _evaluate_memories_patch_entry(root)
        return
    if entry.id == MEMORIES_DELETE_ONE_ENTRY_ID:
        _evaluate_memories_delete_one_entry(root)
        return
    if entry.id == MEMORIES_CLEAR_ENTRY_ID:
        _evaluate_memories_clear_entry(root)
        return
    if entry.id == COMPLETE_ENTRY_ID:
        _evaluate_complete_entry(root)
        return
    if entry.id == STORAGE_UPLOAD_ENTRY_ID:
        _evaluate_storage_upload_entry(root)
        return
    if entry.id == MODERATE_SIGHT_ENTRY_ID:
        _evaluate_moderate_sight_entry(root)
        return
    if entry.id == TRANSCRIBE_ENTRY_ID:
        _evaluate_transcribe_entry(root)
        return
    if entry.id == SYNTHESIZE_ENTRY_ID:
        _evaluate_synthesize_entry(root)
        return
    if entry.id == PACTS_ENTRY_ID:
        _evaluate_pacts_entry(root)
        return
    if entry.id == PACT_SESSION_ENTRY_ID:
        _evaluate_pact_session_entry(root)
        return
    if entry.id == PACT_ANSWER_ENTRY_ID:
        _evaluate_pact_answer_entry(root)
        return
    if entry.id == PACT_SKIP_ENTRY_ID:
        _evaluate_pact_skip_entry(root)
        return
    if entry.id == FRIENDS_LIST_ENTRY_ID:
        _evaluate_friends_list_entry(root)
        return
    if entry.id == FRIENDS_ADD_ENTRY_ID:
        _evaluate_friends_add_entry(root)
        return
    if entry.id == FRIENDS_REMOVE_ENTRY_ID:
        _evaluate_friends_remove_entry(root)
        return
    if entry.id == POSTCARDS_LIST_ENTRY_ID:
        _evaluate_postcards_list_entry(root)
        return
    if entry.id == POSTCARDS_READ_ENTRY_ID:
        _evaluate_postcards_read_entry(root)
        return
    if entry.id == DEVICES_ENTRY_ID:
        _evaluate_devices_entry(root)
        return
    if entry.id == REPORT_ENTRY_ID:
        _evaluate_report_entry(root)
        return
    if entry.id == REPORT_LINE_ENTRY_ID:
        _evaluate_report_line_entry(root)
        return
    if entry.id == SPIRIT_PATCH_ENTRY_ID:
        _evaluate_spirit_patch_entry(root)
        return
    if entry.id == ACCOUNT_DELETE_ENTRY_ID:
        _evaluate_account_delete_entry(root)
        return
    if entry.id == RECALL_ENTRY_ID:
        _evaluate_recall_entry(root)
        return
    raise FixtureValidationError(f"no fixture evaluator for {entry.id}")


def _evaluate_spirits_entry(root: Path) -> None:
    success = load_success(root, SPIRITS_ENTRY_ID)
    try:
        MutationResult.model_validate(success.data)
    except ValidationError as exc:
        raise FixtureValidationError(f"spirits success data is not MutationResult: {exc}") from exc
    mapped = load_business_error(root, SPIRITS_ENTRY_ID)
    if mapped.code not in SPIRITS_BUSINESS_CODES:
        raise FixtureValidationError(
            f"spirits business_error must be CONFLICT or IDEMPOTENCY_CONFLICT, got {mapped.code}"
        )
    payload = load_json_fixture(root, SPIRITS_ENTRY_ID, FixtureKind.MISSING_REQUIRED_FIELD)
    try:
        CreateSpiritRequest.model_validate(payload)
    except ValidationError as exc:
        missing = _missing_field_names(exc)
        if not missing:
            raise FixtureValidationError(
                "spirits missing_required_field must omit a required field, not use a wrong type"
            ) from exc
        return
    raise FixtureValidationError("spirits missing_required_field unexpectedly validated")


def _evaluate_bootstrap_entry(root: Path) -> None:
    success = load_success(root, BOOTSTRAP_ENTRY_ID)
    if isinstance(success.data, dict) and "server_time" in success.data:
        raise FixtureValidationError("bootstrap data must not repeat Envelope server_time")
    try:
        BootstrapSnapshot.model_validate(success.data)
    except ValidationError as exc:
        raise FixtureValidationError(
            f"bootstrap success data is not BootstrapSnapshot: {exc}"
        ) from exc
    mapped = load_business_error(root, BOOTSTRAP_ENTRY_ID)
    if mapped.code not in BOOTSTRAP_BUSINESS_CODES:
        raise FixtureValidationError(
            f"bootstrap business_error must be DEPENDENCY_UNAVAILABLE, got {mapped.code}"
        )
    payload = load_json_fixture(root, BOOTSTRAP_ENTRY_ID, FixtureKind.MISSING_REQUIRED_FIELD)
    try:
        BootstrapSnapshot.model_validate(payload)
    except ValidationError as exc:
        missing = _missing_field_names(exc)
        if not missing:
            raise FixtureValidationError(
                "bootstrap missing_required_field must omit a required field, not use a wrong type"
            ) from exc
        return
    raise FixtureValidationError("bootstrap missing_required_field unexpectedly validated")


def _evaluate_chat_entry(root: Path) -> None:
    success = load_success(root, CHAT_ENTRY_ID)
    try:
        result = ChatTurnResult.model_validate(success.data)
    except ValidationError as exc:
        raise FixtureValidationError(f"chat success data is not ChatTurnResult: {exc}") from exc
    if result.resource.generation_source != "stub":
        raise FixtureValidationError("chat success fixture must identify generation_source=stub")
    if result.resource.onboarding is not True:
        raise FixtureValidationError("chat success fixture must be an onboarding turn")
    if result.resource.speech_audio is not None:
        raise FixtureValidationError("chat success fixture must keep speech_audio null")
    mapped = load_business_error(root, CHAT_ENTRY_ID)
    if mapped.code not in CHAT_BUSINESS_CODES:
        raise FixtureValidationError(
            f"chat business_error must be MODEL_UNAVAILABLE, got {mapped.code}"
        )
    payload = load_json_fixture(root, CHAT_ENTRY_ID, FixtureKind.MISSING_REQUIRED_FIELD)
    try:
        ChatRequest.model_validate(payload)
    except ValidationError as exc:
        missing = _missing_field_names(exc)
        if not missing:
            raise FixtureValidationError(
                "chat missing_required_field must omit a required field, not use a wrong type"
            ) from exc
        if "onboarding" not in missing:
            raise FixtureValidationError(
                "chat missing_required_field must omit onboarding so the server can distinguish it"
            ) from exc
        return
    raise FixtureValidationError("chat missing_required_field unexpectedly validated")


def _evaluate_extract_entry(root: Path) -> None:
    success = load_success(root, EXTRACT_ENTRY_ID)
    try:
        result = ExtractResult.model_validate(success.data)
    except ValidationError as exc:
        raise FixtureValidationError(f"extract success data is not ExtractResult: {exc}") from exc
    if result.resource.status != "extracted":
        raise FixtureValidationError("extract success fixture must be status=extracted")
    if len(result.resource.memories) > 2:
        raise FixtureValidationError("extract success fixture must have 0-2 memories")
    if len(result.resource.style_samples) > 1:
        raise FixtureValidationError("extract success fixture must have 0-1 style samples")
    mapped = load_business_error(root, EXTRACT_ENTRY_ID)
    if mapped.code not in EXTRACT_BUSINESS_CODES:
        raise FixtureValidationError(
            f"extract business_error must be MODEL_UNAVAILABLE, got {mapped.code}"
        )
    payload = load_json_fixture(root, EXTRACT_ENTRY_ID, FixtureKind.MISSING_REQUIRED_FIELD)
    try:
        ExtractRequest.model_validate(payload)
    except ValidationError as exc:
        missing = _missing_field_names(exc)
        if not missing:
            raise FixtureValidationError(
                "extract missing_required_field must omit a required field, not use a wrong type"
            ) from exc
        if "start_message_id" in payload or "end_message_id" in payload:
            raise FixtureValidationError("extract fixtures must not include message IDs") from exc
        return
    raise FixtureValidationError("extract missing_required_field unexpectedly validated")


def _evaluate_feed_entry(root: Path) -> None:
    success = load_success(root, FEED_ENTRY_ID)
    try:
        result = FeedMutationResult.model_validate(success.data)
    except ValidationError as exc:
        raise FixtureValidationError(f"feed success data is not FeedMutationResult: {exc}") from exc
    if result.resource.type != "feed":
        raise FixtureValidationError("feed success resource type must be feed")
    if result.patch.room is None or result.patch.spirit is None:
        raise FixtureValidationError("feed success patch must include spirit and room")
    mapped = load_business_error(root, FEED_ENTRY_ID)
    if mapped.code not in FEED_BUSINESS_CODES:
        raise FixtureValidationError(
            f"feed business_error must be QUOTA_EXCEEDED, got {mapped.code}"
        )
    payload = load_json_fixture(root, FEED_ENTRY_ID, FixtureKind.MISSING_REQUIRED_FIELD)
    try:
        TypeAdapter(FeedCreateRequest).validate_python(payload)
    except ValidationError as exc:
        missing = _missing_field_names(exc)
        if not missing:
            raise FixtureValidationError(
                "feed missing_required_field must omit a required field, not use a wrong type"
            ) from exc
        return
    raise FixtureValidationError("feed missing_required_field unexpectedly validated")


def _evaluate_promise_mutation_entry(root: Path, entry_id: str, request_type: type[Any]) -> None:
    success = load_success(root, entry_id)
    try:
        result = FeedMutationResult.model_validate(success.data)
    except ValidationError as exc:
        raise FixtureValidationError(
            f"{entry_id} success data is not FeedMutationResult: {exc}"
        ) from exc
    if result.resource.type != "feed":
        raise FixtureValidationError(f"{entry_id} success resource type must be feed")
    if result.resource.kind != "promise":
        raise FixtureValidationError(f"{entry_id} success resource kind must be promise")
    if result.patch.room is None or result.patch.spirit is None:
        raise FixtureValidationError(f"{entry_id} success patch must include spirit and room")
    mapped = load_business_error(root, entry_id)
    if mapped.code not in PROMISE_MUTATION_BUSINESS_CODES:
        raise FixtureValidationError(
            f"{entry_id} business_error must be PROMISE_NOT_ACTIVE, got {mapped.code}"
        )
    payload = load_json_fixture(root, entry_id, FixtureKind.MISSING_REQUIRED_FIELD)
    try:
        request_type.model_validate(payload)
    except ValidationError as exc:
        missing = _missing_field_names(exc)
        if not missing:
            raise FixtureValidationError(
                f"{entry_id} missing_required_field must omit a required field, "
                "not use a wrong type"
            ) from exc
        return
    raise FixtureValidationError(f"{entry_id} missing_required_field unexpectedly validated")


def _evaluate_messages_entry(root: Path) -> None:
    success = load_success(root, MESSAGES_ENTRY_ID)
    try:
        page = MessagePage.model_validate(success.data)
    except ValidationError as exc:
        raise FixtureValidationError(f"messages success data is not MessagePage: {exc}") from exc
    if page.items:
        raise FixtureValidationError("messages success fixture must be the empty page")
    if page.has_more or page.next_cursor is not None:
        raise FixtureValidationError("messages success fixture must have next_cursor=null")
    mapped = load_business_error(root, MESSAGES_ENTRY_ID)
    if mapped.code not in MESSAGES_BUSINESS_CODES:
        raise FixtureValidationError(
            f"messages business_error must be INVALID_CURSOR, got {mapped.code}"
        )
    payload = load_json_fixture(root, MESSAGES_ENTRY_ID, FixtureKind.MISSING_REQUIRED_FIELD)
    try:
        MessagePage.model_validate(payload)
    except ValidationError as exc:
        missing = _missing_field_names(exc)
        if not missing:
            raise FixtureValidationError(
                "messages missing_required_field must omit a required field, not use a wrong type"
            ) from exc
    else:
        raise FixtureValidationError("messages missing_required_field unexpectedly validated")

    empty = _load_message_page(root, "page_empty")
    first = _load_message_page(root, "page_first")
    middle = _load_message_page(root, "page_middle")
    last = _load_message_page(root, "page_last")
    if empty.items or empty.has_more or empty.next_cursor is not None:
        raise FixtureValidationError("page_empty must have no items and next_cursor=null")
    if not first.items or not first.has_more or not first.next_cursor:
        raise FixtureValidationError("page_first must have items, has_more, and next_cursor")
    if not middle.items or not middle.has_more or not middle.next_cursor:
        raise FixtureValidationError("page_middle must have items, has_more, and next_cursor")
    if first.next_cursor == middle.next_cursor:
        raise FixtureValidationError("page_middle cursor must differ from page_first")
    if not last.items or last.has_more or last.next_cursor is not None:
        raise FixtureValidationError("page_last must have items and next_cursor=null")
    _assert_newest_first(first.items)
    _assert_newest_first(middle.items)
    _assert_newest_first(last.items)
    for item in (*first.items, *middle.items, *last.items):
        if item.role == "user" and item.client_id is None:
            raise FixtureValidationError("user history items must include client_id")
        if item.role == "spirit" and item.client_id is not None:
            raise FixtureValidationError("spirit history items must not include client_id")
    expired = load_json_fixture(root, MESSAGES_ENTRY_ID, "invalid_or_expired_cursor")
    expired_error = parse_business_error(expired)
    if expired_error.code != "INVALID_CURSOR":
        raise FixtureValidationError("invalid_or_expired_cursor must use INVALID_CURSOR")


def _load_message_page(root: Path, scenario: str) -> MessagePage:
    loaded = parse_success(load_json_fixture(root, MESSAGES_ENTRY_ID, scenario))
    try:
        return MessagePage.model_validate(loaded.data)
    except ValidationError as exc:
        raise FixtureValidationError(f"{scenario} data is not MessagePage: {exc}") from exc


def _assert_newest_first(items: list[MessagePublic]) -> None:
    previous: tuple[str, str] | None = None
    for item in items:
        key = (item.created_at, str(item.id))
        if previous is not None and key > previous:
            raise FixtureValidationError("message page items must be created_at desc, id desc")
        previous = key


def _evaluate_memories_list_entry(root: Path) -> None:
    success = load_success(root, MEMORIES_LIST_ENTRY_ID)
    try:
        page = MemoryPage.model_validate(success.data)
    except ValidationError as exc:
        raise FixtureValidationError(f"memories success data is not MemoryPage: {exc}") from exc
    if page.items:
        raise FixtureValidationError("memories success fixture must be the empty page")
    if page.tombstones:
        raise FixtureValidationError("memories success fixture must have empty tombstones")
    if page.has_more or page.next_cursor is not None:
        raise FixtureValidationError("memories success fixture must have next_cursor=null")
    mapped = load_business_error(root, MEMORIES_LIST_ENTRY_ID)
    if mapped.code not in MEMORIES_LIST_BUSINESS_CODES:
        raise FixtureValidationError(
            f"memories business_error must be INVALID_CURSOR, got {mapped.code}"
        )
    payload = load_json_fixture(root, MEMORIES_LIST_ENTRY_ID, FixtureKind.MISSING_REQUIRED_FIELD)
    try:
        MemoryListQuery.model_validate(payload)
    except ValidationError as exc:
        missing = _missing_field_names(exc)
        if not missing:
            raise FixtureValidationError(
                "memories missing_required_field must omit a required field, not use a wrong type"
            ) from exc
        if "filter" not in missing:
            raise FixtureValidationError(
                "memories missing_required_field must omit filter"
            ) from exc
    else:
        raise FixtureValidationError("memories missing_required_field unexpectedly validated")

    empty = _load_memory_page(root, "page_empty")
    first = _load_memory_page(root, "page_first")
    middle = _load_memory_page(root, "page_middle")
    last = _load_memory_page(root, "page_last")
    if empty.items or empty.has_more or empty.next_cursor is not None:
        raise FixtureValidationError("page_empty must have no items and next_cursor=null")
    if not first.items or not first.has_more or not first.next_cursor:
        raise FixtureValidationError("page_first must have items, has_more, and next_cursor")
    if not middle.items or not middle.has_more or not middle.next_cursor:
        raise FixtureValidationError("page_middle must have items, has_more, and next_cursor")
    if first.next_cursor == middle.next_cursor:
        raise FixtureValidationError("page_middle cursor must differ from page_first")
    if not last.items or last.has_more or last.next_cursor is not None:
        raise FixtureValidationError("page_last must have items and next_cursor=null")
    _assert_memory_items_newest_first(first.items)
    _assert_memory_items_newest_first(middle.items)
    _assert_memory_items_newest_first(last.items)
    for item in (*first.items, *middle.items, *last.items):
        if item.status == "deleted":
            raise FixtureValidationError("memory page items must not include deleted bodies")
    for stone in (*first.tombstones, *middle.tombstones, *last.tombstones):
        _assert_tombstone_has_no_body(stone)
    expired = load_json_fixture(root, MEMORIES_LIST_ENTRY_ID, "invalid_or_expired_cursor")
    if parse_business_error(expired).code != "INVALID_CURSOR":
        raise FixtureValidationError("invalid_or_expired_cursor must use INVALID_CURSOR")
    mismatched = load_json_fixture(root, MEMORIES_LIST_ENTRY_ID, "filter_mismatch_cursor")
    if parse_business_error(mismatched).code != "INVALID_CURSOR":
        raise FixtureValidationError("filter_mismatch_cursor must use INVALID_CURSOR")


def _load_memory_page(root: Path, scenario: str) -> MemoryPage:
    loaded = parse_success(load_json_fixture(root, MEMORIES_LIST_ENTRY_ID, scenario))
    try:
        return MemoryPage.model_validate(loaded.data)
    except ValidationError as exc:
        raise FixtureValidationError(f"{scenario} data is not MemoryPage: {exc}") from exc


def _assert_memory_items_newest_first(items: list[MemoryPublic]) -> None:
    previous: tuple[str, str] | None = None
    for item in items:
        key = (item.created_at, str(item.id))
        if previous is not None and key > previous:
            raise FixtureValidationError("memory page items must be created_at desc, id desc")
        previous = key


def _assert_tombstone_has_no_body(stone: MemoryTombstone) -> None:
    dumped = stone.model_dump()
    if set(dumped) != {"id", "deleted_at"}:
        raise FixtureValidationError("memory tombstone must only contain id and deleted_at")
    if "summary" in dumped:
        raise FixtureValidationError("deleted memory tombstone must not include summary")


def _evaluate_memories_patch_entry(root: Path) -> None:
    success = load_success(root, MEMORIES_PATCH_ENTRY_ID)
    try:
        result = MemoryMutationResult.model_validate(success.data)
    except ValidationError as exc:
        raise FixtureValidationError(
            f"memory patch success data is not MemoryMutationResult: {exc}"
        ) from exc
    if result.resource.type != "memory":
        raise FixtureValidationError("memory patch success resource type must be memory")
    if result.resource.status != "active":
        raise FixtureValidationError("correct success fixture must keep status=active")
    if not result.patch.memories_upsert:
        raise FixtureValidationError("correct success must upsert the corrected memory")
    mapped = load_business_error(root, MEMORIES_PATCH_ENTRY_ID)
    if mapped.code not in MEMORIES_PATCH_BUSINESS_CODES:
        raise FixtureValidationError(
            f"memory patch business_error must be CONFLICT or MEMORY_NOT_ACTIVE, got {mapped.code}"
        )
    payload = load_json_fixture(root, MEMORIES_PATCH_ENTRY_ID, FixtureKind.MISSING_REQUIRED_FIELD)
    try:
        MemoryPatchRequest.model_validate(payload)
    except ValidationError as exc:
        missing = _missing_field_names(exc)
        if not missing:
            raise FixtureValidationError(
                "memory patch missing_required_field must omit a required field"
            ) from exc
    else:
        raise FixtureValidationError("memory patch missing_required_field unexpectedly validated")
    _assert_named_error(
        root, MEMORIES_PATCH_ENTRY_ID, "idempotency_conflict", "IDEMPOTENCY_CONFLICT"
    )
    _assert_named_error(root, MEMORIES_PATCH_ENTRY_ID, "version_conflict", "CONFLICT")
    _assert_named_error(root, MEMORIES_PATCH_ENTRY_ID, "not_found", "NOT_FOUND")
    _assert_named_error(root, MEMORIES_PATCH_ENTRY_ID, "illegal_state", "MEMORY_NOT_ACTIVE")


def _evaluate_memories_delete_one_entry(root: Path) -> None:
    success = load_success(root, MEMORIES_DELETE_ONE_ENTRY_ID)
    try:
        result = MemoryMutationResult.model_validate(success.data)
    except ValidationError as exc:
        raise FixtureValidationError(
            f"memory delete success data is not MemoryMutationResult: {exc}"
        ) from exc
    if result.resource.type != "memory" or result.resource.status != "deleted":
        raise FixtureValidationError("memory delete success resource must be deleted")
    if result.patch.memories_upsert:
        raise FixtureValidationError("memory delete must not upsert a body")
    if not result.patch.memory_tombstones:
        raise FixtureValidationError("memory delete must return a tombstone")
    for stone in result.patch.memory_tombstones:
        try:
            parsed = MemoryTombstone.model_validate(stone)
        except ValidationError as exc:
            raise FixtureValidationError(
                "memory delete tombstone must be id and deleted_at"
            ) from exc
        _assert_tombstone_has_no_body(parsed)
    mapped = load_business_error(root, MEMORIES_DELETE_ONE_ENTRY_ID)
    if mapped.code not in MEMORIES_DELETE_ONE_BUSINESS_CODES:
        raise FixtureValidationError(
            f"memory delete business_error must be CONFLICT or NOT_FOUND, got {mapped.code}"
        )
    payload = load_json_fixture(
        root, MEMORIES_DELETE_ONE_ENTRY_ID, FixtureKind.MISSING_REQUIRED_FIELD
    )
    try:
        MemoryDeleteRequest.model_validate(payload)
    except ValidationError as exc:
        missing = _missing_field_names(exc)
        if not missing:
            raise FixtureValidationError(
                "memory delete missing_required_field must omit a required field"
            ) from exc
    else:
        raise FixtureValidationError("memory delete missing_required_field unexpectedly validated")
    _assert_named_error(
        root, MEMORIES_DELETE_ONE_ENTRY_ID, "idempotency_conflict", "IDEMPOTENCY_CONFLICT"
    )
    _assert_named_error(root, MEMORIES_DELETE_ONE_ENTRY_ID, "version_conflict", "CONFLICT")
    _assert_named_error(root, MEMORIES_DELETE_ONE_ENTRY_ID, "not_found", "NOT_FOUND")


def _evaluate_memories_clear_entry(root: Path) -> None:
    success = load_success(root, MEMORIES_CLEAR_ENTRY_ID)
    try:
        result = MemoryMutationResult.model_validate(success.data)
    except ValidationError as exc:
        raise FixtureValidationError(
            f"memory clear success data is not MemoryMutationResult: {exc}"
        ) from exc
    if result.resource.type != "memory_clear":
        raise FixtureValidationError("memory clear success resource type must be memory_clear")
    mapped = load_business_error(root, MEMORIES_CLEAR_ENTRY_ID)
    if mapped.code not in MEMORIES_CLEAR_BUSINESS_CODES:
        raise FixtureValidationError(
            f"memory clear business_error must be INVALID_INPUT or IDEMPOTENCY_CONFLICT, "
            f"got {mapped.code}"
        )
    payload = load_json_fixture(root, MEMORIES_CLEAR_ENTRY_ID, FixtureKind.MISSING_REQUIRED_FIELD)
    try:
        MemoryClearRequest.model_validate(payload)
    except ValidationError as exc:
        missing = _missing_field_names(exc)
        if not missing:
            raise FixtureValidationError(
                "memory clear missing_required_field must omit a required field"
            ) from exc
        if "confirm" not in missing:
            raise FixtureValidationError("memory clear missing_required_field must omit confirm")
    else:
        raise FixtureValidationError("memory clear missing_required_field unexpectedly validated")
    _assert_named_error(
        root, MEMORIES_CLEAR_ENTRY_ID, "idempotency_conflict", "IDEMPOTENCY_CONFLICT"
    )


def _assert_named_error(root: Path, entry_id: str, scenario: str, code: str) -> None:
    loaded = parse_business_error(load_json_fixture(root, entry_id, scenario))
    if loaded.code != code:
        raise FixtureValidationError(f"{scenario} must use {code}, got {loaded.code}")


def _evaluate_complete_entry(root: Path) -> None:
    success = load_success(root, COMPLETE_ENTRY_ID)
    try:
        result = OnboardingCompleteResult.model_validate(success.data)
    except ValidationError as exc:
        raise FixtureValidationError(
            f"onboarding complete success data is not OnboardingCompleteResult: {exc}"
        ) from exc
    report = result.patch.report
    if report is None or report.eligibility.completed_dialogue_rounds != 0:
        raise FixtureValidationError(
            "onboarding complete fixture must keep completed_dialogue_rounds at 0"
        )
    mapped = load_business_error(root, COMPLETE_ENTRY_ID)
    if mapped.code not in COMPLETE_BUSINESS_CODES:
        raise FixtureValidationError(
            f"onboarding complete business_error must be ONBOARDING_INCOMPLETE, got {mapped.code}"
        )
    payload = load_json_fixture(root, COMPLETE_ENTRY_ID, FixtureKind.MISSING_REQUIRED_FIELD)
    try:
        CompleteOnboardingRequest.model_validate(payload)
    except ValidationError as exc:
        missing = _missing_field_names(exc)
        if not missing:
            raise FixtureValidationError(
                "onboarding complete missing_required_field must omit a required field, "
                "not use a wrong type"
            ) from exc
        return
    raise FixtureValidationError(
        "onboarding complete missing_required_field unexpectedly validated"
    )


def _evaluate_storage_upload_entry(root: Path) -> None:
    success = load_success(root, STORAGE_UPLOAD_ENTRY_ID)
    try:
        result = SightUploadUrlResult.model_validate(success.data)
    except ValidationError as exc:
        raise FixtureValidationError(
            f"storage upload success data is not SightUploadUrlResult: {exc}"
        ) from exc
    if result.method != "PUT":
        raise FixtureValidationError("storage upload success fixture must use method PUT")
    if result.headers.get("content-type") != "image/jpeg":
        raise FixtureValidationError("storage upload success fixture must lock jpeg content-type")
    if result.max_size_bytes != 5242880:
        raise FixtureValidationError("storage upload success fixture must lock max_size_bytes")
    parts = result.object_path.split("/")
    if (
        len(parts) != 4
        or parts[0] != "sight-temp"
        or not parts[3].endswith(".jpg")
        or ".." in result.object_path
    ):
        raise FixtureValidationError("storage upload success fixture path must be four segments")
    lowered_url = result.url.lower()
    if "service_role" in lowered_url or "eyj" in lowered_url:
        raise FixtureValidationError("storage upload success fixture must not look like a JWT")
    mapped = load_business_error(root, STORAGE_UPLOAD_ENTRY_ID)
    if mapped.code not in STORAGE_UPLOAD_BUSINESS_CODES:
        raise FixtureValidationError(
            f"storage upload business_error must be UPLOAD_EXPIRED, got {mapped.code}"
        )
    payload = load_json_fixture(root, STORAGE_UPLOAD_ENTRY_ID, FixtureKind.MISSING_REQUIRED_FIELD)
    if "object_path" in payload or "bucket" in payload:
        raise FixtureValidationError("clients must not supply object_path or bucket")
    try:
        SightUploadUrlRequest.model_validate(payload)
    except ValidationError as exc:
        missing = _missing_field_names(exc)
        if not missing:
            raise FixtureValidationError(
                "storage upload missing_required_field must omit a required field, "
                "not use a wrong type"
            ) from exc
        return
    raise FixtureValidationError("storage upload missing_required_field unexpectedly validated")


def _evaluate_moderate_sight_entry(root: Path) -> None:
    success = load_success(root, MODERATE_SIGHT_ENTRY_ID)
    try:
        result = ModerateSightResult.model_validate(success.data)
    except ValidationError as exc:
        raise FixtureValidationError(
            f"moderate-sight success data is not ModerateSightResult: {exc}"
        ) from exc
    if result.resource.kind != "sight" or result.resource.status != "accepted":
        raise FixtureValidationError("moderate-sight success fixture must be accepted sight")
    if result.prop != "lamp":
        raise FixtureValidationError("moderate-sight success fixture must lock a whitelist prop")
    if not result.patch.memories_upsert:
        raise FixtureValidationError("moderate-sight success fixture must include a sight memory")
    mapped = load_business_error(root, MODERATE_SIGHT_ENTRY_ID)
    if mapped.code not in MODERATE_SIGHT_BUSINESS_CODES:
        raise FixtureValidationError(
            f"moderate-sight business_error must be UPLOAD_NOT_READY, got {mapped.code}"
        )
    payload = load_json_fixture(root, MODERATE_SIGHT_ENTRY_ID, FixtureKind.MISSING_REQUIRED_FIELD)
    try:
        ModerateSightRequest.model_validate(payload)
    except ValidationError as exc:
        missing = _missing_field_names(exc)
        if not missing:
            raise FixtureValidationError(
                "moderate-sight missing_required_field must omit a required field, "
                "not use a wrong type"
            ) from exc
        return
    raise FixtureValidationError("moderate-sight missing_required_field unexpectedly validated")


def _evaluate_transcribe_entry(root: Path) -> None:
    success = load_success(root, TRANSCRIBE_ENTRY_ID)
    try:
        result = TranscribeResult.model_validate(success.data)
    except ValidationError as exc:
        raise FixtureValidationError(
            f"transcribe success data is not TranscribeResult: {exc}"
        ) from exc
    if not result.resource.text.strip():
        raise FixtureValidationError("transcribe success fixture must have non-empty text")
    if result.resource.duration_ms < 1 or result.resource.duration_ms > 30_000:
        raise FixtureValidationError("transcribe success duration_ms must be 1..30000")
    asr_quota = next((item for item in result.quotas if item.capability == "asr"), None)
    if asr_quota is None or asr_quota.limit != 60 or asr_quota.used < 1:
        raise FixtureValidationError("transcribe success must return asr quota used>=1 limit=60")
    mapped = load_business_error(root, TRANSCRIBE_ENTRY_ID)
    if mapped.code not in TRANSCRIBE_BUSINESS_CODES:
        raise FixtureValidationError(
            f"transcribe business_error must be ASR_EMPTY_RESULT, got {mapped.code}"
        )
    if mapped.retryable:
        raise FixtureValidationError("ASR_EMPTY_RESULT must not be retryable")
    payload = load_json_fixture(root, TRANSCRIBE_ENTRY_ID, FixtureKind.MISSING_REQUIRED_FIELD)
    try:
        TranscribeForm.model_validate(payload)
    except ValidationError as exc:
        missing = _missing_field_names(exc)
        if not missing:
            raise FixtureValidationError(
                "transcribe missing_required_field must omit a required field, not use a wrong type"
            ) from exc
        if "client_id" not in missing:
            raise FixtureValidationError(
                "transcribe missing_required_field must omit client_id"
            ) from exc
    else:
        raise FixtureValidationError("transcribe missing_required_field unexpectedly validated")
    empty = parse_business_error(load_json_fixture(root, TRANSCRIBE_ENTRY_ID, "empty_result"))
    if empty.code != "ASR_EMPTY_RESULT" or empty.retryable:
        raise FixtureValidationError("empty_result must be non-retryable ASR_EMPTY_RESULT")
    for scenario in ("invalid_mime", "too_large", "duration_exceeded"):
        mapped_input = parse_business_error(load_json_fixture(root, TRANSCRIBE_ENTRY_ID, scenario))
        if mapped_input.code != "INVALID_INPUT":
            raise FixtureValidationError(f"{scenario} must be INVALID_INPUT")


def _evaluate_synthesize_entry(root: Path) -> None:
    success = load_success(root, SYNTHESIZE_ENTRY_ID)
    try:
        result = SynthesizeResult.model_validate(success.data)
    except ValidationError as exc:
        raise FixtureValidationError(
            f"synthesize success data is not SynthesizeResult: {exc}"
        ) from exc
    if result.resource.cache_hit:
        raise FixtureValidationError("synthesize success fixture must be a cache miss")
    if result.resource.mime != "audio/mp4":
        raise FixtureValidationError("synthesize success mime must be audio/mp4")
    if not result.resource.audio_url.startswith("https://"):
        raise FixtureValidationError("synthesize audio_url must be https")
    tts_quota = next((item for item in result.quotas if item.capability == "tts"), None)
    if tts_quota is None or tts_quota.limit != 20 or tts_quota.used != 1:
        raise FixtureValidationError("synthesize success must return tts quota used=1 limit=20")
    mapped = load_business_error(root, SYNTHESIZE_ENTRY_ID)
    if mapped.code not in SYNTHESIZE_BUSINESS_CODES:
        raise FixtureValidationError(
            f"synthesize business_error must be NOT_FOUND, got {mapped.code}"
        )
    payload = load_json_fixture(root, SYNTHESIZE_ENTRY_ID, FixtureKind.MISSING_REQUIRED_FIELD)
    try:
        SynthesizeRequest.model_validate(payload)
    except ValidationError as exc:
        missing = _missing_field_names(exc)
        if not missing:
            raise FixtureValidationError(
                "synthesize missing_required_field must omit a required field, not use a wrong type"
            ) from exc
        if "message_id" not in missing:
            raise FixtureValidationError(
                "synthesize missing_required_field must omit message_id"
            ) from exc
    else:
        raise FixtureValidationError("synthesize missing_required_field unexpectedly validated")
    hit = load_success_from_scenario(root, SYNTHESIZE_ENTRY_ID, "cache_hit")
    try:
        hit_result = SynthesizeResult.model_validate(hit.data)
    except ValidationError as exc:
        raise FixtureValidationError(f"cache_hit data is not SynthesizeResult: {exc}") from exc
    if not hit_result.resource.cache_hit:
        raise FixtureValidationError("cache_hit fixture must set cache_hit=true")
    hit_quota = next((item for item in hit_result.quotas if item.capability == "tts"), None)
    if hit_quota is None or hit_quota.used != tts_quota.used:
        raise FixtureValidationError("cache_hit must not increment tts quota used")
    exceeded = parse_business_error(load_json_fixture(root, SYNTHESIZE_ENTRY_ID, "quota_exceeded"))
    if exceeded.code != "QUOTA_EXCEEDED" or exceeded.retryable:
        raise FixtureValidationError("quota_exceeded must be non-retryable QUOTA_EXCEEDED")
    exceeded_payload = load_json_fixture(root, SYNTHESIZE_ENTRY_ID, "quota_exceeded")
    details = _parse_envelope(exceeded_payload).error
    if details is None or not details.details or details.details.get("quota") != "tts":
        raise FixtureValidationError("tts quota_exceeded must include details.quota=tts")


def _evaluate_pacts_entry(root: Path) -> None:
    success = load_success(root, PACTS_ENTRY_ID)
    try:
        result = CreatePactResult.model_validate(success.data)
    except ValidationError as exc:
        raise FixtureValidationError(f"pacts success data is not CreatePactResult: {exc}") from exc
    if result.resource.status != "active" or result.resource.theme != "interview":
        raise FixtureValidationError("pacts success fixture must be an active interview pact")
    if result.resource.question_bank_version == "":
        raise FixtureValidationError("pacts success must lock question_bank_version")
    if result.patch.pact is None or result.patch.pact.id != result.resource.id:
        raise FixtureValidationError("pacts success must patch the created pact")
    mapped = load_business_error(root, PACTS_ENTRY_ID)
    if mapped.code not in PACTS_BUSINESS_CODES or mapped.retryable:
        raise FixtureValidationError(
            "pacts business_error must be non-retryable PACT_ALREADY_ACTIVE"
        )
    payload = load_json_fixture(root, PACTS_ENTRY_ID, FixtureKind.MISSING_REQUIRED_FIELD)
    try:
        CreatePactRequest.model_validate(payload)
    except ValidationError as exc:
        missing = _missing_field_names(exc)
        if not missing or "client_id" not in missing:
            raise FixtureValidationError(
                "pacts missing_required_field must omit client_id"
            ) from exc
    else:
        raise FixtureValidationError("pacts missing_required_field unexpectedly validated")
    conflict = parse_business_error(load_json_fixture(root, PACTS_ENTRY_ID, "active_conflict"))
    if conflict.code != "PACT_ALREADY_ACTIVE":
        raise FixtureValidationError("active_conflict must be PACT_ALREADY_ACTIVE")
    notes = parse_business_error(load_json_fixture(root, PACTS_ENTRY_ID, "notes_not_found"))
    if notes.code != "NOT_FOUND":
        raise FixtureValidationError("notes_not_found must be NOT_FOUND")


def _evaluate_pact_session_entry(root: Path) -> None:
    success = load_success(root, PACT_SESSION_ENTRY_ID)
    try:
        result = PactSessionResult.model_validate(success.data)
    except ValidationError as exc:
        raise FixtureValidationError(
            f"pact-session success data is not PactSessionResult: {exc}"
        ) from exc
    if len(result.resource.questions) != 3:
        raise FixtureValidationError("pact-session success must return exactly 3 questions")
    ids = [item.question_id for item in result.resource.questions]
    if len(set(ids)) != 3:
        raise FixtureValidationError("pact-session questions must have stable unique question_id")
    if result.resource.row_version < 1:
        raise FixtureValidationError("pact-session success must lock row_version")
    mapped = load_business_error(root, PACT_SESSION_ENTRY_ID)
    if mapped.code not in PACT_SESSION_BUSINESS_CODES:
        raise FixtureValidationError("pact-session business_error must be PACT_DAY_CLOSED")
    payload = load_json_fixture(root, PACT_SESSION_ENTRY_ID, FixtureKind.MISSING_REQUIRED_FIELD)
    try:
        PactSessionRequest.model_validate(payload)
    except ValidationError as exc:
        missing = _missing_field_names(exc)
        if not missing or "pact_id" not in missing:
            raise FixtureValidationError(
                "pact-session missing_required_field must omit pact_id"
            ) from exc
    else:
        raise FixtureValidationError("pact-session missing_required_field unexpectedly validated")
    closed = parse_business_error(load_json_fixture(root, PACT_SESSION_ENTRY_ID, "day_closed"))
    if closed.code != "PACT_DAY_CLOSED":
        raise FixtureValidationError("day_closed must be PACT_DAY_CLOSED")
    replay = load_success_from_scenario(root, PACT_SESSION_ENTRY_ID, "idempotent_replay")
    replayed = PactSessionResult.model_validate(replay.data)
    if replayed.resource.id != result.resource.id:
        raise FixtureValidationError("idempotent_replay must return the same session id")


def _evaluate_pact_answer_entry(root: Path) -> None:
    success = load_success(root, PACT_ANSWER_ENTRY_ID)
    try:
        result = PactAnswerResult.model_validate(success.data)
    except ValidationError as exc:
        raise FixtureValidationError(
            f"pact-answer success data is not PactAnswerResult: {exc}"
        ) from exc
    if result.resource.finalized or result.resource.answered_count != 1:
        raise FixtureValidationError("pact-answer success must be a non-final first question")
    if result.resource.score is not None or result.resource.pact_status is not None:
        raise FixtureValidationError("non-final answers must omit score and pact_status")
    mapped = load_business_error(root, PACT_ANSWER_ENTRY_ID)
    if mapped.code not in PACT_ANSWER_BUSINESS_CODES:
        raise FixtureValidationError(
            "pact-answer business_error must be PACT_QUESTION_ALREADY_ANSWERED"
        )
    payload = load_json_fixture(root, PACT_ANSWER_ENTRY_ID, FixtureKind.MISSING_REQUIRED_FIELD)
    try:
        PactAnswerRequest.model_validate(payload)
    except ValidationError as exc:
        missing = _missing_field_names(exc)
        if not missing or "expected_session_version" not in missing:
            raise FixtureValidationError(
                "pact-answer missing_required_field must omit expected_session_version"
            ) from exc
    else:
        raise FixtureValidationError("pact-answer missing_required_field unexpectedly validated")
    finalized = load_success_from_scenario(root, PACT_ANSWER_ENTRY_ID, "last_question_finalize")
    final_result = PactAnswerResult.model_validate(finalized.data)
    if (
        not final_result.resource.finalized
        or final_result.resource.score is None
        or final_result.resource.pact_status is None
        or final_result.patch.pact is None
        or final_result.patch.spirit is None
    ):
        raise FixtureValidationError(
            "last_question_finalize must lock score, pact_status, and patch"
        )
    if not final_result.resource.mistakes:
        raise FixtureValidationError("last_question_finalize must include mistake summaries")
    already = parse_business_error(
        load_json_fixture(root, PACT_ANSWER_ENTRY_ID, "already_answered")
    )
    if already.code != "PACT_QUESTION_ALREADY_ANSWERED":
        raise FixtureValidationError("already_answered must be PACT_QUESTION_ALREADY_ANSWERED")
    missing_q = parse_business_error(
        load_json_fixture(root, PACT_ANSWER_ENTRY_ID, "question_not_found")
    )
    if missing_q.code != "PACT_QUESTION_NOT_FOUND":
        raise FixtureValidationError("question_not_found must be PACT_QUESTION_NOT_FOUND")
    version = parse_business_error(
        load_json_fixture(root, PACT_ANSWER_ENTRY_ID, "version_conflict")
    )
    if version.code != "CONFLICT":
        raise FixtureValidationError("version_conflict must be CONFLICT")
    order = parse_business_error(load_json_fixture(root, PACT_ANSWER_ENTRY_ID, "out_of_order"))
    if order.code != "INVALID_INPUT":
        raise FixtureValidationError("out_of_order must be INVALID_INPUT")
    racing = parse_business_error(
        load_json_fixture(root, PACT_ANSWER_ENTRY_ID, "last_question_in_progress")
    )
    if racing.code != "IDEMPOTENCY_IN_PROGRESS" or not racing.retryable:
        raise FixtureValidationError("last_question_in_progress must be retryable")
    fallback = load_success_from_scenario(root, PACT_ANSWER_ENTRY_ID, "provider_fallback")
    fallback_result = PactAnswerResult.model_validate(fallback.data)
    if fallback_result.resource.finalized:
        raise FixtureValidationError("provider_fallback must stay incremental")


def _evaluate_pact_skip_entry(root: Path) -> None:
    success = load_success(root, PACT_SKIP_ENTRY_ID)
    try:
        result = PactSkipResult.model_validate(success.data)
    except ValidationError as exc:
        raise FixtureValidationError(
            f"pact-skip success data is not PactSkipResult: {exc}"
        ) from exc
    if result.resource.status != "skipped":
        raise FixtureValidationError("pact-skip success must be status=skipped")
    if result.patch.pact is None or result.patch.pact.completed_sessions != 0:
        raise FixtureValidationError("skip must not count a completed session")
    mapped = load_business_error(root, PACT_SKIP_ENTRY_ID)
    if mapped.code not in PACT_SKIP_BUSINESS_CODES:
        raise FixtureValidationError(
            "pact-skip business_error must be PACT_SESSION_ALREADY_SUBMITTED"
        )
    payload = load_json_fixture(root, PACT_SKIP_ENTRY_ID, FixtureKind.MISSING_REQUIRED_FIELD)
    try:
        PactSkipRequest.model_validate(payload)
    except ValidationError as exc:
        missing = _missing_field_names(exc)
        if not missing or "session_date" not in missing:
            raise FixtureValidationError(
                "pact-skip missing_required_field must omit session_date"
            ) from exc
    else:
        raise FixtureValidationError("pact-skip missing_required_field unexpectedly validated")
    submitted = parse_business_error(
        load_json_fixture(root, PACT_SKIP_ENTRY_ID, "already_submitted")
    )
    if submitted.code != "PACT_SESSION_ALREADY_SUBMITTED":
        raise FixtureValidationError("already_submitted must be PACT_SESSION_ALREADY_SUBMITTED")
    replay = load_success_from_scenario(root, PACT_SKIP_ENTRY_ID, "skip_replay")
    replayed = PactSkipResult.model_validate(replay.data)
    if replayed.resource.id != result.resource.id:
        raise FixtureValidationError("skip_replay must return the same session")
    complete = load_success_from_scenario(root, PACT_SKIP_ENTRY_ID, "completeness")
    complete_result = PactSkipResult.model_validate(complete.data)
    if complete_result.patch.pact is None or complete_result.patch.pact.completeness < 70:
        raise FixtureValidationError("completeness fixture must expose completeness")


def _evaluate_friends_list_entry(root: Path) -> None:
    success = load_success(root, FRIENDS_LIST_ENTRY_ID)
    try:
        page = FriendPage.model_validate(success.data)
    except ValidationError as exc:
        raise FixtureValidationError(f"friends success data is not FriendPage: {exc}") from exc
    if page.items:
        raise FixtureValidationError("friends success fixture must be the empty page")
    if page.has_more or page.next_cursor is not None:
        raise FixtureValidationError("friends success fixture must have next_cursor=null")
    mapped = load_business_error(root, FRIENDS_LIST_ENTRY_ID)
    if mapped.code not in FRIENDS_LIST_BUSINESS_CODES:
        raise FixtureValidationError("friends business_error must be INVALID_CURSOR")
    payload = load_json_fixture(root, FRIENDS_LIST_ENTRY_ID, FixtureKind.MISSING_REQUIRED_FIELD)
    try:
        FriendPage.model_validate(payload)
    except ValidationError as exc:
        missing = _missing_field_names(exc)
        if not missing:
            raise FixtureValidationError(
                "friends missing_required_field must omit a required field"
            ) from exc
    else:
        raise FixtureValidationError("friends missing_required_field unexpectedly validated")
    empty = _load_friend_page(root, "page_empty")
    first = _load_friend_page(root, "page_first")
    middle = _load_friend_page(root, "page_middle")
    last = _load_friend_page(root, "page_last")
    if empty.items or empty.has_more or empty.next_cursor is not None:
        raise FixtureValidationError("page_empty must have no items and next_cursor=null")
    if not first.items or not first.has_more or not first.next_cursor:
        raise FixtureValidationError("page_first must have items, has_more, and next_cursor")
    if not middle.items or not middle.has_more or not middle.next_cursor:
        raise FixtureValidationError("page_middle must have items, has_more, and next_cursor")
    if first.next_cursor == middle.next_cursor:
        raise FixtureValidationError("page_middle cursor must differ from page_first")
    if not last.items or last.has_more or last.next_cursor is not None:
        raise FixtureValidationError("page_last must have items and next_cursor=null")
    _assert_friends_newest_first(first.items)
    _assert_friends_newest_first(middle.items)
    _assert_friends_newest_first(last.items)
    expired = parse_business_error(
        load_json_fixture(root, FRIENDS_LIST_ENTRY_ID, "invalid_or_expired_cursor")
    )
    if expired.code != "INVALID_CURSOR":
        raise FixtureValidationError("invalid_or_expired_cursor must use INVALID_CURSOR")


def _load_friend_page(root: Path, scenario: str) -> FriendPage:
    loaded = parse_success(load_json_fixture(root, FRIENDS_LIST_ENTRY_ID, scenario))
    try:
        return FriendPage.model_validate(loaded.data)
    except ValidationError as exc:
        raise FixtureValidationError(f"{scenario} data is not FriendPage: {exc}") from exc


def _assert_friends_newest_first(items: list[Any]) -> None:
    previous: tuple[str, str] | None = None
    for item in items:
        key = (item.created_at, str(item.friend_id))
        if previous is not None and key > previous:
            raise FixtureValidationError("friend page items must be created_at desc, id desc")
        previous = key


def _evaluate_friends_add_entry(root: Path) -> None:
    success = load_success(root, FRIENDS_ADD_ENTRY_ID)
    try:
        result = AddFriendResult.model_validate(success.data)
    except ValidationError as exc:
        raise FixtureValidationError(
            f"friends add success data is not AddFriendResult: {exc}"
        ) from exc
    if result.patch.social is None:
        raise FixtureValidationError("friends add must patch social")
    mapped = load_business_error(root, FRIENDS_ADD_ENTRY_ID)
    if mapped.code not in FRIENDS_ADD_BUSINESS_CODES or mapped.retryable:
        raise FixtureValidationError(
            "friends business_error must be non-retryable SELF_FRIEND_NOT_ALLOWED"
        )
    payload = load_json_fixture(root, FRIENDS_ADD_ENTRY_ID, FixtureKind.MISSING_REQUIRED_FIELD)
    try:
        AddFriendRequest.model_validate(payload)
    except ValidationError as exc:
        missing = _missing_field_names(exc)
        if not missing or "client_id" not in missing:
            raise FixtureValidationError(
                "friends missing_required_field must omit client_id"
            ) from exc
    else:
        raise FixtureValidationError("friends missing_required_field unexpectedly validated")
    self_add = parse_business_error(load_json_fixture(root, FRIENDS_ADD_ENTRY_ID, "self_friend"))
    if self_add.code != "SELF_FRIEND_NOT_ALLOWED":
        raise FixtureValidationError("self_friend must be SELF_FRIEND_NOT_ALLOWED")
    invalid = parse_business_error(load_json_fixture(root, FRIENDS_ADD_ENTRY_ID, "invalid_invite"))
    if invalid.code != "INVALID_INPUT":
        raise FixtureValidationError("invalid_invite must be INVALID_INPUT")
    replay = load_success_from_scenario(root, FRIENDS_ADD_ENTRY_ID, "idempotent_replay")
    replayed = AddFriendResult.model_validate(replay.data)
    if replayed.resource.friend_id != result.resource.friend_id:
        raise FixtureValidationError("idempotent_replay must return the same friend_id")


def _evaluate_friends_remove_entry(root: Path) -> None:
    success = load_success(root, FRIENDS_REMOVE_ENTRY_ID)
    try:
        RemoveFriendResult.model_validate(success.data)
    except ValidationError as exc:
        raise FixtureValidationError(
            f"friends remove success data is not RemoveFriendResult: {exc}"
        ) from exc
    mapped = load_business_error(root, FRIENDS_REMOVE_ENTRY_ID)
    if mapped.code not in FRIENDS_REMOVE_BUSINESS_CODES:
        raise FixtureValidationError("friends remove business_error must be NOT_FOUND")
    payload = load_json_fixture(root, FRIENDS_REMOVE_ENTRY_ID, FixtureKind.MISSING_REQUIRED_FIELD)
    try:
        RemoveFriendRequest.model_validate(payload)
    except ValidationError as exc:
        missing = _missing_field_names(exc)
        if not missing or "client_id" not in missing:
            raise FixtureValidationError(
                "friends remove missing_required_field must omit client_id"
            ) from exc
    else:
        raise FixtureValidationError("friends remove missing_required_field unexpectedly validated")
    missing_edge = parse_business_error(
        load_json_fixture(root, FRIENDS_REMOVE_ENTRY_ID, "not_found")
    )
    if missing_edge.code != "NOT_FOUND":
        raise FixtureValidationError("not_found must be NOT_FOUND")
    conflict = parse_business_error(
        load_json_fixture(root, FRIENDS_REMOVE_ENTRY_ID, "idempotency_conflict")
    )
    if conflict.code != "IDEMPOTENCY_CONFLICT":
        raise FixtureValidationError("idempotency_conflict must be IDEMPOTENCY_CONFLICT")


def _evaluate_postcards_list_entry(root: Path) -> None:
    success = load_success(root, POSTCARDS_LIST_ENTRY_ID)
    try:
        page = PostcardPage.model_validate(success.data)
    except ValidationError as exc:
        raise FixtureValidationError(f"postcards success data is not PostcardPage: {exc}") from exc
    if page.items:
        raise FixtureValidationError("postcards success fixture must be the empty page")
    mapped = load_business_error(root, POSTCARDS_LIST_ENTRY_ID)
    if mapped.code not in POSTCARDS_LIST_BUSINESS_CODES:
        raise FixtureValidationError("postcards business_error must be INVALID_CURSOR")
    payload = load_json_fixture(root, POSTCARDS_LIST_ENTRY_ID, FixtureKind.MISSING_REQUIRED_FIELD)
    try:
        PostcardPage.model_validate(payload)
    except ValidationError as exc:
        missing = _missing_field_names(exc)
        if not missing:
            raise FixtureValidationError(
                "postcards missing_required_field must omit a required field"
            ) from exc
    else:
        raise FixtureValidationError("postcards missing_required_field unexpectedly validated")
    empty = _load_postcard_page(root, "page_empty")
    first = _load_postcard_page(root, "page_first")
    middle = _load_postcard_page(root, "page_middle")
    last = _load_postcard_page(root, "page_last")
    if empty.items or empty.has_more or empty.next_cursor is not None:
        raise FixtureValidationError("page_empty must have no items and next_cursor=null")
    if not first.items or not first.has_more or not first.next_cursor:
        raise FixtureValidationError("page_first must have items, has_more, and next_cursor")
    if not middle.items or not middle.has_more or not middle.next_cursor:
        raise FixtureValidationError("page_middle must have items, has_more, and next_cursor")
    if first.next_cursor == middle.next_cursor:
        raise FixtureValidationError("page_middle cursor must differ from page_first")
    if not last.items or last.has_more or last.next_cursor is not None:
        raise FixtureValidationError("page_last must have items and next_cursor=null")
    _assert_postcards_newest_first(first.items)
    _assert_postcards_newest_first(middle.items)
    _assert_postcards_newest_first(last.items)
    expired = parse_business_error(
        load_json_fixture(root, POSTCARDS_LIST_ENTRY_ID, "invalid_or_expired_cursor")
    )
    if expired.code != "INVALID_CURSOR":
        raise FixtureValidationError("invalid_or_expired_cursor must use INVALID_CURSOR")


def _load_postcard_page(root: Path, scenario: str) -> PostcardPage:
    loaded = parse_success(load_json_fixture(root, POSTCARDS_LIST_ENTRY_ID, scenario))
    try:
        return PostcardPage.model_validate(loaded.data)
    except ValidationError as exc:
        raise FixtureValidationError(f"{scenario} data is not PostcardPage: {exc}") from exc


def _assert_postcards_newest_first(items: list[Any]) -> None:
    previous: tuple[str, str] | None = None
    for item in items:
        key = (item.created_at, str(item.id))
        if previous is not None and key > previous:
            raise FixtureValidationError("postcard page items must be created_at desc, id desc")
        previous = key


def _evaluate_postcards_read_entry(root: Path) -> None:
    success = load_success(root, POSTCARDS_READ_ENTRY_ID)
    try:
        result = PostcardReadResult.model_validate(success.data)
    except ValidationError as exc:
        raise FixtureValidationError(
            f"postcard read success data is not PostcardReadResult: {exc}"
        ) from exc
    if result.resource.read_at is None:
        raise FixtureValidationError("postcard read success must include read_at")
    mapped = load_business_error(root, POSTCARDS_READ_ENTRY_ID)
    if mapped.code not in POSTCARDS_READ_BUSINESS_CODES:
        raise FixtureValidationError("postcard read business_error must be NOT_FOUND")
    payload = load_json_fixture(root, POSTCARDS_READ_ENTRY_ID, FixtureKind.MISSING_REQUIRED_FIELD)
    try:
        PostcardReadRequest.model_validate(payload)
    except ValidationError as exc:
        missing = _missing_field_names(exc)
        if not missing or "client_id" not in missing:
            raise FixtureValidationError(
                "postcard read missing_required_field must omit client_id"
            ) from exc
    else:
        raise FixtureValidationError("postcard read missing_required_field unexpectedly validated")
    missing_card = parse_business_error(
        load_json_fixture(root, POSTCARDS_READ_ENTRY_ID, "not_found")
    )
    if missing_card.code != "NOT_FOUND":
        raise FixtureValidationError("not_found must be NOT_FOUND")
    replay = load_success_from_scenario(root, POSTCARDS_READ_ENTRY_ID, "read_replay")
    replayed = PostcardReadResult.model_validate(replay.data)
    if replayed.resource.read_at != result.resource.read_at:
        raise FixtureValidationError("read_replay must keep the first read_at")


def _evaluate_devices_entry(root: Path) -> None:
    success = load_success(root, DEVICES_ENTRY_ID)
    try:
        registered = DeviceRegistration.model_validate(success.data)
    except ValidationError as exc:
        raise FixtureValidationError(
            f"devices success data is not DeviceRegistration: {exc}"
        ) from exc
    mapped = load_business_error(root, DEVICES_ENTRY_ID)
    if mapped.code not in DEVICES_BUSINESS_CODES or mapped.retryable:
        raise FixtureValidationError(
            f"devices business_error must be INVALID_INPUT, got {mapped.code}"
        )
    payload = load_json_fixture(root, DEVICES_ENTRY_ID, FixtureKind.MISSING_REQUIRED_FIELD)
    try:
        RegisterDeviceRequest.model_validate(payload)
    except ValidationError as exc:
        missing = _missing_field_names(exc)
        if "apns_token" not in missing and not missing:
            raise FixtureValidationError(
                "devices missing_required_field must omit a required field"
            ) from exc
    else:
        raise FixtureValidationError("devices missing_required_field unexpectedly validated")
    rotated = DeviceRegistration.model_validate(
        load_success_from_scenario(root, DEVICES_ENTRY_ID, "token_rotate").data
    )
    if rotated.device_id != registered.device_id:
        raise FixtureValidationError("token_rotate must keep the same device_id")
    mismatch = parse_business_error(
        load_json_fixture(root, DEVICES_ENTRY_ID, "environment_mismatch")
    )
    if mismatch.code != "INVALID_INPUT":
        raise FixtureValidationError("environment_mismatch must be INVALID_INPUT")
    disabled = DeviceRegistration.model_validate(
        load_success_from_scenario(root, DEVICES_ENTRY_ID, "enabled_false").data
    )
    if disabled.enabled:
        raise FixtureValidationError("enabled_false must have enabled false")
    replay = DeviceRegistration.model_validate(
        load_success_from_scenario(root, DEVICES_ENTRY_ID, "idempotent_replay").data
    )
    if replay.device_id != registered.device_id:
        raise FixtureValidationError("idempotent_replay must return the same device_id")


def _evaluate_report_entry(root: Path) -> None:
    success = load_success(root, REPORT_ENTRY_ID)
    try:
        locked = ReportSnapshot.model_validate(success.data)
    except ValidationError as exc:
        raise FixtureValidationError(f"report success data is not ReportSnapshot: {exc}") from exc
    if locked.status != "locked" or locked.report_id is not None or locked.card is not None:
        raise FixtureValidationError("report success fixture must be locked with null id/card")
    mapped = load_business_error(root, REPORT_ENTRY_ID)
    if mapped.code not in REPORT_BUSINESS_CODES:
        raise FixtureValidationError(
            f"report business_error must be DEPENDENCY_UNAVAILABLE, got {mapped.code}"
        )
    payload = load_json_fixture(root, REPORT_ENTRY_ID, FixtureKind.MISSING_REQUIRED_FIELD)
    try:
        ReportSnapshot.model_validate(payload)
    except ValidationError as exc:
        missing = _missing_field_names(exc)
        if not missing:
            raise FixtureValidationError(
                "report missing_required_field must omit a required field, not use a wrong type"
            ) from exc
    else:
        raise FixtureValidationError("report missing_required_field unexpectedly validated")
    generating = ReportSnapshot.model_validate(
        load_success_from_scenario(root, REPORT_ENTRY_ID, "generating").data
    )
    if (
        generating.status != "generating"
        or generating.report_id is None
        or generating.card is not None
    ):
        raise FixtureValidationError("generating fixture must have report_id and null card")
    partial = ReportSnapshot.model_validate(
        load_success_from_scenario(root, REPORT_ENTRY_ID, "partial").data
    )
    if (
        partial.status != "partial"
        or partial.card is None
        or partial.card.signature_line is not None
    ):
        raise FixtureValidationError("partial fixture must include a card with null signature_line")
    unavailable = [item for item in partial.card.top_memories if item.unavailable]
    if not unavailable or unavailable[0].type is not None or unavailable[0].summary is not None:
        raise FixtureValidationError(
            "partial fixture must include an unavailable memory without body"
        )
    ready = ReportSnapshot.model_validate(
        load_success_from_scenario(root, REPORT_ENTRY_ID, "ready").data
    )
    if ready.status != "ready" or ready.card is None or ready.card.signature_line is None:
        raise FixtureValidationError("ready fixture must include signature_line")
    failed = ReportSnapshot.model_validate(
        load_success_from_scenario(root, REPORT_ENTRY_ID, "failed").data
    )
    if failed.status != "failed" or failed.report_id is None or failed.card is not None:
        raise FixtureValidationError("failed fixture must have report_id and null card")


def _evaluate_report_line_entry(root: Path) -> None:
    success = load_success(root, REPORT_LINE_ENTRY_ID)
    try:
        result = ReportLineResult.model_validate(success.data)
    except ValidationError as exc:
        raise FixtureValidationError(
            f"report line success data is not ReportLineResult: {exc}"
        ) from exc
    if result.resource.status != "ready" or result.resource.signature_line is None:
        raise FixtureValidationError("report line success must return a ready card")
    if result.patch.report.card is None:
        raise FixtureValidationError("report line patch must include the report snapshot")
    mapped = load_business_error(root, REPORT_LINE_ENTRY_ID)
    if mapped.code not in REPORT_LINE_BUSINESS_CODES:
        raise FixtureValidationError(
            f"report line business_error must be REPORT_LINE_UNAVAILABLE, got {mapped.code}"
        )
    payload = load_json_fixture(root, REPORT_LINE_ENTRY_ID, FixtureKind.MISSING_REQUIRED_FIELD)
    try:
        ReportLineRequest.model_validate(payload)
    except ValidationError as exc:
        missing = _missing_field_names(exc)
        if not missing:
            raise FixtureValidationError(
                "report line missing_required_field must omit a required field"
            ) from exc
    else:
        raise FixtureValidationError("report line missing_required_field unexpectedly validated")
    replay = ReportLineResult.model_validate(
        load_success_from_scenario(root, REPORT_LINE_ENTRY_ID, "idempotent_replay").data
    )
    if replay.resource.signature_line != result.resource.signature_line:
        raise FixtureValidationError("idempotent_replay must keep the same signature_line")
    conflict = parse_business_error(
        load_json_fixture(root, REPORT_LINE_ENTRY_ID, "version_conflict")
    )
    if conflict.code != "CONFLICT":
        raise FixtureValidationError("version_conflict must be CONFLICT")
    exhausted = parse_business_error(load_json_fixture(root, REPORT_LINE_ENTRY_ID, "max_attempts"))
    if exhausted.code != "REPORT_LINE_UNAVAILABLE":
        raise FixtureValidationError("max_attempts must be REPORT_LINE_UNAVAILABLE")
    failed = ReportLineResult.model_validate(
        load_success_from_scenario(root, REPORT_LINE_ENTRY_ID, "provider_fail").data
    )
    if failed.resource.status != "partial" or failed.resource.signature_line is not None:
        raise FixtureValidationError("provider_fail must keep partial with null signature_line")
    if failed.resource.title != result.resource.title:
        raise FixtureValidationError("provider_fail must not change title")


def _evaluate_spirit_patch_entry(root: Path) -> None:
    success = load_success(root, SPIRIT_PATCH_ENTRY_ID)
    try:
        result = SpiritPatchResult.model_validate(success.data)
    except ValidationError as exc:
        raise FixtureValidationError(
            f"spirit patch success data is not SpiritPatchResult: {exc}"
        ) from exc
    if result.patch.preferences.dnd_start != "23:00":
        raise FixtureValidationError("settings success must use HH:MM DND")
    mapped = load_business_error(root, SPIRIT_PATCH_ENTRY_ID)
    if mapped.code not in SPIRIT_PATCH_BUSINESS_CODES:
        raise FixtureValidationError(
            f"spirit patch business_error must be CONFLICT, got {mapped.code}"
        )
    payload = load_json_fixture(root, SPIRIT_PATCH_ENTRY_ID, FixtureKind.MISSING_REQUIRED_FIELD)
    try:
        PatchSpiritRequest.model_validate(payload)
    except ValidationError as exc:
        missing = _missing_field_names(exc)
        if not missing:
            raise FixtureValidationError(
                "spirit patch missing_required_field must omit a required field"
            ) from exc
    else:
        raise FixtureValidationError("spirit patch missing_required_field unexpectedly validated")
    replay = SpiritPatchResult.model_validate(
        load_success_from_scenario(root, SPIRIT_PATCH_ENTRY_ID, "idempotent_replay").data
    )
    if (
        replay.resource.id != result.resource.id
        or replay.resource.version != result.resource.version
    ):
        raise FixtureValidationError("idempotent_replay must return the same spirit version")
    conflict = parse_business_error(
        load_json_fixture(root, SPIRIT_PATCH_ENTRY_ID, "version_conflict")
    )
    if conflict.code != "CONFLICT":
        raise FixtureValidationError("version_conflict must be CONFLICT")
    for scenario in ("illegal_timezone", "illegal_dnd", "name_too_long"):
        illegal = parse_business_error(load_json_fixture(root, SPIRIT_PATCH_ENTRY_ID, scenario))
        if illegal.code != "INVALID_INPUT":
            raise FixtureValidationError(f"{scenario} must be INVALID_INPUT")


def _evaluate_account_delete_entry(root: Path) -> None:
    success = load_success(root, ACCOUNT_DELETE_ENTRY_ID)
    try:
        result = DeletionResult.model_validate(success.data)
    except ValidationError as exc:
        raise FixtureValidationError(
            f"account delete success data is not DeletionResult: {exc}"
        ) from exc
    if result.status != "accepted":
        raise FixtureValidationError("account delete success must be accepted")
    mapped = load_business_error(root, ACCOUNT_DELETE_ENTRY_ID)
    if mapped.code not in ACCOUNT_DELETE_BUSINESS_CODES:
        raise FixtureValidationError(
            f"account delete business_error must be ACCOUNT_DELETE_PENDING, got {mapped.code}"
        )
    payload = load_json_fixture(root, ACCOUNT_DELETE_ENTRY_ID, FixtureKind.MISSING_REQUIRED_FIELD)
    try:
        DeleteAccountRequest.model_validate(payload)
    except ValidationError as exc:
        missing = _missing_field_names(exc)
        if not missing:
            raise FixtureValidationError(
                "account delete missing_required_field must omit a required field"
            ) from exc
    else:
        raise FixtureValidationError("account delete missing_required_field unexpectedly validated")
    replay = DeletionResult.model_validate(
        load_success_from_scenario(root, ACCOUNT_DELETE_ENTRY_ID, "idempotent_replay").data
    )
    if replay.deletion_id != result.deletion_id:
        raise FixtureValidationError("idempotent_replay must return the same deletion_id")
    confirm = parse_business_error(
        load_json_fixture(root, ACCOUNT_DELETE_ENTRY_ID, "confirm_mismatch")
    )
    if confirm.code != "INVALID_INPUT":
        raise FixtureValidationError("confirm_mismatch must be INVALID_INPUT")
    pending = parse_business_error(load_json_fixture(root, ACCOUNT_DELETE_ENTRY_ID, "pending"))
    if pending.code != "ACCOUNT_DELETE_PENDING":
        raise FixtureValidationError("pending must be ACCOUNT_DELETE_PENDING")


def _evaluate_recall_entry(root: Path) -> None:
    success = load_success(root, RECALL_ENTRY_ID)
    try:
        result = RecallMutationResult.model_validate(success.data)
    except ValidationError as exc:
        raise FixtureValidationError(
            f"recall success data is not RecallMutationResult: {exc}"
        ) from exc
    if result.resource.method != "food":
        raise FixtureValidationError("recall success must be food method")
    if result.patch.spirit is None or result.patch.spirit.status != "home":
        raise FixtureValidationError("recall success must return home")
    if result.patch.spirit.hunger != 80:
        raise FixtureValidationError("food recall must not apply ordinary food hunger")
    if not any(item.summary == "你离开过" for item in result.patch.memories_upsert):
        raise FixtureValidationError("recall success must upsert the relation memory")
    if not any(event.type == "recall.returned" for event in result.events):
        raise FixtureValidationError("recall success must emit recall.returned")
    mapped = load_business_error(root, RECALL_ENTRY_ID)
    if mapped.code not in RECALL_BUSINESS_CODES:
        raise FixtureValidationError(
            f"recall business_error must be SPIRIT_NOT_LOST, got {mapped.code}"
        )
    payload = load_json_fixture(root, RECALL_ENTRY_ID, FixtureKind.MISSING_REQUIRED_FIELD)
    try:
        TypeAdapter(RecallRequest).validate_python(payload)
    except ValidationError as exc:
        missing = _missing_field_names(exc)
        if not missing:
            raise FixtureValidationError(
                "recall missing_required_field must omit a required field"
            ) from exc
    else:
        raise FixtureValidationError("recall missing_required_field unexpectedly validated")
    replay = RecallMutationResult.model_validate(
        load_success_from_scenario(root, RECALL_ENTRY_ID, "idempotent_replay").data
    )
    if (
        replay.resource.id != result.resource.id
        or replay.resource.version != result.resource.version
    ):
        raise FixtureValidationError("idempotent_replay must return the same recall resource")
    not_lost = parse_business_error(load_json_fixture(root, RECALL_ENTRY_ID, "not_lost"))
    if not_lost.code != "SPIRIT_NOT_LOST":
        raise FixtureValidationError("not_lost must be SPIRIT_NOT_LOST")
    for scenario in ("sealed_memory", "deleted_memory", "wrong_owner", "non_sight"):
        illegal = parse_business_error(load_json_fixture(root, RECALL_ENTRY_ID, scenario))
        if illegal.code != "RECALL_SOURCE_INVALID":
            raise FixtureValidationError(f"{scenario} must be RECALL_SOURCE_INVALID")
    full = RecallMutationResult.model_validate(
        load_success_from_scenario(root, RECALL_ENTRY_ID, "quota_full_food").data
    )
    if full.patch.spirit is None or full.patch.spirit.status != "home":
        raise FixtureValidationError("quota_full_food must still return home")
    if full.patch.spirit.hunger != 80:
        raise FixtureValidationError("quota_full_food must not apply ordinary food hunger")
    food_quota = next((item for item in full.quotas if item.capability == "food"), None)
    if food_quota is None or food_quota.used < food_quota.limit:
        raise FixtureValidationError("quota_full_food must show a full food quota")


def load_success_from_scenario(root: Path, entry_id: str, scenario: str) -> SuccessFixture:
    payload = load_json_fixture(root, entry_id, scenario)
    return parse_success(payload)


def evaluate_catalog(root: Path, entries: tuple[CatalogEntry, ...]) -> None:
    assert_required_fixtures_exist(root, entries)
    for entry in entries:
        evaluate_entry(root, entry)


def fixture_manifest_from_export(exported: OpenApiExport) -> dict[str, Any]:
    return {
        "api_version": exported.manifest["api_version"],
        "schema_version": exported.manifest["schema_version"],
        "openapi_sha256": exported.sha256,
        "generated_at": exported.manifest["generated_at"],
        "source_commit": exported.manifest["source_commit"],
        "source_app": SOURCE_APP,
        "entries": [
            {
                "id": entry.id,
                "kind": entry.kind,
                "endpoint": entry.endpoint,
                "scenarios": list(entry.scenarios),
            }
            for entry in catalog_for_openapi(exported.document)
        ],
    }


def load_manifest(root: Path) -> dict[str, Any]:
    path = root / "manifest.json"
    if not path.is_file():
        raise FixtureMissingError(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise FixtureManifestError("manifest.json must be a JSON object")
    return raw


def assert_manifest_matches_export(manifest: dict[str, Any], exported: OpenApiExport) -> None:
    required = ("api_version", "schema_version", "openapi_sha256", "generated_at", "source_commit")
    missing = [key for key in required if key not in manifest]
    if missing:
        raise FixtureManifestError(f"manifest missing keys: {', '.join(missing)}")
    if manifest["openapi_sha256"] != exported.sha256:
        raise FixtureManifestError(
            "manifest openapi_sha256 does not match live app export: "
            f"{manifest['openapi_sha256']} != {exported.sha256}"
        )
    if manifest["api_version"] != exported.manifest["api_version"]:
        raise FixtureManifestError("manifest api_version does not match live app")
    if manifest["schema_version"] != exported.manifest["schema_version"]:
        raise FixtureManifestError("manifest schema_version does not match live app")


def write_fixture_manifest(root: Path, exported: OpenApiExport) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "manifest.json"
    payload = fixture_manifest_from_export(exported)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def assert_fixture_is_desensitized(payload: dict[str, Any], path: Path | None = None) -> None:
    label = str(path) if path is not None else "fixture"
    blobs = _walk_strings(payload)
    for key, value in blobs:
        lowered_key = key.lower()
        if any(fragment in lowered_key for fragment in FORBIDDEN_KEY_FRAGMENTS):
            raise FixtureValidationError(f"{label} contains sensitive key {key}")
        lowered_value = value.lower()
        if any(fragment in lowered_value for fragment in FORBIDDEN_SUBSTRINGS):
            raise FixtureValidationError(f"{label} contains a sensitive value")


def _parse_envelope(payload: dict[str, Any]) -> Envelope:
    try:
        return Envelope.model_validate(payload)
    except ValidationError as exc:
        raise FixtureValidationError(f"invalid Envelope: {exc}") from exc


def _missing_field_names(exc: ValidationError) -> list[str]:
    names: list[str] = []
    for error in exc.errors():
        if error.get("type") != "missing":
            continue
        loc = error.get("loc", ())
        names.append(".".join(str(part) for part in loc) if loc else "unknown")
    return names


def _walk_strings(value: Any, key: str = "") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for child_key, child in value.items():
            found.extend(_walk_strings(child, str(child_key)))
        return found
    if isinstance(value, list):
        for child in value:
            found.extend(_walk_strings(child, key))
        return found
    if isinstance(value, str):
        found.append((key, value))
    return found
