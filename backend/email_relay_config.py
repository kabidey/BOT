"""Phase 19.2 — SMTP config storage with Fernet-encrypted password.

Source of truth precedence:
    1. Mongo `app_config` document `_id == "smtp_relay"`  (preferred)
    2. Env vars (legacy fallback, kept for back-compat)
    3. None (relay disabled)

In-process 5-minute memoization keeps the hot path (every sale email) off
Mongo. The cache is invalidated on `put_smtp_config` / `delete_smtp_config`.

Encryption at rest
==================
The plaintext SMTP password is encrypted with Fernet before being written
to Mongo. The key is read from env var `CONFIG_FERNET_KEY`; if that env
var is missing on first use, we generate a fresh key, persist it to
`/app/backend/.env`, and load it into the live process. Rotation: replace
the env var with a new key and re-`PUT` the SMTP config — the old encrypted
blob becomes unreadable (which is the desired behaviour during a rotation
incident).
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_ENV_FILE = Path(__file__).parent / ".env"
_FERNET_ENV_KEY = "CONFIG_FERNET_KEY"
_CONFIG_DOC_ID = "smtp_relay"
_CACHE_TTL_SECONDS = 300  # 5 minutes
_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}


# --------------------------- fernet key bootstrap ---------------------------

def _ensure_fernet_key() -> bytes:
    """Lazy bootstrap: generate + persist a Fernet key on first call if the
    env var is unset. Returns the key bytes ready for `Fernet(key)`.
    """
    raw = (os.environ.get(_FERNET_ENV_KEY) or "").strip()
    if raw:
        return raw.encode("utf-8")

    from cryptography.fernet import Fernet
    key = Fernet.generate_key()
    os.environ[_FERNET_ENV_KEY] = key.decode("utf-8")

    # Idempotent append to .env so the same key survives a restart.
    try:
        existing = _ENV_FILE.read_text(encoding="utf-8") if _ENV_FILE.exists() else ""
        if f"{_FERNET_ENV_KEY}=" not in existing:
            sep = "" if existing.endswith("\n") or not existing else "\n"
            with _ENV_FILE.open("a", encoding="utf-8") as fh:
                fh.write(
                    f"{sep}\n# Phase 19.2 — Fernet key for `app_config` SMTP "
                    f"password encryption (auto-generated)\n"
                    f"{_FERNET_ENV_KEY}={key.decode('utf-8')}\n"
                )
            logger.info("CONFIG_FERNET_KEY auto-generated and persisted to .env")
    except Exception:
        logger.exception("Failed to persist CONFIG_FERNET_KEY to .env (running with in-memory key)")
    return key


def _encrypt_password(plaintext: str) -> str:
    if not plaintext:
        return ""
    from cryptography.fernet import Fernet
    return Fernet(_ensure_fernet_key()).encrypt(plaintext.encode("utf-8")).decode("ascii")


def _decrypt_password(token: str) -> str:
    if not token:
        return ""
    from cryptography.fernet import Fernet, InvalidToken
    try:
        return Fernet(_ensure_fernet_key()).decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken:
        logger.error(
            "SMTP password decrypt failed — CONFIG_FERNET_KEY likely rotated. "
            "Re-PUT the SMTP config via /api/admin/email_relay/config to re-encrypt."
        )
        return ""


# ----------------------------- masking helpers ------------------------------

def mask_password(plaintext: str) -> str:
    """`***` + last 4 chars, or `***` if shorter than 5 chars."""
    if not plaintext:
        return ""
    if len(plaintext) < 5:
        return "***"
    return "***" + plaintext[-4:]


def mask_email(addr: str) -> str:
    """`we***@smifs.com` — preserves the domain, keeps first 2 of localpart."""
    if not addr or "@" not in addr:
        return addr or ""
    local, _, dom = addr.partition("@")
    if len(local) <= 2:
        return f"{local}***@{dom}"
    return f"{local[:2]}***@{dom}"


# ------------------------------- env fallback -------------------------------

def _env_config() -> Optional[Dict[str, Any]]:
    """Build a config dict from env vars; returns None when nothing is set."""
    host = (os.environ.get("SMTP_HOST") or "").strip()
    user = (os.environ.get("SMTP_USER") or "").strip()
    pwd = os.environ.get("SMTP_PASSWORD") or ""
    from_email = (os.environ.get("FROM_EMAIL") or "").strip()
    if not (host and user and pwd and from_email):
        return None
    cc_raw = os.environ.get("CC_OPS_FIXED", "")
    cc_ops = [a.strip().lower() for a in cc_raw.split(",") if a.strip()]
    return {
        "host": host,
        "port": int(os.environ.get("SMTP_PORT") or 587),
        "starttls": (os.environ.get("SMTP_STARTTLS") or "true").lower() != "false",
        "user": user,
        "password": pwd,
        "from_email": from_email,
        "from_name": (os.environ.get("FROM_NAME") or "SMIFS Wealth Guidance").strip(),
        "cc_ops_fixed": cc_ops,
        "source": "env",
        "updated_at": None,
    }


# ----------------------------- public surface -------------------------------

async def get_smtp_config(db) -> Optional[Dict[str, Any]]:
    """Returns the active SMTP config (with decrypted password in-process) or
    None when nothing is configured. Memoized for 5 minutes.

    Shape:
        {host, port, starttls, user, password, from_email, from_name,
         cc_ops_fixed: [...], source: "mongo"|"env", updated_at: <iso|None>}
    """
    cached = _CACHE.get("active")
    if cached and cached[0] > time.time():
        return cached[1]

    cfg: Optional[Dict[str, Any]] = None
    try:
        doc = await db.app_config.find_one({"_id": _CONFIG_DOC_ID}, {"_id": 0}) if db is not None else None
    except Exception:
        logger.exception("app_config read failed; falling back to env")
        doc = None

    if doc and doc.get("host") and doc.get("user") and doc.get("password_encrypted") and doc.get("from_email"):
        cfg = {
            "host": doc["host"],
            "port": int(doc.get("port") or 587),
            "starttls": bool(doc.get("starttls", True)),
            "user": doc["user"],
            "password": _decrypt_password(doc["password_encrypted"]),
            "from_email": doc["from_email"],
            "from_name": doc.get("from_name") or "SMIFS Wealth Guidance",
            "cc_ops_fixed": list(doc.get("cc_ops_fixed") or []),
            "source": "mongo",
            "updated_at": doc.get("updated_at"),
        }
        # Decrypt may fail silently after a key rotation — drop to env so the
        # relay doesn't silently break.
        if not cfg["password"]:
            logger.warning("Mongo SMTP config present but password decrypt empty; falling back to env")
            cfg = None

    if cfg is None:
        cfg = _env_config()

    _CACHE["active"] = (time.time() + _CACHE_TTL_SECONDS, cfg) if cfg else (time.time() + _CACHE_TTL_SECONDS, None)
    return cfg


async def put_smtp_config(
    db,
    *,
    host: str,
    port: int,
    starttls: bool,
    user: str,
    password: str,
    from_email: str,
    from_name: str,
    cc_ops_fixed: List[str],
    token_hash: str,
) -> Dict[str, Any]:
    """Upsert the SMTP config doc; encrypt the password; clear the cache.
    Returns the masked config (no plaintext password)."""
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    cc_clean = [a.strip().lower() for a in (cc_ops_fixed or []) if a and a.strip()]
    doc = {
        "_id": _CONFIG_DOC_ID,
        "host": host.strip(),
        "port": int(port),
        "starttls": bool(starttls),
        "user": user.strip(),
        "password_encrypted": _encrypt_password(password) if password else "",
        "password_last4": password[-4:] if password and len(password) >= 4 else "",
        "from_email": from_email.strip(),
        "from_name": from_name.strip() or "SMIFS Wealth Guidance",
        "cc_ops_fixed": cc_clean,
        "updated_at": now_iso,
        "updated_by_token_hash": token_hash,
    }
    await db.app_config.replace_one({"_id": _CONFIG_DOC_ID}, doc, upsert=True)
    invalidate_cache()

    # Audit row.
    try:
        await db.security_events.insert_one({
            "created_at": now_iso,
            "kind": "email_relay_config_changed",
            "session_id": None,
            "role_state": "admin",
            "user_message_excerpt": (
                f"host={host}:{port} user={mask_email(user)} from={mask_email(from_email)} "
                f"starttls={starttls} cc_ops_n={len(cc_clean)} pwd={mask_password(password)}"
            ),
            "action": "smtp_config_upsert",
            "updated_by_token_hash": token_hash,
        })
    except Exception:
        logger.exception("security_events insert failed for smtp_config (non-fatal)")

    logger.info(
        "smtp_config upserted host=%s port=%s user=%s from=%s starttls=%s cc_ops_n=%d",
        host, port, mask_email(user), mask_email(from_email), starttls, len(cc_clean),
    )
    return masked_view(doc)


async def delete_smtp_config(db, *, token_hash: str) -> bool:
    """Hard-delete the Mongo config so we fall back to env (or unconfigured)."""
    res = await db.app_config.delete_one({"_id": _CONFIG_DOC_ID})
    invalidate_cache()
    if res.deleted_count:
        from datetime import datetime, timezone
        try:
            await db.security_events.insert_one({
                "created_at": datetime.now(timezone.utc).isoformat(),
                "kind": "email_relay_config_changed",
                "session_id": None,
                "role_state": "admin",
                "user_message_excerpt": "deleted",
                "action": "smtp_config_delete",
                "updated_by_token_hash": token_hash,
            })
        except Exception:
            logger.exception("security_events insert failed for smtp_config_delete")
    return bool(res.deleted_count)


def invalidate_cache() -> None:
    _CACHE.clear()


def masked_view(doc_or_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Render a doc / live cfg as a masked-for-display payload."""
    last4 = doc_or_cfg.get("password_last4")
    if not last4 and doc_or_cfg.get("password"):
        pw = doc_or_cfg["password"]
        last4 = pw[-4:] if len(pw) >= 4 else ""
    pwd_masked = ("***" + last4) if last4 else ("***" if (doc_or_cfg.get("password_encrypted") or doc_or_cfg.get("password")) else "")
    return {
        "host": doc_or_cfg.get("host"),
        "port": doc_or_cfg.get("port"),
        "starttls": bool(doc_or_cfg.get("starttls", True)),
        "user": doc_or_cfg.get("user"),
        "password_masked": pwd_masked,
        "password_set": bool(doc_or_cfg.get("password_encrypted") or doc_or_cfg.get("password")),
        "from_email": doc_or_cfg.get("from_email"),
        "from_name": doc_or_cfg.get("from_name"),
        "cc_ops_fixed": list(doc_or_cfg.get("cc_ops_fixed") or []),
        "source": doc_or_cfg.get("source") or "mongo",
        "updated_at": doc_or_cfg.get("updated_at"),
    }


async def get_masked_config_view(db) -> Dict[str, Any]:
    """View used by `GET /api/admin/email_relay/config`. Includes a
    `source` field so admin can see whether the relay is reading from
    Mongo or env (or is unconfigured)."""
    # Direct Mongo read so we surface the source accurately even when the
    # cache is warm with an env fallback.
    doc = None
    try:
        doc = await db.app_config.find_one({"_id": _CONFIG_DOC_ID}, {"_id": 0})
    except Exception:
        logger.exception("app_config read failed in get_masked_config_view")

    if doc:
        view = masked_view(doc)
        view["source"] = "mongo"
        return view

    env = _env_config()
    if env:
        view = masked_view(env)
        view["source"] = "env"
        return view

    return {
        "host": None, "port": None, "starttls": True,
        "user": None, "password_masked": "", "password_set": False,
        "from_email": None, "from_name": None, "cc_ops_fixed": [],
        "source": "none", "updated_at": None,
    }


# ------------------------- test-connection classifier -----------------------

async def test_connection(cfg: Dict[str, Any], *, timeout: float = 15.0) -> Dict[str, Any]:
    """Open an SMTP connection + STARTTLS + AUTH against `cfg` creds, then
    immediately QUIT. Returns a classified result. NEVER logs the password.
    """
    try:
        import aiosmtplib
    except ImportError:
        return {"ok": False, "error_kind": "unknown_error",
                "error_message": "aiosmtplib not installed"}

    host = cfg.get("host")
    port = int(cfg.get("port") or 587)
    user = cfg.get("user")
    pwd = cfg.get("password") or ""
    starttls = bool(cfg.get("starttls", True))
    if not (host and user and pwd):
        return {"ok": False, "error_kind": "unknown_error",
                "error_message": "host/user/password missing"}

    client = aiosmtplib.SMTP(hostname=host, port=port, timeout=timeout,
                             start_tls=False, use_tls=False)
    try:
        await client.connect()
    except Exception as e:
        return {"ok": False, "error_kind": "connection_refused",
                "error_message": _scrub(str(e), pwd)[:300]}

    try:
        if starttls:
            try:
                await client.starttls()
            except Exception as e:
                try:
                    await client.quit()
                except Exception:
                    pass
                return {"ok": False, "error_kind": "tls_failed",
                        "error_message": _scrub(str(e), pwd)[:300]}

        try:
            await client.login(user, pwd)
        except Exception as e:
            kind, msg = _classify_auth_exception(e, pwd)
            try:
                await client.quit()
            except Exception:
                pass
            return {"ok": False, "error_kind": kind, "error_message": msg[:300]}

        try:
            await client.quit()
        except Exception:
            pass
        return {"ok": True, "error_kind": None, "error_message": None}
    except Exception as e:  # last-resort
        try:
            await client.quit()
        except Exception:
            pass
        return {"ok": False, "error_kind": "unknown_error",
                "error_message": _scrub(str(e), pwd)[:300]}


def _classify_auth_exception(exc: Exception, pwd: str) -> Tuple[str, str]:
    msg = _scrub(str(exc), pwd)
    low = msg.lower()
    if "535" in msg and ("basic authentication is disabled" in low
                         or "smtpclientauthentication" in low):
        return "auth_disabled", msg
    if "535" in msg or "authentication" in low and "unsuccess" in low:
        return "auth_failed", msg
    if "timeout" in low:
        return "timeout", msg
    return "unknown_error", msg


def _scrub(text: str, pwd: str) -> str:
    if pwd and pwd in text:
        return text.replace(pwd, "***")
    return text
