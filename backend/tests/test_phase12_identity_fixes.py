"""Phase 12 bug-fix — unit tests for identity helpers."""
from __future__ import annotations
import sys
sys.path.insert(0, "/app/backend")

from identity import _derive_first_name, extract_ucc


# ---------- _derive_first_name ----------
def test_first_name_skips_single_letter_initial():
    assert _derive_first_name("A BALARAM PATRO", "balaram.patro143@gmail.com") == "Balaram"


def test_first_name_prefers_official_over_email_handle():
    # Email handle = "deynet" → would mis-derive "Deynet"; official name wins.
    assert _derive_first_name("SOMNATH DEY", "deynet@gmail.com") == "Somnath"


def test_first_name_falls_back_to_email_when_no_name():
    assert _derive_first_name("", "john.doe@example.com") == "John"
    assert _derive_first_name(None, "john.doe@example.com") == "John"


def test_first_name_skips_honorifics():
    assert _derive_first_name("DR. R K MEHTA", "rk.mehta@example.com") == "Mehta"
    assert _derive_first_name("SHRI ANIL KUMAR", "anil@x.com") == "Anil"


def test_first_name_returns_none_when_nothing_usable():
    assert _derive_first_name("", "") is None
    assert _derive_first_name(None, None) is None


def test_first_name_handles_trailing_punctuation():
    assert _derive_first_name("BALARAM.", "x@y.com") == "Balaram"


# ---------- extract_ucc ----------
def test_extract_ucc_digits_only():
    assert extract_ucc("63876") == "63876"
    assert extract_ucc("my UCC is 63876") == "63876"


def test_extract_ucc_alpha_prefix_preserved():
    assert extract_ucc("D900300") == "D900300"
    assert extract_ucc("d900300") == "D900300"  # canonical upper
    assert extract_ucc("DM12345") == "DM12345"


def test_extract_ucc_in_sentence():
    assert extract_ucc("my UCC is D900300") == "D900300"


def test_extract_ucc_rejects_year():
    # Pure 4-digit years should NOT match
    assert extract_ucc("Joined SMIFS in 2019") is None
    assert extract_ucc("year 2024 onwards") is None


def test_extract_ucc_rejects_leading_zero():
    assert extract_ucc("01234") is None
    # alpha-prefix records are NOT subject to leading-zero filter
    assert extract_ucc("A01234") == "A01234"


def test_extract_ucc_no_match():
    assert extract_ucc("hello there") is None
    assert extract_ucc("") is None
    assert extract_ucc(None) is None
