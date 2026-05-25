"""Phase 14 — Sales-Ops Bridge backend.

Single endpoint: `POST /api/sales` accepts a sale entry, validates per-product
fields, persists to the `sales_entries` collection, and fires-and-forgets the
email notification.

Authorization: caller's `session_id` must resolve to a VERIFIED EMPLOYEE.
Clients/visitors get a 403.

Privacy:
    * Client PAN is plaintext-stored as a legitimate business record (Sales Ops
      must contact the client) but admin-token-gated on read.
    * `pan_hash` is also persisted for masked listing / dedupe.
    * The conversational rendering of `submission_id` confirms the sale but
      never echoes PAN, phone or email back into chat history.
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone, date
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query

import identity as id_mod
import email_relay
import sales_catalog

logger = logging.getLogger(__name__)


PRODUCTS = {"mutual_fund", "aif", "pms", "fd", "insurance", "ncd_primary", "sif"}
PAN_RE = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
PHONE_DIGITS_RE = re.compile(r"\D+")
ARN_RE = re.compile(r"^ARN-[A-Za-z0-9]{4,7}$|^[A-Za-z0-9]{4,7}$")

# Per-product field schema. `req` = required, `optional` = optional,
# `radio`/`select` define the allowed values for client+server validation.
#
# Phase 21 — Field-cleanup pass + SIF + extended ARN/APRN. Removed fields:
#   mutual_fund: folio_number, arn_distributor_code
#   aif:         category, drawdown_schedule, fund_manager
#   pms:         fee_structure, fixed_fee_pct, performance_fee_pct
#   fd:          interest_rate_pct
#   insurance:   product_type ENUM removed (still required, now free-text)
#   ncd_primary: coupon_rate_pct, tenure_years
# Insurance gained `premium_paying_term_years` + `premium_amount_inr`.
# Old sales rows retain dropped keys in `product_details` — admin drawer
# surfaces them under a "Legacy fields" collapsible (response_builder side).
_PRODUCT_SCHEMA: Dict[str, Dict[str, Any]] = {
    "mutual_fund": {
        "req": ["amc_name", "scheme_name", "scheme_type"],
        "optional": ["frequency"],
        "enums": {
            "scheme_type": {"SIP", "Lump sum", "SWP", "STP"},
            "frequency": {"Monthly", "Quarterly", "Annually"},
        },
    },
    "aif": {
        "req": ["aif_name", "commitment_amount_inr"],
        "optional": [],
        "enums": {},
        "numeric": {"commitment_amount_inr": (0, None)},
    },
    "pms": {
        "req": ["pms_provider", "strategy_name", "corpus_inr"],
        "optional": [],
        "enums": {},
        "numeric": {
            "corpus_inr": (5_000_000, None),
        },
    },
    "fd": {
        "req": ["issuer_name", "issuer_type", "tenure_months",
                "payout_frequency", "fd_type"],
        "optional": [],
        "enums": {
            "issuer_type": {"Bank", "NBFC", "Corporate FD"},
            "payout_frequency": {"Monthly", "Quarterly", "Half-yearly", "Annual", "On maturity"},
            "fd_type": {"Cumulative", "Non-cumulative"},
        },
        "numeric": {
            "tenure_months": (1, 120),
        },
    },
    "insurance": {
        "req": ["carrier", "product_type", "policy_term_years",
                "premium_paying_term_years", "premium_frequency",
                "sum_assured_inr", "premium_amount_inr"],
        "optional": [],
        "enums": {
            # product_type enum removed (now free-text).
            "premium_frequency": {"Single", "Annual", "Half-yearly", "Quarterly", "Monthly"},
        },
        "numeric": {
            "policy_term_years": (1, 50),
            "premium_paying_term_years": (1, 50),
            "sum_assured_inr": (0, None),
            "premium_amount_inr": (0, None),
        },
    },
    "ncd_primary": {
        # Public-issue NCD application. Amount must be a multiple of ₹1,000
        # because NCDs are issued in ₹1,000 face-value lots.
        "req": ["issuer_name", "series_option", "application_amount_inr",
                "interest_frequency"],
        "optional": ["asba_upi_reference"],
        "enums": {
            "interest_frequency": {"Monthly", "Quarterly", "Annual", "Cumulative"},
        },
        "numeric": {
            "application_amount_inr": (10_000, None),
        },
        # Custom rule: amount must be divisible by 1000.
        "custom": [
            ("application_amount_inr",
             lambda v: float(v) % 1000 == 0,
             "Application amount must be a multiple of ₹1,000 (NCD face value).")
        ],
    },
    "sif": {
        # Phase 21 — Specialised Investment Fund. Mirrors the MF shape:
        # vehicle-locked identity + free-form theme + investment-type radio +
        # conditional frequency (only when staggered) + optional lock-in.
        "req": ["sif_name", "strategy_theme", "investment_type"],
        "optional": ["frequency", "lock_in_months"],
        "enums": {
            "investment_type": {"Lump sum", "Staggered (SIP-equivalent)",
                                 "Open-ended subscription"},
            "frequency": {"Monthly", "Quarterly", "Annually"},
        },
        "numeric": {
            "lock_in_months": (0, 120),
        },
    },
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bad(field: str, msg: str) -> Dict[str, str]:
    return {"field": field, "error": msg}


def _validate_common(fields: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    """Returns (cleaned_common_fields, errors[])."""
    errors: List[Dict[str, str]] = []
    out: Dict[str, Any] = {}

    # client_name
    cn = (fields.get("client_name") or "").strip()
    if not cn or len(cn) < 2 or len(cn) > 120:
        errors.append(_bad("client_name", "Client name is required (2-120 chars)."))
    out["client_name"] = cn

    # client_pan
    pan = (fields.get("client_pan") or "").strip().upper().replace(" ", "").replace("-", "")
    if not PAN_RE.match(pan):
        errors.append(_bad("client_pan", "PAN must match ABCDE1234F format."))
    out["client_pan"] = pan

    # client_phone
    phone_raw = str(fields.get("client_phone") or "").strip()
    digits = PHONE_DIGITS_RE.sub("", phone_raw)
    if len(digits) >= 10:
        digits = digits[-10:]
    else:
        errors.append(_bad("client_phone", "Phone must be at least 10 digits."))
    out["client_phone"] = digits

    # client_email
    email = (fields.get("client_email") or "").strip().lower()
    if not EMAIL_RE.match(email):
        errors.append(_bad("client_email", "Provide a valid email address."))
    out["client_email"] = email

    # amount_inr
    try:
        amt = float(fields.get("amount_inr") or 0)
        if amt < 1000:
            errors.append(_bad("amount_inr", "Amount must be ≥ ₹1,000."))
    except Exception:
        errors.append(_bad("amount_inr", "Amount must be a number."))
        amt = 0
    out["amount_inr"] = amt

    # expected_login_date
    login = (fields.get("expected_login_date") or "").strip()
    pay = (fields.get("expected_payment_date") or "").strip()
    try:
        login_d = date.fromisoformat(login)
        if login_d < date.today():
            errors.append(_bad("expected_login_date", "Login date must be today or later."))
    except Exception:
        errors.append(_bad("expected_login_date", "Provide a valid date (YYYY-MM-DD)."))
        login_d = None
    try:
        pay_d = date.fromisoformat(pay)
        if login_d and pay_d < login_d:
            errors.append(_bad("expected_payment_date", "Payment date must be on or after login date."))
    except Exception:
        errors.append(_bad("expected_payment_date", "Provide a valid date (YYYY-MM-DD)."))
        pay_d = None
    out["expected_login_date"] = login
    out["expected_payment_date"] = pay

    # remarks (optional)
    remarks = (fields.get("remarks") or "").strip()
    if len(remarks) > 500:
        errors.append(_bad("remarks", "Remarks limited to 500 characters."))
    out["remarks"] = remarks[:500]

    return out, errors


def _validate_product(product: str, fields: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    schema = _PRODUCT_SCHEMA[product]
    errors: List[Dict[str, str]] = []
    out: Dict[str, Any] = {}
    for fname in schema["req"]:
        v = fields.get(fname)
        if v is None or (isinstance(v, str) and not v.strip()):
            errors.append(_bad(fname, "Required."))
            continue
        out[fname] = v.strip() if isinstance(v, str) else v
    for fname in schema.get("optional", []):
        v = fields.get(fname)
        if v not in (None, ""):
            out[fname] = v.strip() if isinstance(v, str) else v
    for fname, allowed in schema.get("enums", {}).items():
        if fname in out and out[fname] not in allowed:
            errors.append(_bad(fname, f"Must be one of: {sorted(allowed)}"))
    for fname, (lo, hi) in schema.get("numeric", {}).items():
        if fname in out:
            try:
                n = float(out[fname])
                if lo is not None and n < lo:
                    errors.append(_bad(fname, f"Must be ≥ {lo}."))
                if hi is not None and n > hi:
                    errors.append(_bad(fname, f"Must be ≤ {hi}."))
                out[fname] = n
            except Exception:
                errors.append(_bad(fname, "Must be a number."))
    # MF conditional: SIP/SWP/STP requires `frequency`
    if product == "mutual_fund" and out.get("scheme_type") in {"SIP", "SWP", "STP"}:
        if not fields.get("frequency"):
            errors.append(_bad("frequency", "Required when scheme_type is SIP/SWP/STP."))
    # Phase 21 — SIF conditional: `Staggered (SIP-equivalent)` requires
    # `frequency`. Lump sum / Open-ended must NOT carry a frequency (catch
    # tampering — return 422 if frequency is present in the wrong context).
    if product == "sif":
        itype = out.get("investment_type")
        freq = fields.get("frequency")
        if itype == "Staggered (SIP-equivalent)":
            if not freq:
                errors.append(_bad("frequency",
                                    "Required when investment_type is Staggered (SIP-equivalent)."))
        elif freq:
            errors.append(_bad("frequency",
                                "frequency only applies when investment_type is "
                                "'Staggered (SIP-equivalent)'."))
    # Phase 15 — generic custom rules (used by NCD primary issue for the
    # "amount must be a multiple of ₹1,000" check).
    for fname, predicate, msg in schema.get("custom", []):
        if fname in out:
            try:
                if not predicate(out[fname]):
                    errors.append(_bad(fname, msg))
            except Exception:
                # Numeric coercion failed — the numeric block above will have
                # already reported it.
                pass
    # NCD: surface read-only number_of_ncds for downstream listings.
    if product == "ncd_primary" and "application_amount_inr" in out:
        try:
            out["number_of_ncds"] = int(float(out["application_amount_inr"]) / 1000)
        except Exception:
            pass
    return out, errors


def _validate_mf_arn(fields: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    """Phase 21 — MF Folio Transfer subtype (formerly "ARN Transfer", kept on
    the same `subtype="arn_transfer"` key for back-compat with existing rows
    and the admin pipeline filter).

    Existing/new ARN codes and the transfer-effective date are no longer
    captured per user direction. The sub-flow is now a thin "folio transfer"
    report: AMC + scheme are still required (auto-fill from locked vehicle),
    plus folio numbers + AUM transferred + free-form remarks.
    """
    errors: List[Dict[str, str]] = []
    out: Dict[str, Any] = {"subtype": "arn_transfer"}
    sub = fields.get("arn_transfer_fields") or {}

    folio = (sub.get("folio_numbers") or "").strip()
    if not folio:
        errors.append(_bad("folio_numbers", "At least one folio number is required."))

    amc = (sub.get("amc_name") or fields.get("amc_name") or "").strip()
    scheme = (sub.get("scheme_name") or fields.get("scheme_name") or "").strip()
    if not amc:
        errors.append(_bad("amc_name", "AMC name is required (auto-fills from vehicle)."))
    if not scheme:
        errors.append(_bad("scheme_name", "Scheme name is required (auto-fills from vehicle)."))

    try:
        aum = float(sub.get("aum_inr") or 0)
        if aum < 1000:
            errors.append(_bad("aum_inr", "AUM transferred must be ≥ ₹1,000."))
    except Exception:
        errors.append(_bad("aum_inr", "AUM must be a number."))
        aum = 0

    remarks = (sub.get("arn_remarks") or "").strip()[:500]
    out["arn_transfer"] = {
        "folio_numbers": folio,
        "amc_name": amc,
        "scheme_name": scheme,
        "aum_inr": aum,
        "arn_remarks": remarks,
    }
    # For downstream consistency we also surface AMC/scheme at the top level
    # of `product_details` so the existing UI / admin row continues to work
    # without product-type-specific accessors.
    out["amc_name"] = amc
    out["scheme_name"] = scheme
    out["scheme_type"] = "ARN Transfer"
    return out, errors


def _validate_aif_arn(fields: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    """Phase 21 — AIF ARN Transfer sub-flow. AIF name auto-fills from the
    locked vehicle picker. Commitment account ID + AUM + remarks captured.
    """
    errors: List[Dict[str, str]] = []
    out: Dict[str, Any] = {"subtype": "arn_transfer"}
    sub = fields.get("arn_transfer_fields") or {}

    aif_name = (sub.get("aif_name") or fields.get("aif_name") or "").strip()
    if not aif_name:
        errors.append(_bad("aif_name", "AIF name is required (auto-fills from vehicle)."))

    acct = (sub.get("commitment_account_id") or "").strip()
    if not acct:
        errors.append(_bad("commitment_account_id", "Commitment account ID is required."))

    try:
        aum = float(sub.get("aum_inr") or 0)
        if aum < 1000:
            errors.append(_bad("aum_inr", "AUM transferred must be ≥ ₹1,000."))
    except Exception:
        errors.append(_bad("aum_inr", "AUM must be a number."))
        aum = 0

    remarks = (sub.get("arn_remarks") or "").strip()[:500]
    out["arn_transfer"] = {
        "aif_name": aif_name,
        "commitment_account_id": acct,
        "aum_inr": aum,
        "arn_remarks": remarks,
    }
    out["aif_name"] = aif_name
    return out, errors


def _validate_sif_arn(fields: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    """Phase 21 — SIF ARN Transfer sub-flow. SIF name auto-fills from the
    locked vehicle picker. Folio/account ID + AUM + remarks captured.
    """
    errors: List[Dict[str, str]] = []
    out: Dict[str, Any] = {"subtype": "arn_transfer"}
    sub = fields.get("arn_transfer_fields") or {}

    sif_name = (sub.get("sif_name") or fields.get("sif_name") or "").strip()
    if not sif_name:
        errors.append(_bad("sif_name", "SIF name is required (auto-fills from vehicle)."))

    acct = (sub.get("folio_account_id") or "").strip()
    if not acct:
        errors.append(_bad("folio_account_id", "Folio / account ID is required."))

    try:
        aum = float(sub.get("aum_inr") or 0)
        if aum < 1000:
            errors.append(_bad("aum_inr", "AUM transferred must be ≥ ₹1,000."))
    except Exception:
        errors.append(_bad("aum_inr", "AUM must be a number."))
        aum = 0

    remarks = (sub.get("arn_remarks") or "").strip()[:500]
    out["arn_transfer"] = {
        "sif_name": sif_name,
        "folio_account_id": acct,
        "aum_inr": aum,
        "arn_remarks": remarks,
    }
    out["sif_name"] = sif_name
    return out, errors


def _validate_pms_aprn(fields: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    """Phase 21 — PMS APRN Transfer sub-flow (APMI Registration Number).
    Separate subtype `aprn_transfer` so admin can route + filter it
    independently from the AIF/SIF/MF ARN family.

    Provider + strategy auto-fill from the locked vehicle picker. Portfolio
    account ID + transferred corpus + remarks captured.
    """
    errors: List[Dict[str, str]] = []
    out: Dict[str, Any] = {"subtype": "aprn_transfer"}
    sub = fields.get("aprn_transfer_fields") or {}

    provider = (sub.get("pms_provider") or fields.get("pms_provider") or "").strip()
    strategy = (sub.get("strategy_name") or fields.get("strategy_name") or "").strip()
    if not provider:
        errors.append(_bad("pms_provider", "PMS provider is required (auto-fills from vehicle)."))
    if not strategy:
        errors.append(_bad("strategy_name", "Strategy name is required (auto-fills from vehicle)."))

    acct = (sub.get("portfolio_account_id") or "").strip()
    if not acct:
        errors.append(_bad("portfolio_account_id", "Portfolio account ID is required."))

    try:
        corpus = float(sub.get("corpus_inr") or 0)
        if corpus < 1000:
            errors.append(_bad("corpus_inr", "Corpus transferred must be ≥ ₹1,000."))
    except Exception:
        errors.append(_bad("corpus_inr", "Corpus must be a number."))
        corpus = 0

    remarks = (sub.get("aprn_remarks") or "").strip()[:500]
    out["aprn_transfer"] = {
        "pms_provider": provider,
        "strategy_name": strategy,
        "portfolio_account_id": acct,
        "corpus_inr": corpus,
        "aprn_remarks": remarks,
    }
    out["pms_provider"] = provider
    out["strategy_name"] = strategy
    return out, errors


_ARN_TRANSFER_VALIDATORS = {
    "mutual_fund": _validate_mf_arn,
    "aif":         _validate_aif_arn,
    "sif":         _validate_sif_arn,
}


async def _next_submission_id(db) -> str:
    """Monotonic SALE-YYYY-NNN — uses a single counter doc with $inc."""
    year = datetime.now(timezone.utc).year
    cdoc = await db.sales_counters.find_one_and_update(
        {"_id": f"sales-{year}"},
        {"$inc": {"seq": 1}},
        upsert=True, return_document=True,  # ReturnDocument.AFTER
    )
    # Fallback if Motor returns the pre-update doc
    seq = (cdoc or {}).get("seq")
    if not seq:
        seq = 1
        await db.sales_counters.update_one({"_id": f"sales-{year}"}, {"$set": {"seq": 1}}, upsert=True)
    return f"SALE-{year}-{int(seq):04d}"


async def _verify_employee_session(db, session_id: Optional[str]) -> Dict[str, Any]:
    if not session_id:
        raise HTTPException(status_code=403, detail="session_id required")
    row = await db.sessions.find_one({"_id": session_id}, {"_id": 0}) or {}
    if row.get("auth_state") != "verified" or row.get("session_type") != "employee":
        raise HTTPException(status_code=403, detail="Only verified employees can log sales.")
    idn = row.get("identity") or {}
    return {
        "employee_id": idn.get("employee_id"),
        "name": idn.get("name") or " ".join(filter(None, [idn.get("first_name"), idn.get("last_name")])),
        "designation": idn.get("designation"),
        "department": idn.get("department"),
        "email": idn.get("email"),  # plaintext work email — needed for attribution
    }


def build_router(db) -> APIRouter:
    router = APIRouter()

    @router.get("/sales/catalog")
    async def sales_catalog_endpoint(session_id: str = Query(...)):
        """Phase 17 — Deck-pegged vehicle catalog for the Sales-Ops picker.

        Verified-employee-only (403 otherwise). Returns the same shape every
        call; the FE filters by product bucket client-side.
        """
        await _verify_employee_session(db, session_id)
        data = await sales_catalog.catalog(db)
        return data

    @router.post("/sales")
    async def create_sale(payload: Dict[str, Any]):
        product = (payload.get("form_type") or payload.get("product") or "").strip().lower()
        session_id = payload.get("session_id")
        fields = payload.get("fields") or {}
        if product not in PRODUCTS:
            raise HTTPException(status_code=400, detail=f"form_type must be one of {sorted(PRODUCTS)}")
        employee = await _verify_employee_session(db, session_id)

        # Phase 17 — vehicle deck cross-check (additive: legacy submissions
        # without vehicle_id still pass; new flows enforce it client-side via
        # the deck-driven picker. We enforce cross-type matching on the server
        # so a tampered request can't bind an NCD vehicle to the FD form.)
        vehicle_id = (fields.get("vehicle_id") or payload.get("vehicle_id") or "").strip() or None
        vehicle_row: Optional[Dict[str, Any]] = None
        if vehicle_id:
            vehicle_row = await sales_catalog.find_vehicle(db, vehicle_id)
            if not vehicle_row:
                raise HTTPException(status_code=400,
                                    detail="vehicle_id not found in current deck")
            if vehicle_row["product_type"] != product:
                raise HTTPException(
                    status_code=400,
                    detail=(f"vehicle_id belongs to product_type='{vehicle_row['product_type']}' "
                            f"but form_type='{product}' — cross-type mismatch"),
                )

        common, errs_c = _validate_common(fields)
        # Phase 17 — MF ARN-Transfer. Phase 21 — extended to AIF + SIF (same
        # `arn_transfer` subtype, different validator per product) and a
        # NEW `aprn_transfer` subtype for PMS only.
        is_arn = bool(fields.get("arn_transfer")) and product in _ARN_TRANSFER_VALIDATORS
        is_aprn = bool(fields.get("aprn_transfer")) and product == "pms"
        if is_arn:
            product_fields, errs_p = _ARN_TRANSFER_VALIDATORS[product](fields)
            subtype = "arn_transfer"
        elif is_aprn:
            product_fields, errs_p = _validate_pms_aprn(fields)
            subtype = "aprn_transfer"
        else:
            product_fields, errs_p = _validate_product(product, fields)
            subtype = None
        all_errors = errs_c + errs_p
        if all_errors:
            raise HTTPException(status_code=422, detail={"errors": all_errors})

        submission_id = await _next_submission_id(db)
        entry: Dict[str, Any] = {
            "_id": str(uuid.uuid4()),
            "submission_id": submission_id,
            "product": product,
            "subtype": subtype,
            "employee": employee,
            "client": {
                "client_name": common["client_name"],
                "client_pan": common["client_pan"],
                "client_phone": common["client_phone"],
                "client_email": common["client_email"],
            },
            "pan_hash": id_mod.pan_hash(common["client_pan"]),
            "product_details": product_fields,
            "amount_inr": common["amount_inr"],
            "expected_login_date": common["expected_login_date"],
            "expected_payment_date": common["expected_payment_date"],
            "remarks": common["remarks"],
            "status": "submitted",
            "email_sent": False,
            "email_sent_at": None,
            "email_recipients": [],
            "session_id": session_id,
            "vehicle_id": vehicle_id,
            "vehicle_name": (vehicle_row or {}).get("vehicle_name"),
            "vehicle_type": (vehicle_row or {}).get("vehicle_type"),
            "created_at": _now_iso(),
        }
        await db.sales_entries.insert_one(entry)

        # Fire-and-forget email; never block the response on SMTP success.
        async def _send_and_update():
            result = await email_relay.send_sale_notification(
                {**entry, "_id": None}, db=db,  # drop ObjectId; pass db for security_events
            )
            updates: Dict[str, Any] = {
                "email_recipients": result.get("recipients") or [],
                "email_routing": result.get("routing") or {},
                "email_status": result.get("reason"),
            }
            if result.get("ok"):
                updates.update({"email_sent": True, "email_sent_at": _now_iso()})
            try:
                await db.sales_entries.update_one(
                    {"submission_id": submission_id}, {"$set": updates}
                )
            except Exception:
                logger.exception("post-send update failed (non-fatal)")

        asyncio.create_task(_send_and_update())

        # Strip _id and full PAN before responding — admin needs the full record
        # via the admin endpoints, but the FE confirmation block doesn't.
        return {
            "submission_id": submission_id,
            "message": (
                f"Sale logged. Reference: **{submission_id}**. "
                "The Sales Ops team will follow up shortly."
            ),
            "product": product,
            "subtype": entry["subtype"],
            "vehicle_id": vehicle_id,
            "vehicle_name": entry.get("vehicle_name"),
            "amount_inr": common["amount_inr"],
            "client_name": common["client_name"],
            "client_pan_masked": id_mod.mask_pan(common["client_pan"]),
            "created_at": entry["created_at"],
        }

    return router
