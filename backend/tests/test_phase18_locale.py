"""Phase 18 — Workstream B acceptance tests for multilingual locale.

Two sub-suites:
  * Pure helper: `orchestrator.locale_instruction()` returns the exact
    strict-injection wording for hi/ta and empty string for en.
  * `_maybe_inject_context()`: locale instruction is appended for hi/ta
    regardless of identity_obj (visitors must localise too).
"""
from __future__ import annotations
import sys

import pytest

sys.path.insert(0, "/app/backend")

from agents import orchestrator  # noqa: E402


# ---------------- helper assertions ----------------
HINDI_KEY_PHRASES = [
    "Respond entirely in Hindi",
    "Devanagari script",
    "PAN, UCC, NAV, AUM, ARN, SIP, NCD",
]
TAMIL_KEY_PHRASES = [
    "Respond entirely in Tamil",
    "Tamil script",
    "PAN, UCC, NAV, AUM, ARN, SIP, NCD",
]


def test_locale_instruction_english_is_noop():
    assert orchestrator.locale_instruction("en") == ""
    assert orchestrator.locale_instruction(None) == ""
    assert orchestrator.locale_instruction("EN") == ""


def test_locale_instruction_hindi_contains_all_required_phrases():
    text = orchestrator.locale_instruction("hi")
    for phrase in HINDI_KEY_PHRASES:
        assert phrase in text, f"hi instruction missing phrase: {phrase}"


def test_locale_instruction_tamil_contains_all_required_phrases():
    text = orchestrator.locale_instruction("ta")
    for phrase in TAMIL_KEY_PHRASES:
        assert phrase in text, f"ta instruction missing phrase: {phrase}"


def test_locale_instruction_unknown_locale_is_noop():
    """Defensive: unknown locale codes must not blow up — they should fall
    back to English (no extra instruction appended)."""
    assert orchestrator.locale_instruction("fr") == ""
    assert orchestrator.locale_instruction("bn") == ""


# ---------------- _maybe_inject_context behaviour ----------------
SYSTEM = "You are the advisor."


def test_inject_context_english_visitor_is_pass_through():
    out = orchestrator._maybe_inject_context(SYSTEM, None, locale="en")
    assert out == SYSTEM


def test_inject_context_hindi_visitor_appends_instruction():
    """Visitors (no identity_obj) MUST still localise — locale is session-
    level, not identity-bound."""
    out = orchestrator._maybe_inject_context(SYSTEM, None, locale="hi")
    assert out.startswith(SYSTEM)
    assert "Respond entirely in Hindi" in out


def test_inject_context_tamil_visitor_appends_instruction():
    out = orchestrator._maybe_inject_context(SYSTEM, None, locale="ta")
    assert "Respond entirely in Tamil" in out
    assert "Tamil script" in out


def test_inject_context_locale_from_identity_blob_fallback():
    """If the caller didn't pass `locale` explicitly but the identity blob
    carries one, that locale wins."""
    identity = {"type": "client", "first_name": "Ada", "locale": "hi"}
    # Cant test the full context_block_for here (it pulls real identity
    # helpers), so we patch it locally to just return None.
    orig = orchestrator.auth_agent.context_block_for
    orchestrator.auth_agent.context_block_for = lambda _: None
    try:
        out = orchestrator._maybe_inject_context(SYSTEM, identity)
        assert "Respond entirely in Hindi" in out
    finally:
        orchestrator.auth_agent.context_block_for = orig


def test_inject_context_explicit_locale_overrides_identity_blob():
    """If both are present, the explicit `locale` arg wins (session-level
    state is the source of truth)."""
    identity = {"type": "client", "first_name": "Ada", "locale": "hi"}
    orig = orchestrator.auth_agent.context_block_for
    orchestrator.auth_agent.context_block_for = lambda _: None
    try:
        out = orchestrator._maybe_inject_context(SYSTEM, identity, locale="ta")
        assert "Respond entirely in Tamil" in out
        assert "Respond entirely in Hindi" not in out
    finally:
        orchestrator.auth_agent.context_block_for = orig
