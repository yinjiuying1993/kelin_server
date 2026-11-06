"""S2-T03 central facade idempotency decisions. No IO."""

from __future__ import annotations

from app.core.errors import ApiError
from app.repositories.central.idempotency import CentralIdempotencyClaim
from app.services.central.idempotency import decide_claim
from pytest import raises


def _claim(
    *,
    inserted: bool,
    request_hash: str = "aa",
    status: str = "in_progress",
    response_json: dict[str, object] | None = None,
) -> CentralIdempotencyClaim:
    return CentralIdempotencyClaim(
        inserted=inserted,
        request_hash=request_hash,
        status=status,
        response_json=response_json,
    )


def test_fresh_insert_proceeds_even_while_in_progress() -> None:
    decision, stored = decide_claim(_claim(inserted=True), "aa")
    assert decision == "proceed"
    assert stored is None


def test_completed_same_hash_replays_payload() -> None:
    payload = {"snapshot_version": 4, "payload": {"ok": True}}
    decision, stored = decide_claim(
        _claim(inserted=False, status="completed", response_json=payload),
        "aa",
    )
    assert decision == "replay"
    assert stored == payload


def test_completed_different_hash_conflicts() -> None:
    with raises(ApiError) as exc:
        decide_claim(
            _claim(
                inserted=False,
                status="completed",
                response_json={"snapshot_version": 2, "payload": {}},
            ),
            "bb",
        )
    assert exc.value.code == "IDEMPOTENCY_CONFLICT"
    assert exc.value.status_code == 409
    assert exc.value.retryable is False


def test_in_progress_row_is_retryable() -> None:
    with raises(ApiError) as exc:
        decide_claim(_claim(inserted=False, status="in_progress"), "aa")
    assert exc.value.code == "IDEMPOTENCY_IN_PROGRESS"
    assert exc.value.retryable is True
