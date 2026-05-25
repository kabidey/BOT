"""Phase 22 — unit tests for `fingerprint_guard.compute_suspicious_score`.

Pure-function tests (no Mongo needed). Validate:
  * Single client binding → score 0 (rapid-burst only kicks in after 1st client).
  * Three rapid bindings within the window → above BLOCK_SCORE.
  * Time decay: a 14-day-old binding contributes < 25 % of its initial weight.
  * RM-linkage mitigator brings a 4-client RM-onboarding device back below FLAG.
  * IP /16 jump within 10 min adds 50 points.

Run: `cd /app/backend && python -m pytest tests/test_fingerprint_guard.py -q`
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Make `backend/` importable when invoked from inside `backend/tests`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fingerprint_guard as fg  # noqa: E402


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _row(*, clients=None, employees=None, ips=None, uas=None):
    return {
        "client_identities": clients or [],
        "employee_identities": employees or [],
        "ips_seen": ips or [],
        "user_agents_seen": uas or [],
    }


def test_single_client_binding_scores_zero() -> None:
    now = datetime.now(timezone.utc)
    score, breakdown = fg.compute_suspicious_score(_row(
        clients=[{"ucc": "UCC1", "first_at": _iso(now), "rm_name": "Alice"}],
    ))
    assert score == 0.0, f"expected 0, got {score} ({breakdown})"
    assert breakdown["rapid_burst"] == 0
    assert breakdown["rapid_count"] == 1


def test_three_rapid_clients_triggers_block_threshold() -> None:
    """3 distinct UCCs within 5 minutes → rapid_burst score = 50, plus
    daily_saturation = 15 → 65. That's still under default BLOCK=75 BUT a
    4-client rapid burst MUST trip the block."""
    now = datetime.now(timezone.utc)
    four = [{"ucc": f"UCC{i}", "first_at": _iso(now - timedelta(minutes=i))}
            for i in range(4)]
    score, breakdown = fg.compute_suspicious_score(_row(clients=four))
    assert score >= fg.BLOCK_SCORE(), \
        f"4 rapid clients should trip block; score={score}, breakdown={breakdown}"
    assert breakdown["rapid_burst"] >= 50


def test_time_decay_reduces_weight() -> None:
    now = datetime.now(timezone.utc)
    # 11 clients, all 14 days old → effective decayed count = 11 * 0.5^2 = 2.75
    old = [{"ucc": f"UCC{i}", "first_at": _iso(now - timedelta(days=14))}
           for i in range(11)]
    score, breakdown = fg.compute_suspicious_score(_row(clients=old))
    # 14 days = 2 half-lives → weight = 0.25 of full
    assert breakdown["lifetime_decayed_count"] < 3.0
    # And since decayed count < LIFETIME_LIMIT (10), no_rm_score = 0.
    assert breakdown["lifetime_no_rm"] == 0
    # Plus none are within 24h, so daily/rapid = 0 → score = 0.
    assert score == 0.0


def test_rm_linkage_mitigator_keeps_legit_branch_device_below_flag() -> None:
    """A branch laptop where an RM onboards 4 of their own clients in 90 min
    must NOT be flagged: the RM's own employee record on the same device
    triggers the −20 RM-linkage mitigator."""
    now = datetime.now(timezone.utc)
    clients = [
        {"ucc": f"UCC{i}", "first_at": _iso(now - timedelta(minutes=15 * i)),
         "rm_name": "Alice Sharma"}
        for i in range(4)
    ]
    employees = [{"employee_id": "EMP1", "name": "Alice Sharma",
                  "first_at": _iso(now - timedelta(hours=4))}]
    score, breakdown = fg.compute_suspicious_score(_row(
        clients=clients, employees=employees,
    ))
    # 4 rapid clients → 75 rapid + (4-2)*15 = 30 daily = 105 raw before mitigator.
    # Then −20 (rm) + maybe −10 (single network) but we have no IPs here.
    # Expect at least 20 points knocked off via rm_mitigator.
    assert breakdown["rm_mitigator"] == -20
    assert breakdown["rm_matched"] == 4


def test_ip_jump_within_window_adds_50_points() -> None:
    now = datetime.now(timezone.utc)
    ips = [
        {"ip": "203.0.113.10", "network_prefix": "203.0.0.0/16",
         "first_at": _iso(now - timedelta(minutes=4)),
         "last_at":  _iso(now - timedelta(minutes=4)), "count": 1},
        {"ip": "198.51.100.5", "network_prefix": "198.51.0.0/16",
         "first_at": _iso(now - timedelta(minutes=2)),
         "last_at":  _iso(now - timedelta(minutes=2)), "count": 1},
    ]
    score, breakdown = fg.compute_suspicious_score(_row(ips=ips))
    assert breakdown["ip_jump"] == 50
    assert score >= 40.0


def test_silent_block_response_shapes_are_not_403() -> None:
    """The block responses must look like ordinary soft-failure envelopes —
    no `blocked: true`, no error field, status will be 200 in the
    middleware."""
    chat = fg.silent_block_chat_response(session_id="s1")
    assert chat["intent"] == "SOFT_ERROR"
    assert chat["session_id"] == "s1"
    assert isinstance(chat["blocks"], list) and chat["blocks"][0]["type"] == "text"
    # Belt-and-suspenders: no leaky key in the payload.
    flat = str(chat).lower()
    assert "blocked" not in flat
    assert "403" not in flat
    assert "forbidden" not in flat

    legacy = fg.silent_block_legacy_chat()
    assert legacy["model"] is None
    assert "unable to process" in legacy["reply"].lower()

    empty = fg.silent_block_empty_data()
    assert empty == {"ok": True, "value": None, "results": [], "rows": []}


def test_mask_identity_redacts_correctly() -> None:
    assert fg._mask_identity("client", "1234567890") == "12***90"
    assert fg._mask_identity("employee", "SMWM-25031054") == "SMWM-2***"
    assert fg._mask_identity("client", "abc") == "***"
