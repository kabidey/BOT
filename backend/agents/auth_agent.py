"""Auth Agent — in-chat client verification via 2 personalized questions.

State machine:
  anonymous ──[code]──▶ awaiting_q1 ──[correct]──▶ awaiting_q2 ──[correct]──▶ verified
                              ↳[wrong×3]──▶ locked (15 min)
                              ↳[wrong]──▶ stays in same state, attempts++

The Auth Agent runs BEFORE the Router whenever:
  - Session is mid-verification (awaiting_q1/q2)
  - User message matches a client code pattern (SMIFS\\d+ or 10+-digit phone)
  - Router subsequently classified intent=CLIENT_LOOKUP (handled by orchestrator)
"""
from __future__ import annotations
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from . import api_agent

logger = logging.getLogger(__name__)

MAX_FAILED_ATTEMPTS = 3
LOCKOUT_MINUTES = 15


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _norm_answer(s: str) -> str:
    return (s or "").strip().lower()


# ---------- session row helpers ----------
async def get_or_create_session_row(db, session_id: str) -> Dict[str, Any]:
    """Return the session row, creating an anonymous one if missing.
    Also auto-resets a locked session whose lockout has expired."""
    row = await db.sessions.find_one({"_id": session_id})
    if row is None:
        now_dt = _now()
        now_iso = now_dt.isoformat()
        row = {
            "_id": session_id,
            "session_id": session_id,
            "client_code": None,
            "auth_state": "anonymous",
            "failed_attempts": 0,
            "pending_question_index": None,
            "verified_at": None,
            "locked_until": None,
            "created_at": now_iso,
            "updated_at": now_iso,
            "updated_at_dt": now_dt,
        }
        await db.sessions.insert_one(row)
        return row

    # Auto-clear expired lockouts
    if row.get("auth_state") == "locked" and row.get("locked_until"):
        try:
            until = datetime.fromisoformat(row["locked_until"])
            if _now() >= until:
                await _update(db, session_id, auth_state="anonymous", failed_attempts=0,
                              pending_question_index=None, locked_until=None, client_code=None)
                row["auth_state"] = "anonymous"
                row["failed_attempts"] = 0
                row["pending_question_index"] = None
                row["locked_until"] = None
                row["client_code"] = None
        except Exception:
            pass
    return row


async def _update(db, session_id: str, **fields) -> None:
    now_dt = _now()
    fields["updated_at"] = now_dt.isoformat()
    fields["updated_at_dt"] = now_dt  # real ISODate for TTL
    await db.sessions.update_one({"_id": session_id}, {"$set": fields})


async def signout(db, session_id: str) -> Dict[str, Any]:
    """Reset auth state to anonymous; idempotent — creates the row if missing."""
    row = await db.sessions.find_one({"_id": session_id})
    if row is None:
        # Insert an anonymous row so the response is consistent
        await get_or_create_session_row(db, session_id)
    await _update(
        db, session_id,
        client_code=None,
        auth_state="anonymous",
        failed_attempts=0,
        pending_question_index=None,
        verified_at=None,
        locked_until=None,
    )
    return await db.sessions.find_one({"_id": session_id}) or {"auth_state": "anonymous"}


# ---------- branches consumed by the orchestrator ----------
async def begin_verification(db, session_id: str, identifier: str) -> Dict[str, Any]:
    """Look up the client and start at q1, OR return a not-found message that keeps
    the session anonymous (no lockout penalty)."""
    res = await api_agent.lookup_client_with_questions(db, identifier)
    if not res.get("found"):
        # No lockout for unknown codes — just polite redirect.
        return {
            "blocks": [{
                "type": "text",
                "text": (
                    f"I couldn't find an SMIFS account matching '{identifier}'. "
                    "Please double-check the code or phone, or I can continue helping you as a prospect."
                ),
            }],
            "citations": [],
            "model": None,
        }
    code = res["code"]
    questions = res.get("verify_questions", [])
    await _update(
        db, session_id,
        client_code=code,
        auth_state="awaiting_q1",
        failed_attempts=0,
        pending_question_index=0,
    )
    q = questions[0]["q"] if questions else "Year of birth"
    return {
        "blocks": [{
            "type": "text",
            "text": (
                f"I found your record on file. For your security, I'll need to verify your identity with two short questions.\n\n"
                f"**1 of 2 — {q}?**"
            ),
        }],
        "citations": [],
        "model": None,
    }


async def handle_answer(db, session_id: str, row: Dict[str, Any], user_text: str) -> Dict[str, Any]:
    """Process the user's response when we are mid-verification."""
    state = row["auth_state"]
    code = row.get("client_code")
    if not code:
        # Defensive — shouldn't happen
        await _update(db, session_id, auth_state="anonymous", failed_attempts=0, pending_question_index=None)
        return {
            "blocks": [{"type": "text", "text": "Verification context was lost. Please share your client code again to restart."}],
            "citations": [],
            "model": None,
        }

    client = await api_agent.lookup_client_with_questions(db, code)
    questions = client.get("verify_questions", [])
    idx = row.get("pending_question_index", 0) or 0
    if idx >= len(questions):
        # Edge case — finalise as verified
        return await _finalise_verified(db, session_id, client)

    expected = _norm_answer(questions[idx].get("a", ""))
    given = _norm_answer(user_text)
    matched = bool(expected) and (expected == given or expected in given or given in expected)

    if matched:
        # Advance
        if state == "awaiting_q1":
            next_q = questions[1]["q"] if len(questions) > 1 else None
            if next_q is None:
                return await _finalise_verified(db, session_id, client)
            await _update(db, session_id, auth_state="awaiting_q2", failed_attempts=0, pending_question_index=1)
            return {
                "blocks": [{
                    "type": "text",
                    "text": f"Thank you. **2 of 2 — {next_q}?**",
                }],
                "citations": [],
                "model": None,
            }
        # awaiting_q2 → verified
        return await _finalise_verified(db, session_id, client)

    # Mismatch
    attempts = (row.get("failed_attempts") or 0) + 1
    if attempts >= MAX_FAILED_ATTEMPTS:
        until = _now() + timedelta(minutes=LOCKOUT_MINUTES)
        await _update(
            db, session_id,
            auth_state="locked",
            failed_attempts=attempts,
            locked_until=until.isoformat(),
            pending_question_index=None,
        )
        return {
            "blocks": [
                {
                    "type": "text",
                    "text": (
                        f"For security, verification has been temporarily locked after {MAX_FAILED_ATTEMPTS} unsuccessful attempts. "
                        f"Please try again in {LOCKOUT_MINUTES} minutes, or speak directly with our advisory desk."
                    ),
                },
                {"type": "escalation_card", "data": {"reason": "verification_locked"}},
            ],
            "citations": [],
            "model": None,
        }

    await _update(db, session_id, failed_attempts=attempts)
    current_q = questions[idx]["q"]
    return {
        "blocks": [{
            "type": "text",
            "text": (
                f"That doesn't match our records. Please try again — **{current_q}?** "
                f"({attempts}/{MAX_FAILED_ATTEMPTS} attempts used)"
            ),
        }],
        "citations": [],
        "model": None,
    }


async def locked_response() -> Dict[str, Any]:
    return {
        "blocks": [
            {
                "type": "text",
                "text": (
                    "Verification is locked for security. Please try again in a few minutes "
                    "or call our advisory desk to speak with a human."
                ),
            },
            {"type": "escalation_card", "data": {"reason": "verification_locked"}},
        ],
        "citations": [],
        "model": None,
    }


async def _finalise_verified(db, session_id: str, client: Dict[str, Any]) -> Dict[str, Any]:
    now_iso = _now().isoformat()
    await _update(
        db, session_id,
        auth_state="verified",
        failed_attempts=0,
        pending_question_index=None,
        verified_at=now_iso,
    )
    name = client.get("name", "there")
    first = name.split()[0] if name else "there"
    return {
        "blocks": [
            {
                "type": "text",
                "text": (
                    f"Thank you, {first} — your identity is verified. "
                    "Here is your account at a glance. How can I help you today?"
                ),
            },
            {
                "type": "client_card",
                "data": {
                    "code": client.get("code"),
                    "name": client.get("name"),
                    "holdings_summary": client.get("holdings_summary"),
                    "verified": True,
                },
            },
        ],
        "citations": [],
        "model": None,
    }


# ---------- prompt-injection helper for downstream LLM branches ----------
async def get_verified_client(db, session_id: str) -> Optional[Dict[str, Any]]:
    """Returns {code, name, holdings_summary} if the session is currently verified, else None."""
    row = await db.sessions.find_one({"_id": session_id}, {"_id": 0})
    if not row or row.get("auth_state") != "verified" or not row.get("client_code"):
        return None
    client = await api_agent.lookup_client(db, row["client_code"])
    if not client.get("found"):
        return None
    return {
        "code": client.get("code"),
        "name": client.get("name"),
        "holdings_summary": client.get("holdings_summary"),
    }


def client_context_block(client: Dict[str, Any]) -> str:
    """The string injected into the system prompt of LLM-using branches when verified."""
    first = (client.get("name") or "").split()[0] or "the client"
    return (
        "\n\n--- VERIFIED CLIENT CONTEXT ---\n"
        f"Name: {client.get('name')}\n"
        f"First name: {first}\n"
        f"Code: {client.get('code')}\n"
        f"Holdings summary: {client.get('holdings_summary')}\n"
        f"PERSONALIZATION RULE: Open every reply with the client's first name as a salutation "
        f"(e.g. start with '{first},'). Use this context whenever the client asks about their "
        f"portfolio, holdings, or personalised recommendations. Do NOT invent additional holdings "
        f"or numbers beyond the summary; for specifics outside the summary, offer to involve a human advisor."
        "\n--- END CLIENT CONTEXT ---"
    )
