from __future__ import annotations

import inspect
from uuid import uuid4

import pytest
from app.domain.onboarding import (
    complete_onboarding_request_hash,
    onboarding_ready_to_hatch,
)
from app.schemas.onboarding import CompleteOnboardingRequest
from app.services.onboarding import complete_onboarding
from pydantic import ValidationError


def test_hatch_requires_step_five_and_five_complete_rounds() -> None:
    assert onboarding_ready_to_hatch(step=5, complete_rounds=5) is True
    assert onboarding_ready_to_hatch(step=5, complete_rounds=6) is True
    assert onboarding_ready_to_hatch(step=5, complete_rounds=4) is False
    assert onboarding_ready_to_hatch(step=4, complete_rounds=5) is False
    assert onboarding_ready_to_hatch(step=4, complete_rounds=4) is False


def test_complete_hash_ignores_client_id_and_rejects_user_id() -> None:
    first = CompleteOnboardingRequest.model_validate(
        {"client_id": str(uuid4()), "expected_spirit_version": 6}
    )
    second = CompleteOnboardingRequest.model_validate(
        {"client_id": str(uuid4()), "expected_spirit_version": 6}
    )
    assert complete_onboarding_request_hash(first) == complete_onboarding_request_hash(second)
    payload = first.model_dump(mode="json")
    payload["user_id"] = str(uuid4())
    with pytest.raises(ValidationError):
        CompleteOnboardingRequest.model_validate(payload)
    assert "user_id" not in inspect.signature(complete_onboarding).parameters
