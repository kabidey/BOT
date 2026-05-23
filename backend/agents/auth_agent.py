"""Phase 6 Auth Agent — real-identity verification via OrgLens API.

Three session types: visitor (default), employee, client.

State machine
=============
                    ┌─────────────────┐
                    │   anonymous     │  (session_type=visitor — no auth challenge)
                    └────────┬────────┘
              role intent /  │   role intent / verify request
              identifier  in │
                  message    ▼
                ┌──────────────────────┐
                │   awaiting_role      │  ← "verify me" w/o role hint
                └──────┬───────────────┘
            picks      │
            employee   │   picks client
                       ▼
                ┌──────────────────────┐
                │  awaiting_identifier │  (session_type set provisionally)
                └──────┬───────────────┘
       email/UCC + 200 │   404 → friendly retry, no lock
                       ▼
                ┌──────────────────────┐
                │   awaiting_pan       │  (sanitized record cached, expected_pan_hash set)
                └──────┬───────────────┘
            wrong x<3  │   match → verified
            wrong x3   │
                       ▼
                ┌──────────────────────┐
                │      locked          │  (15 min, then auto-clear → anonymous)
                └──────────────────────┘

Privacy
=======
* Plaintext PAN is never persisted. Only `expected_pan_hash` (HMAC-SHA256) is
  stored on the session row, computed from the OrgLens record's `pan` /
  `pan_number` field at lookup time. The user's PAN is hashed in-memory and
  compared.
* The cached `pending_record` excludes PAN/Aadhaar/bank/full-address/etc. —
  see identity.sanitize_*_for_storage.
"""
from __future__ import annotations
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from pymongo import ReturnDocument

import identity

logger = logging.getLogger(__name__)

MAX_FAILED_ATTEMPTS = 3
LOCKOUT_MINUTES = 15

# Auth states
ANON = "anonymous"
AWAIT_ROLE = "awaiting_role"
AWAIT_IDENT = "awaiting_identifier"
AWAIT_PAN = "awaiting_pan"
VERIFIED = "verified"
LOCKED = "locked"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------- session row helpers ----------------
async def get_or_create_session_row(db, session_id: str) -> Dict[str, Any]:
    now_dt = _now()
    now_iso = now_dt.isoformat()
    seed = {
        "_id": session_id,
        "session_id": session_id,
        "session_type": "visitor",
        "auth_state": AWAIT_ROLE,
        "pending_session_type": None,
        "pending_identifier": None,
        "pending_record": None,
        "expected_pan_hash": None,
        "identity": None,
        "failed_attempts": 0,
        "verified_at": None,
        "locked_until": None,
        "pan_hash": None,
        "consent_to_ingest": False,
        "created_at": now_iso,
        "updated_at": now_iso,
        "updated_at_dt": now_dt,
    }
    row = await db.sessions.find_one_and_update(
        {"_id": session_id},
        {"$setOnInsert": seed},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    if row.get("auth_state") == LOCKED and row.get("locked_until"):
        try:
            until = datetime.fromisoformat(row["locked_until"])
            if _now() >= until:
                row = await _atomic_set(
                    db, session_id,
                    auth_state=ANON, failed_attempts=0,
                    pending_session_type=None, pending_identifier=None,
                    pending_record=None, expected_pan_hash=None,
                    locked_until=None,
                )
        except Exception:
            pass
    return row


async def _atomic_set(db, session_id: str, **fields) -> Dict[str, Any]:
    now_dt = _now()
    fields["updated_at"] = now_dt.isoformat()
    fields["updated_at_dt"] = now_dt
    return await db.sessions.find_one_and_update(
        {"_id": session_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )


async def _atomic_increment_attempts(db, session_id: str) -> Dict[str, Any]:
    now_dt = _now()
    return await db.sessions.find_one_and_update(
        {"_id": session_id},
        {"$inc": {"failed_attempts": 1},
         "$set": {"updated_at": now_dt.isoformat(), "updated_at_dt": now_dt}},
        return_document=ReturnDocument.AFTER,
    )


async def signout(db, session_id: str) -> Dict[str, Any]:
    await get_or_create_session_row(db, session_id)
    row = await _atomic_set(
        db, session_id,
        session_type="visitor", auth_state=ANON,
        pending_session_type=None, pending_identifier=None,
        pending_record=None, expected_pan_hash=None,
        identity=None, verified_at=None, failed_attempts=0,
        locked_until=None, pan_hash=None,
    )
    return row or {"auth_state": ANON, "session_type": "visitor"}


# ---------------- branches consumed by the orchestrator ----------------
async def start_role_inquiry(db, session_id: str) -> Dict[str, Any]:
    await _atomic_set(db, session_id, auth_state=AWAIT_ROLE,
                      pending_session_type=None, pending_identifier=None,
                      pending_record=None, expected_pan_hash=None,
                      failed_attempts=0)
    return {
        "blocks": [{"type": "text", "text": (
            "Happy to help with that. Are you reaching out as a "
            "**Mackertich ONE client** or as an **SMIFS employee**?"
        )}],
        "citations": [], "model": None,
    }


async def handle_role_response(db, session_id: str, message: str) -> Dict[str, Any]:
    role = identity.detect_role_intent(message)
    msg_lower = (message or "").strip().lower()
    if role == "employee" or msg_lower.startswith("employee") or msg_lower.startswith("emp"):
        return await start_employee_flow(db, session_id, identity.extract_smifs_email(message))
    if role == "client" or msg_lower.startswith("client") or "ucc" in msg_lower[:8]:
        return await start_client_flow(db, session_id, identity.extract_ucc(message, require_client_context=True))
    return {
        "blocks": [{"type": "text", "text": (
            "Just to be sure — please reply with **client** or **employee** so I can pull up the right record."
        )}],
        "citations": [], "model": None,
    }


# --------------- Phase 10: explicit role selection ---------------
async def select_role(db, session_id: str, role: str) -> Dict[str, Any]:
    """Role gate endpoint handler. Drive the state machine into the right
    AWAIT_* state and return the friendly next-turn prompt."""
    role = (role or "").strip().lower()
    if role not in ("client", "employee", "visitor"):
        return {
            "blocks": [{"type": "text", "text": "Please pick one of: client, employee, visitor."}],
            "citations": [], "model": None,
        }
    await get_or_create_session_row(db, session_id)
    if role == "employee":
        return await start_employee_flow(db, session_id, email=None)
    if role == "client":
        return await start_client_flow(db, session_id, ucc=None)
    # Visitor
    await _atomic_set(
        db, session_id,
        session_type="visitor", auth_state=ANON,
        pending_session_type=None, pending_identifier=None,
        pending_record=None, expected_pan_hash=None, failed_attempts=0,
    )
    return {
        "blocks": [{"type": "text", "text": (
            "Welcome to Mackertich ONE. I can share generic wealth-management concepts, "
            "and when you're ready, connect you with a Wealth Manager who can tailor "
            "specific recommendations to your goals. How can I help?"
        )}],
        "citations": [], "model": None,
        "intent_hint": "VISITOR_WELCOME",
    }


async def start_employee_flow(db, session_id: str, email: Optional[str]) -> Dict[str, Any]:
    if email:
        return await _do_employee_lookup(db, session_id, email)
    await _atomic_set(db, session_id,
                      session_type="employee", pending_session_type="employee",
                      auth_state=AWAIT_IDENT, failed_attempts=0,
                      pending_identifier=None, pending_record=None,
                      expected_pan_hash=None)
    return {
        "blocks": [{"type": "text", "text": (
            "Of course. Could you share your **work email** "
            "(your @smifs.com address) so I can pull up your record?"
        )}],
        "citations": [], "model": None,
    }


async def start_client_flow(db, session_id: str, ucc: Optional[str]) -> Dict[str, Any]:
    if ucc:
        return await _do_client_lookup(db, session_id, ucc)
    await _atomic_set(db, session_id,
                      session_type="client", pending_session_type="client",
                      auth_state=AWAIT_IDENT, failed_attempts=0,
                      pending_identifier=None, pending_record=None,
                      expected_pan_hash=None)
    return {
        "blocks": [{"type": "text", "text": (
            "Of course. Could you share your **client code (UCC)**? "
            "It's the numeric code on your contract note or ledger."
        )}],
        "citations": [], "model": None,
    }


async def handle_identifier_response(db, session_id: str, message: str) -> Dict[str, Any]:
    fresh = await db.sessions.find_one({"_id": session_id}) or {}
    pending_type = fresh.get("pending_session_type") or fresh.get("session_type")
    if pending_type == "employee":
        email = identity.extract_smifs_email(message) or identity.extract_email(message)
        if not email:
            return {
                "blocks": [{"type": "text", "text": (
                    "I didn't see a valid work email. Please share your @smifs.com address."
                )}],
                "citations": [], "model": None,
            }
        return await _do_employee_lookup(db, session_id, email)
    if pending_type == "client":
        ucc = identity.extract_ucc(message)
        # NO digit-stripping fallback — alphanumeric UCCs (e.g. "D900300") must
        # not be silently rewritten to a different account. Better to ask again.
        if not ucc:
            return {
                "blocks": [{"type": "text", "text": (
                    "I didn't see a valid client code. Please share the numeric UCC (4–8 digits)."
                )}],
                "citations": [], "model": None,
            }
        return await _do_client_lookup(db, session_id, ucc)
    return await start_role_inquiry(db, session_id)


async def _do_employee_lookup(db, session_id: str, email: str) -> Dict[str, Any]:
    try:
        rec = await identity.lookup_employee_by_email(email)
    except identity.OrgLensForbidden:
        logger.warning("OrgLens 403 (employees:pii missing) for email lookup")
        await _atomic_set(db, session_id, auth_state=ANON, session_type="visitor",
                          pending_session_type=None, pending_identifier=None)
        return {
            "blocks": [{"type": "text", "text": (
                "I cannot verify employees right now (directory permission denied). "
                "Please reach out to HR or your manager directly. "
                "I can still help you as a visitor in the meantime."
            )}],
            "citations": [], "model": None,
        }
    except Exception as e:
        logger.exception("OrgLens employee lookup failed: %s", str(e)[:120])
        await _atomic_set(db, session_id, auth_state=ANON, session_type="visitor",
                          pending_session_type=None, pending_identifier=None)
        return {
            "blocks": [{"type": "text", "text": (
                "Our directory service is briefly unavailable. Please try again in a moment, "
                "or continue as a visitor."
            )}],
            "citations": [], "model": None,
        }
    if rec is None:
        await _atomic_set(db, session_id, auth_state=ANON, session_type="visitor",
                          pending_session_type=None, pending_identifier=None,
                          pending_record=None, expected_pan_hash=None)
        return {
            "blocks": [{"type": "text", "text": (
                f"I couldn't find **{email}** in our directory. "
                "Want me to try another email, or shall I continue helping you as a visitor?"
            )}],
            "citations": [], "model": None,
        }
    pan_value = (rec.get("pan_number") or "").strip()
    if not identity.is_valid_pan(pan_value):
        logger.warning("OrgLens employee record missing PAN; falling back to escalation.")
        await _atomic_set(db, session_id, auth_state=ANON, session_type="visitor",
                          pending_session_type=None, pending_identifier=None)
        return {
            "blocks": [{"type": "text", "text": (
                "I found your record, but it lacks the verifier I need. "
                "Please reach out to your manager directly so they can verify you in person."
            )}],
            "citations": [], "model": None,
        }
    expected = identity.pan_hash(pan_value)
    sanitized = identity.sanitize_employee_for_storage(rec)
    await _atomic_set(
        db, session_id,
        session_type="employee", pending_session_type="employee",
        auth_state=AWAIT_PAN,
        pending_identifier=email,
        pending_record=sanitized,
        expected_pan_hash=expected,
        # Phase 7 — stash email_hash NOW so we never touch plaintext email again.
        email_hash=identity.email_hash(rec.get("email") or email),
        failed_attempts=0,
    )
    first = sanitized.get("first_name") or sanitized.get("name", "").split()[0] or "there"
    return {
        "blocks": [{"type": "text", "text": (
            f"Got it, {first}. For security, please share your **PAN** "
            f"(format: ABCDE1234F). It's used only to verify your identity and "
            f"is masked the moment you send it."
        )}],
        "citations": [], "model": None,
        "intent_hint": "AUTH_PAN_REQUEST",
    }


async def _do_client_lookup(db, session_id: str, ucc: str) -> Dict[str, Any]:
    try:
        rec = await identity.lookup_client_by_ucc(ucc)
    except identity.OrgLensForbidden:
        logger.warning("OrgLens 403 (clients:pii missing) for UCC lookup")
        await _atomic_set(db, session_id, auth_state=ANON, session_type="visitor",
                          pending_session_type=None, pending_identifier=None)
        return {
            "blocks": [{"type": "text", "text": (
                "I'm unable to look up client records right now. "
                "Please contact your relationship manager directly."
            )}],
            "citations": [], "model": None,
        }
    except Exception as e:
        logger.exception("OrgLens client lookup failed: %s", str(e)[:120])
        await _atomic_set(db, session_id, auth_state=ANON, session_type="visitor",
                          pending_session_type=None, pending_identifier=None)
        return {
            "blocks": [{"type": "text", "text": (
                "Our records service is briefly unavailable. Please try again shortly."
            )}],
            "citations": [], "model": None,
        }
    if rec is None:
        await _atomic_set(db, session_id, auth_state=ANON, session_type="visitor",
                          pending_session_type=None, pending_identifier=None,
                          pending_record=None, expected_pan_hash=None)
        return {
            "blocks": [{"type": "text", "text": (
                f"I couldn't locate UCC **{ucc}**. Could you double-check the code? "
                "Or I can help you as a prospect."
            )}],
            "citations": [], "model": None,
        }
    pan_value = (rec.get("pan") or "").strip()
    if not identity.is_valid_pan(pan_value):
        logger.warning("OrgLens client record missing PAN; falling back to escalation.")
        await _atomic_set(db, session_id, auth_state=ANON, session_type="visitor",
                          pending_session_type=None, pending_identifier=None)
        return {
            "blocks": [
                {"type": "text", "text": (
                    "I found your record, but I cannot verify it via PAN at this moment. "
                    "Please call our advisory desk so we can verify you in person."
                )},
                {"type": "escalation_card", "data": {"reason": "client_pan_unavailable"}},
            ],
            "citations": [], "model": None,
        }
    expected = identity.pan_hash(pan_value)
    sanitized = identity.sanitize_client_for_storage(rec)
    await _atomic_set(
        db, session_id,
        session_type="client", pending_session_type="client",
        auth_state=AWAIT_PAN,
        pending_identifier=ucc,
        pending_record=sanitized,
        expected_pan_hash=expected,
        # Phase 7 — client record's registered email (if any)
        email_hash=identity.email_hash(rec.get("email")) if rec.get("email") else None,
        phone_hash=identity.phone_hash(rec.get("telephone")) if rec.get("telephone") else None,
        failed_attempts=0,
    )
    first = sanitized.get("first_name") or "there"
    return {
        "blocks": [{"type": "text", "text": (
            f"Thanks{', ' + first if first != 'there' else ''}. "
            "For security, please share your **PAN** (format: ABCDE1234F). "
            "It's used only to verify your identity and is masked the moment you send it."
        )}],
        "citations": [], "model": None,
        "intent_hint": "AUTH_PAN_REQUEST",
    }


async def handle_pan_response(db, session_id: str, message: str) -> Dict[str, Any]:
    fresh = await db.sessions.find_one({"_id": session_id}) or {}
    expected = fresh.get("expected_pan_hash")
    pending = fresh.get("pending_record") or {}
    session_type = fresh.get("session_type") or "visitor"
    pan = identity.extract_pan(message)
    if not pan:
        return {
            "blocks": [{"type": "text", "text": (
                "I didn't catch a valid PAN in your message. "
                "PAN format is 5 letters + 4 digits + 1 letter (e.g. ABCDE1234F)."
            )}],
            "citations": [], "model": None,
        }
    if not expected:
        await _atomic_set(db, session_id, auth_state=ANON, session_type="visitor",
                          pending_session_type=None, pending_identifier=None,
                          pending_record=None, expected_pan_hash=None)
        return {
            "blocks": [{"type": "text", "text": (
                "Verification context was lost. Please tell me again whether you're "
                "a Mackertich ONE client or an SMIFS employee."
            )}],
            "citations": [], "model": None,
        }
    given_hash = identity.pan_hash(pan)
    if given_hash == expected:
        return await _finalise_verified(db, session_id, session_type, pending, given_hash)
    after = await _atomic_increment_attempts(db, session_id)
    attempts = (after or {}).get("failed_attempts", 1)
    if attempts >= MAX_FAILED_ATTEMPTS:
        until = _now() + timedelta(minutes=LOCKOUT_MINUTES)
        await _atomic_set(
            db, session_id,
            auth_state=LOCKED, locked_until=until.isoformat(),
            pending_record=None, expected_pan_hash=None,
        )
        return {
            "blocks": [
                {"type": "text", "text": (
                    f"For security, verification is now locked after {MAX_FAILED_ATTEMPTS} "
                    f"unsuccessful attempts. Please try again in {LOCKOUT_MINUTES} minutes, "
                    "or speak directly with our advisory desk."
                )},
                {"type": "escalation_card", "data": {"reason": "verification_locked"}},
            ],
            "citations": [], "model": None,
        }
    return {
        "blocks": [{"type": "text", "text": (
            f"That PAN doesn't match our records. Please try again "
            f"({attempts}/{MAX_FAILED_ATTEMPTS} attempts used). "
            "PAN format: ABCDE1234F."
        )}],
        "citations": [], "model": None,
    }


async def locked_response() -> Dict[str, Any]:
    return {
        "blocks": [
            {"type": "text", "text": (
                "Verification is locked for security. Please try again in a few minutes "
                "or call our advisory desk to speak with a human."
            )},
            {"type": "escalation_card", "data": {"reason": "verification_locked"}},
        ],
        "citations": [], "model": None,
    }


async def _finalise_verified(db, session_id: str, session_type: str,
                             pending: Dict[str, Any], pan_fp: str) -> Dict[str, Any]:
    now_iso = _now().isoformat()
    consent_default = True if session_type == "employee" else False
    # Phase 7 — identity hashes were already stashed on AWAIT_PAN. Final-set
    # only the verification-specific fields; don't overwrite hashes.
    fields: Dict[str, Any] = {
        "session_type": session_type,
        "auth_state": VERIFIED,
        "identity": pending,
        "pan_hash": pan_fp,
        "verified_at": now_iso,
        "failed_attempts": 0,
        "pending_session_type": None,
        "pending_identifier": None,
        "pending_record": None,
        "expected_pan_hash": None,
        "consent_to_ingest": consent_default,
        "lifecycle": "active",
    }
    if session_type == "employee":
        fields["emp_id_hash"] = identity.emp_id_hash(pending.get("employee_id"))
    elif session_type == "client":
        fields["ucc_hash"] = identity.ucc_hash(pending.get("ucc"))
        # Phase 10 — enrich client identity with the RM's work email/mobile
        # (the client OrgLens record doesn't return these). Best-effort.
        try:
            pending = await identity.enrich_client_rm_contact(pending)
            fields["identity"] = pending
        except Exception:
            pass
    await _atomic_set(db, session_id, **fields)

    # Phase 7 — rehydration offer on successful verification
    import lifecycle  # late import to avoid cycles
    offers = await lifecycle.rehydration_candidates_for_session(db, session_id)
    if session_type == "employee":
        payload = _employee_verified_payload(pending)
    else:
        payload = _client_verified_payload(pending)
    if offers:
        payload["blocks"] = [
            {"type": "resume_offer", "data": {"candidates": offers}},
        ] + payload["blocks"]
        payload["resume_offer"] = offers
    return payload


def _employee_verified_payload(emp: Dict[str, Any]) -> Dict[str, Any]:
    first = emp.get("first_name") or "there"
    # Build a richer 1-line context fragment for the deterministic welcome.
    role = emp.get("designation")
    dept = emp.get("department")
    role_clause = ""
    if role and dept:
        role_clause = f"as a {role} in {dept}"
    elif role:
        role_clause = f"as {role}"
    elif dept:
        role_clause = f"with the {dept} team"
    rt = emp.get("reports_to_name")
    rt_clause = f", reporting to {rt}" if rt else ""
    welcome = (
        f"Welcome, {first}. You're verified {role_clause}{rt_clause}. "
        "Happy to help with internal product specifics, KB queries, or anything else. "
        "How can I help today?"
    ).replace("verified .", "verified.")
    # Strip the bulky `raw` blob before shipping the card to the FE
    card_data = {k: v for k, v in emp.items() if k != "raw"}
    card_data["verified"] = True
    return {
        "blocks": [
            {"type": "text", "text": welcome},
            {"type": "employee_card", "data": card_data},
        ],
        "citations": [], "model": None,
        "intent_hint": "AUTH_VERIFIED",
    }


def _client_verified_payload(cli: Dict[str, Any]) -> Dict[str, Any]:
    first = cli.get("first_name") or "valued investor"
    rm = cli.get("rm_name") or "your relationship manager"
    risk = cli.get("risk_profile")
    branch = cli.get("branch_name")
    seg_yes = [k.upper() for k, v in (cli.get("segments") or {}).items() if v == "Yes"]
    seg_clause = f"{' + '.join(seg_yes[:3])} active" if seg_yes else "your account is active"
    risk_clause = f"{risk.lower()} profile" if risk else "your profile"
    rm_clause = f"with {rm}" + (f" at {branch}" if branch else "")
    welcome = (
        f"Welcome back, {first}. Your Mackertich ONE relationship is verified — "
        f"{risk_clause}, {rm_clause}, {seg_clause}. How can I help today?"
    )
    card_data = {k: v for k, v in cli.items() if k != "raw"}
    card_data["verified"] = True
    return {
        "blocks": [
            {"type": "text", "text": welcome},
            {"type": "client_card", "data": card_data},
        ],
        "citations": [], "model": None,
        "intent_hint": "AUTH_VERIFIED",
    }


# ---------------- helpers consumed by orchestrator/server ----------------
async def get_verified_identity(db, session_id: str) -> Optional[Dict[str, Any]]:
    row = await db.sessions.find_one({"_id": session_id}, {"_id": 0})
    if not row or row.get("auth_state") != VERIFIED:
        return None
    return row.get("identity")


def context_block_for(identity_obj: Optional[Dict[str, Any]]) -> Optional[str]:
    if not identity_obj:
        return None
    if identity_obj.get("type") == "employee":
        return identity.employee_context_block(identity_obj)
    if identity_obj.get("type") == "client":
        return identity.client_context_block(identity_obj)
    return None
