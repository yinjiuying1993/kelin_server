"""S5-T03: extract window decision is IO-free; chat reply has no extract body."""

from __future__ import annotations

import ast
from pathlib import Path

from app.domain.central.consumers import consumers_for_event
from app.domain.central.pipeline import CHAT_PIPELINE
from app.domain.chat import ChatTurnSettlement
from app.domain.memory import EXTRACT_ROUNDS
from app.services.memory import extract_task_decision

ROOT = Path(__file__).resolve().parents[2]
MEMORY_SERVICE = ROOT / "app" / "services" / "memory"
CHAT_SERVICE = ROOT / "app" / "services" / "chat.py"
CRON = ROOT / "app" / "services" / "cron.py"


def _imported(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    return tuple(found)


def test_extract_rounds_is_three() -> None:
    assert EXTRACT_ROUNDS == 3
    assert extract_task_decision(onboarding=False, status="open", user_round_count=1) == "skip"
    assert extract_task_decision(onboarding=False, status="open", user_round_count=2) == "skip"
    assert (
        extract_task_decision(onboarding=False, status="open", user_round_count=3)
        == "seal_and_enqueue"
    )
    assert extract_task_decision(onboarding=False, status="ready", user_round_count=3) == "enqueue"
    assert extract_task_decision(onboarding=False, status="failed", user_round_count=3) == "enqueue"
    assert extract_task_decision(
        onboarding=False, status="extracted", user_round_count=3
    ) == "skip"
    assert extract_task_decision(
        onboarding=False, status="extracting", user_round_count=3
    ) == "skip"
    assert extract_task_decision(onboarding=True, status="open", user_round_count=3) == "skip"


def test_chat_pipeline_windows_after_persist() -> None:
    assert CHAT_PIPELINE.index("persist_reply") < CHAT_PIPELINE.index(
        "publish_dialogue_completed"
    )
    assert CHAT_PIPELINE.index("publish_dialogue_completed") < CHAT_PIPELINE.index(
        "memory_window"
    )
    assert "memory" in consumers_for_event("dialogue.completed")


def test_chat_settlement_omits_extract_memories() -> None:
    names = set(ChatTurnSettlement.__dataclass_fields__)
    assert "memories" not in names
    assert "style_samples" not in names
    chat_src = CHAT_SERVICE.read_text(encoding="utf-8")
    assert "process_due_extracts" not in chat_src
    assert "adapter.extract" not in chat_src


def test_window_service_does_not_call_model_or_chat() -> None:
    window = MEMORY_SERVICE / "window_service.py"
    imported = _imported(window)
    assert "app.services.chat" not in imported
    assert "app.services.extract" not in imported
    assert "app.providers" not in imported
    assert "extract(" not in window.read_text(encoding="utf-8")


def test_extract_worker_is_async_and_wired() -> None:
    worker_src = (MEMORY_SERVICE / "extract_worker.py").read_text(encoding="utf-8")
    assert "claimed_transaction" in worker_src
    assert "adapter.extract" in worker_src
    assert "app.services.chat" not in worker_src
    assert "settle_extract" not in worker_src
    cron = CRON.read_text(encoding="utf-8")
    assert "process_due_extracts" in cron
    assert "app.services.memory.extract_worker" in cron
