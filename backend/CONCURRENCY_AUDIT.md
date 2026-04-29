# Concurrency Audit — Mackertich ONE Advisor (Phase 5)

Goal: prove that two simultaneous chat sessions cannot interfere with each other,
and that auth-state machines for distinct sessions are isolated.

## Module-level mutable state inventory

| File | Symbol | Kind | Verdict | Reasoning |
|---|---|---|---|---|
| `agents/llm.py` | `_LAST_OK: Dict[str, str]` | per-task last-good-model cache | **Safe** | Mutated atomically (single-key write of an interned string); idempotent across requests. Worst case under contention: two tasks set the same key with the same value. No session data flows through it. |
| `agents/llm.py` | `_db_handle` | DB handle | **Safe (init-only)** | Set once via `bind_db()` at app startup; only read after that. |
| `agents/llm.py` | `LLMHUB_API_KEY`, `LLMHUB_BASE_URL`, `CHAT_CHAIN`, `ROUTER_CHAIN`, `HUB_HINT_FIELD`, `HUB_HINT_CHAT`, `HUB_HINT_ROUTER` | env-derived constants | **Safe** | Read-only after import. |
| `agents/router.py` | `INTENT_TOOLS`, `TOOL_TO_INTENT`, `ROUTER_TOOL_CHAIN`, `ROUTER_SYSTEM_*` | constants | **Safe** | Read-only. |
| `rag.py` | `EMBEDDER_KIND` | embedder selection | **Safe (init-only)** | Set once at startup by `detect_active_embedder()`. After that, only read by `embed_texts()`. |
| `rag.py` | `_local_model` | lazy SentenceTransformer instance | **Safe** | Lazy-instantiated under a single asyncio thread; `SentenceTransformer.encode` is itself thread-safe enough for sequential calls; we call it only via `asyncio.to_thread` from a single coroutine at a time per request. No shared mutable state in calls. |
| `rag.py` | `_index_lock: asyncio.Lock` | guards in-memory index load + reingest | **Safe** | Used to serialise loads and reingests; reads of `_index_matrix` after load are race-free because Python's GIL guarantees atomic reference reads of a single object slot. |
| `rag.py` | `_index_matrix`, `_index_meta` | numpy matrix + metadata list | **Safe (immutable after rebuild)** | Once built, read by `search()` without locking — but the matrix object reference is replaced atomically during reingest. Concurrent searches during a reingest will see either the old or the new matrix consistently (Python ref swap is atomic). No per-request mutation. |
| `widget_config.py` | `_cache` | last-loaded config doc | **Safe** | Lock-protected refresh; reads are lock-free once warm. Cache invalidated on PUT/reset. |
| `agents/orchestrator.py` | (none) | — | **Safe** | All mutable state is parameter-passed or DB-backed. |
| `server.py` | `app`, `db`, `client`, `api_router`, `ADMIN_TOKEN` | singletons | **Safe (init-only)** | Set once at module load. |

**Verdict:** zero "risky" findings. Every mutable module-level symbol is either init-only,
idempotent across requests, lock-guarded, or immutable-after-rebuild.

## Per-session state — where it lives

| Domain | Storage | Concurrency strategy |
|---|---|---|
| Conversation history | MongoDB `conversations._id == session_id` | One document per session; read+append uses `$push` for atomic appends (see Atomic Append below). |
| Auth state machine | MongoDB `sessions._id == session_id` | All transitions use `find_one_and_update` with `$set` / `$inc`, returning the post-transition row. No read-modify-write anywhere. |
| Verified client info | Derived per-request from `sessions.client_code` → `mock_clients` lookup | Pure read; no mutation. |
| Cost ledger | MongoDB `llm_calls` (immutable inserts) | Each call inserts a new doc; no shared mutable state. |
| Leads | MongoDB `leads` (immutable inserts) | Each submission inserts a new doc. |

## Atomic append for conversation history

`_append_message` in the orchestrator uses `db.conversations.update_one(... $push ...)` rather than
read-modify-write. This means two concurrent writes to the *same* session_id will both succeed and
both messages will end up in the array in well-defined order (MongoDB serialises updates against a
single doc).

## Auth-state atomic updates (Phase 5 hardening)

Previously `auth_agent` performed read-then-update for `failed_attempts`. Phase 5 hardens this:
- `_atomic_set_state` uses `find_one_and_update` with `returnDocument=AFTER` and returns the
  post-update row directly — callers no longer trust the pre-fetched `row`.
- `failed_attempts` is incremented via `$inc: {failed_attempts: 1}` so two concurrent wrong answers
  on the same session_id produce `attempts=2`, never `attempts=1`.
- The lock-out transition uses a guarded compare-and-set: `find_one_and_update` matching
  `failed_attempts >= MAX_FAILED_ATTEMPTS - 1` to avoid double-entering "locked".

In practice a single end-user can't fire two simultaneous answers, but adversarial clients can.
The hardened path is deterministic regardless.

## SSE generator capture safety

The `/api/agent/turn/stream` event source captures `req.session_id` and `req.message` as
parameter values, plus a private `asyncio.Queue` per request — none of these references escape
the request scope. The `runner()` task awaits orchestrator's per-call return and pushes events to
the request-local queue. No global state is touched.

## Conclusion

Module-level state: AUDIT CLEAN. Per-session state: lives in MongoDB keyed by `session_id`, with
all mutations going through atomic operators. Concurrency stress test
(`tests/test_concurrency.py`) verifies the design end-to-end with 20 parallel sessions and
10 parallel auth flows.
