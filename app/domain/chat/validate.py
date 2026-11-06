"""Reply gate. Empty or unsafe replies still fall back. No table writes."""

from __future__ import annotations

from app.domain.chat.live_facts import public_reply_text
from app.domain.chat.types import GateResult, ModelReply, Overlay, RecallItem, TurnPlan

_SAFETY_TEXT = "先保证安全。如果有危险，联系身边的人或当地紧急帮助。"


def validate_reply(
    plan: TurnPlan,
    reply: ModelReply,
    *,
    overlay: Overlay | None = None,
    whitelist: tuple[RecallItem, ...] = (),
    outside_facts: tuple[str, ...] = (),
) -> GateResult:
    facts = overlay or Overlay()
    text = public_reply_text(reply.text) or ""
    if not text or not reply.intent:
        return _fail(plan, facts, "structure")
    if reply.safety == "reject":
        return _fail(plan, facts, "safety_rejected", force_safety=True)
    if _outside(text, reply.citation_ids, whitelist, outside_facts):
        return _fail(plan, facts, "outside_fact")
    warning = None
    if (
        facts.address
        and plan.traits.closeness == "high"
        and facts.address not in text
    ):
        warning = "address_unused"
    return GateResult(ok=True, code=None, text=text, degraded=False, warning=warning)


def fallback_text(
    plan: TurnPlan,
    overlay: Overlay,
    *,
    force_safety: bool = False,
) -> str:
    if force_safety or plan.mode == "safety":
        return _SAFETY_TEXT
    if plan.mode == "recall_only":
        return "先把我叫回去。给我口粮。"
    if plan.mode == "away_note":
        return "我出去了。串门。"
    if plan.mode == "knowledge":
        return "这次没找可靠依据，不敢乱讲。"
    if overlay.address:
        return f"我在，{overlay.address}。"
    return "我在。"


def _fail(
    plan: TurnPlan,
    overlay: Overlay,
    code: str,
    *,
    force_safety: bool = False,
) -> GateResult:
    return GateResult(
        ok=False,
        code=code,
        text=fallback_text(plan, overlay, force_safety=force_safety),
        degraded=True,
    )


def _outside(
    text: str,
    citation_ids: tuple[str, ...],
    whitelist: tuple[RecallItem, ...],
    outside_facts: tuple[str, ...],
) -> bool:
    allowed = {item.id for item in whitelist if item.active}
    illegal = [item for item in citation_ids if item not in allowed]
    if not illegal and not outside_facts:
        return False
    return any(fact and fact in text for fact in outside_facts)
