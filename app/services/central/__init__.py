"""Central facade entry. Routers must not settle outside this module."""

from app.services.central.dialogue import publish_dialogue_completed
from app.services.central.facade import FacadeResult, SettlementOutcome, execute
from app.services.central.memory_control import MemoryControlResult, execute_memory_control
from app.services.central.pipelines import (
    PipelineKindMismatch,
    bind_pipeline,
    run_bootstrap,
    run_chat,
    run_memory_control,
    run_user_action,
)
from app.services.central.user_action import publish_user_action
from app.services.central.visit_completed import publish_visit_completed

__all__ = [
    "FacadeResult",
    "MemoryControlResult",
    "PipelineKindMismatch",
    "SettlementOutcome",
    "bind_pipeline",
    "execute",
    "execute_memory_control",
    "publish_dialogue_completed",
    "publish_user_action",
    "publish_visit_completed",
    "run_bootstrap",
    "run_chat",
    "run_memory_control",
    "run_user_action",
]
