from app.domain.evolution import (
    AWAKE_BOND_MIN,
    FORMED_ACTIVE_MEMORY_TYPES,
    FORMED_BOND_MIN,
    SCHOLAR_ROOM_LAYER,
    SCHOLAR_TITLE_SUFFIX,
    STAGE_EVENT_TYPE,
    EvolutionFacts,
    apply_scholar_title_suffix,
    awake_ready,
    evolution_summary_patch,
    formed_ready,
    next_stage,
    pact_scholar_mark_key,
    require_forward_step,
    unique_scholar_marks,
)
from pytest import raises


def _facts(
    *,
    stage: str = "whelp",
    bond: int = 0,
    types: frozenset[str] = frozenset(),
    knowledge: bool = False,
    visited: bool = False,
    lost: bool = False,
    marks: tuple[str, ...] = (),
) -> EvolutionFacts:
    return EvolutionFacts(
        stage=stage,
        bond=bond,
        active_memory_types=types,
        has_knowledge_or_pact_growth=knowledge,
        has_visited=visited,
        has_been_lost=lost,
        scholar_marks=marks,
    )


def test_formed_needs_bond_20_and_three_active_types() -> None:
    two = _facts(bond=FORMED_BOND_MIN, types=frozenset({"preference", "knowledge"}))
    assert formed_ready(two) is False
    assert next_stage(two) is None
    low_bond = _facts(
        bond=FORMED_BOND_MIN - 1,
        types=frozenset({"preference", "knowledge", "sight"}),
    )
    assert formed_ready(low_bond) is False
    ready = _facts(
        bond=FORMED_BOND_MIN,
        types=frozenset({"preference", "knowledge", "sight"}),
    )
    assert formed_ready(ready) is True
    assert next_stage(ready) == "formed"
    assert FORMED_ACTIVE_MEMORY_TYPES == 3


def test_awake_needs_bond_knowledge_and_visit_or_lost() -> None:
    formed = _facts(
        stage="formed",
        bond=AWAKE_BOND_MIN,
        types=frozenset({"preference", "knowledge", "sight"}),
        knowledge=True,
    )
    assert awake_ready(formed) is False
    assert next_stage(formed) is None
    no_knowledge = _facts(
        stage="formed",
        bond=AWAKE_BOND_MIN,
        knowledge=False,
        lost=True,
    )
    assert next_stage(no_knowledge) is None
    visited = _facts(stage="formed", bond=AWAKE_BOND_MIN, knowledge=True, visited=True)
    assert next_stage(visited) == "awake"
    lost = _facts(stage="formed", bond=AWAKE_BOND_MIN, knowledge=True, lost=True)
    assert next_stage(lost) == "awake"


def test_whelp_cannot_skip_to_awake() -> None:
    facts = _facts(
        stage="whelp",
        bond=AWAKE_BOND_MIN,
        types=frozenset({"preference", "knowledge", "sight"}),
        knowledge=True,
        lost=True,
    )
    assert next_stage(facts) == "formed"
    with raises(ValueError, match="one step"):
        require_forward_step("whelp", "awake")
    with raises(ValueError, match="one step"):
        require_forward_step("formed", "whelp")
    with raises(ValueError, match="one step"):
        require_forward_step("awake", "formed")


def test_awake_does_not_regress() -> None:
    facts = _facts(stage="awake", bond=0)
    assert next_stage(facts) is None


def test_scholar_mark_is_stable_and_unique() -> None:
    assert pact_scholar_mark_key(theme="interview", question_bank_version="v1") == "interview-v1"
    assert unique_scholar_marks(("interview-v1", "interview-v1", "notes-v1")) == (
        "interview-v1",
        "notes-v1",
    )
    with raises(ValueError, match="stable"):
        unique_scholar_marks(("Interview V1",))
    with raises(ValueError, match="theme"):
        pact_scholar_mark_key(theme="toy", question_bank_version="v1")


def test_room_and_report_summary_patch() -> None:
    empty = evolution_summary_patch(_facts())
    assert empty.room_layers_include_scholar is False
    assert empty.report_title_suffix is None
    marked = evolution_summary_patch(_facts(marks=("interview-v1",)))
    assert marked.room_layers_include_scholar is True
    assert marked.report_title_suffix == SCHOLAR_TITLE_SUFFIX
    assert SCHOLAR_ROOM_LAYER == "scholar"
    assert apply_scholar_title_suffix("阴天收集者", ("interview-v1",)) == "阴天收集者 · 学者"
    assert apply_scholar_title_suffix("阴天收集者 · 学者", ("interview-v1",)) == "阴天收集者 · 学者"
    assert apply_scholar_title_suffix("阴天收集者", ()) == "阴天收集者"
    assert STAGE_EVENT_TYPE == "stage.changed"
