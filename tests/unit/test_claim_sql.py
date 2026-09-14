from __future__ import annotations

from app.db.session import INJECT_CLAIM_SQL, READ_SNAPSHOT_SQL


def test_claim_injection_is_transaction_local() -> None:
    assert "set_config('request.jwt.claim.sub', :sub, true)" in INJECT_CLAIM_SQL
    assert "set_config('request.jwt.claim.role', :role, true)" in INJECT_CLAIM_SQL
    assert "false)" not in INJECT_CLAIM_SQL
    assert INJECT_CLAIM_SQL.count(", true)") == 2
    assert READ_SNAPSHOT_SQL.startswith("SET TRANSACTION")
    assert "REPEATABLE READ" in READ_SNAPSHOT_SQL
    assert "READ ONLY" in READ_SNAPSHOT_SQL
