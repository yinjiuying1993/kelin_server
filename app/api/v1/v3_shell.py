"""V3 module shells. Hidden from OpenAPI until the contract is reviewed."""

from __future__ import annotations

from fastapi import APIRouter

from app.services.ask import reject as reject_ask
from app.services.craft import reject as reject_craft
from app.services.craft_ledger import reject as reject_ledger
from app.services.craft_settle import reject as reject_settle
from app.services.match_index import reject as reject_index

router = APIRouter(prefix="/api/v1/v3", tags=["v3"], include_in_schema=False)


@router.get("/craft")
def craft_shell() -> None:
    reject_craft()


@router.get("/match-index")
def match_index_shell() -> None:
    reject_index()


@router.get("/ask")
def ask_shell() -> None:
    reject_ask()


@router.get("/craft-ledger")
def craft_ledger_shell() -> None:
    reject_ledger()


@router.get("/craft-settle")
def craft_settle_shell() -> None:
    reject_settle()
