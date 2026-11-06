"""S10-T02: legacy services do not UPDATE growth, emotion, or memory tables."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICES = (
    "extract.py",
    "recall.py",
    "visit_settle.py",
    "spirit_state.py",
)
_FORBIDDEN = (
    "UPDATE public.memories",
    "INSERT INTO public.memories",
    "UPDATE public.spirit_growth",
    "INSERT INTO public.spirit_growth",
    "UPDATE public.spirit_emotion",
    "INSERT INTO public.spirit_emotion",
    "INSERT INTO public.growth_events",
    "UPDATE public.growth_events",
    "record_and_apply",
    "apply_growth_to_spirit",
    "apply_spirit_deltas",
    ".advance_stage(",
    ".append_scholar_mark(",
    "UPDATE public.spirits",
)


def test_legacy_services_do_not_write_growth_emotion_or_memory() -> None:
    for name in SERVICES:
        source = (ROOT / "app" / "services" / name).read_text(encoding="utf-8")
        for snippet in _FORBIDDEN:
            assert snippet not in source, f"{name} still contains {snippet}"
