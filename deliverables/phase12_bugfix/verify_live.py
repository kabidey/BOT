"""Live end-to-end verification of the Phase 12 alphanumeric-UCC bug fix.

Hits the real OrgLens API for UCC D900300 (PAN AIJPD2750P) and 63876
(PAN ARIPP3602Q), runs them through `sanitize_client_for_storage`, and
prints:

  * the resolved client_name + derived first_name
  * the list of keys remaining in `identity.raw` (proves PII strip)
  * a probe of forbidden fields (email/mobile/father_name/bank_*) in raw
  * absence of `_x000D_` artefacts in raw values
"""
from __future__ import annotations
import asyncio
import json
import os
import sys

sys.path.insert(0, "/app/backend")

from identity import (
    lookup_client_by_ucc,
    sanitize_client_for_storage,
    _RAW_STRIP_FIELDS,
)


FORBIDDEN = [
    "email", "mobile", "mobile1", "mobile2", "telephone",
    "father_name", "mother_name", "spouse_name",
    "bank", "bank_account", "bank_ifsc", "bank_branch",
    "aadhar_no", "aadhaar", "pan", "pan_number",
    "address1", "address2", "address3", "address4",
    "dob", "birth_date",
]


async def verify(ucc: str, expected_pan: str, expected_name_prefix: str) -> None:
    print(f"\n{'='*72}\nUCC: {ucc}  (expected PAN suffix match: {expected_pan})\n{'='*72}")
    raw = await lookup_client_by_ucc(ucc)
    if not raw:
        print(f"  [FAIL] OrgLens returned None for UCC {ucc}")
        return

    pan = raw.get("pan") or raw.get("pan_number")
    cli_name = raw.get("client_name")
    print(f"  OrgLens.client_name     = {cli_name!r}")
    print(f"  OrgLens.pan             = {pan!r}  (expected ~ {expected_pan})")

    pan_match_ok = (pan or "").upper().strip() == expected_pan.upper()
    name_match_ok = (cli_name or "").upper().startswith(expected_name_prefix.upper())
    print(f"  PAN matches expected?   = {pan_match_ok}")
    print(f"  Name starts with {expected_name_prefix!r}? = {name_match_ok}")

    curated = sanitize_client_for_storage(raw)
    print(f"  curated.name            = {curated.get('name')!r}")
    print(f"  curated.first_name      = {curated.get('first_name')!r}")
    print(f"  curated.email_display   = {curated.get('email_display')!r}")
    print(f"  curated.telephone_display = {curated.get('telephone_display')!r}")
    print(f"  curated.rm_name         = {curated.get('rm_name')!r}")
    print(f"  curated.branch_name     = {curated.get('branch_name')!r}")
    print(f"  curated.dp_name         = {curated.get('dp_name')!r}")

    raw_blob = curated.get("raw") or {}
    print(f"\n  identity.raw key count  = {len(raw_blob)}")

    # Forbidden-field probe
    leaks = [f for f in FORBIDDEN if f in raw_blob]
    if leaks:
        print(f"  [FAIL] identity.raw STILL contains PII fields: {leaks}")
    else:
        print(f"  [OK] identity.raw is clean — no forbidden fields present")

    # x000D probe
    x000d_hits = []
    for k, v in raw_blob.items():
        if isinstance(v, str) and ("_x000D_" in v or "_x000d_" in v):
            x000d_hits.append((k, v[:60]))
        if "_x000d_" in k or "_x000D_" in k:
            x000d_hits.append((f"KEY:{k}", "<key>"))
    if x000d_hits:
        print(f"  [FAIL] _x000D_ artefacts remain: {x000d_hits[:5]}")
    else:
        print(f"  [OK] no _x000D_ artefacts in raw")

    # _RAW_STRIP_FIELDS sanity: print fields actually stripped from this record
    stripped = sorted(_RAW_STRIP_FIELDS & set(raw.keys()))
    print(f"  fields stripped from raw: {stripped}")

    # Dump first 25 keys actually retained for visibility
    print(f"  identity.raw retained keys (first 25): {sorted(raw_blob.keys())[:25]}")


async def main() -> None:
    await verify("D900300", "AIJPD2750P", "SOMNATH")
    await verify("63876", "ARIPP3602Q", "A BALARAM")


if __name__ == "__main__":
    asyncio.run(main())
