# Phase 20 — Architecture Proposal: Dynamic Tool Registry + Multi-Format Rendering

> **One-paragraph thesis.** Replace the current 6 hand-written `directory_agent` / `client_api` tool stubs with a manifest-driven registry that exposes the full ≈ 35-endpoint OrgLens external-api surface as **typed, role-gated, latency-tagged tools**. At each turn, a lightweight *Question Analyzer* (single call to a small LLM) classifies the question on three axes (entity / operation / output-format), narrows the registry to **3-5 candidate tools**, and hands them to Hub AI's native function-calling — letting the model **compose up to 5 tool calls per turn** with shared in-process + Mongo caching. The renderer learns **four new block types** (Table, Chart, Image, Download) on top of the existing card blocks, and the orchestrator picks a block type **before** generation, so the prompt instructs the LLM to emit the right shape rather than always falling back to prose. Net effect: questions like *"compare client X vs Y portfolio composition"* return a chart, not a wall of text — and the underlying data path is the same 800 ms tool call we already pay for.

---

## A. Tool Registry

### Manifest layout — `backend/orglens_tools/manifest.yaml`

One YAML document per tool. Loaded once at boot into an immutable in-memory registry, validated against a Pydantic `ToolSpec` model.

```yaml
- name: employee_by_code
  description: |
    Resolve a SMIFS employee by their employee_id (e.g., SMWM-25031054).
    Returns the FULL 77-field profile: identity, contact, hierarchy (HOD/HRBP/
    manager), org placement (band/grade/cost-centre/business-unit), employment
    status, probation, joining/confirmation dates. PII fields are masked for
    non-admin callers via the adapter layer.
  endpoint: GET /api/v1/employee/by-code/{employee_code}
  required_scope: employees:basic
  allowed_roles: [visitor, client, employee]   # visitor sees masked output only
  latency_tier: medium                          # ~800 ms p50
  parameters:
    employee_code:
      type: string
      required: true
      pattern: '^SM[A-Z]{2}-\d{8}$'
      description: SMIFS employee identifier (e.g., SMWM-25031054).
  returns:
    schema_ref: '#/components/schemas/EmployeeFull'
    field_groups:
      identity:    [employee_id, first_name, last_name, name, email, mobile_number, profile_pic]
      hierarchy:   [reports_to_name, reports_to_email, hod_name, hod_email, hrbp_name, hrbp_email, manager_mongo_id, total_team_size, direct_reports_count]
      org:         [department, designation, band, grade, business_unit, location, bu_cost_center_name, dept_cost_center_name]
      employment:  [employment_status, date_of_joining, date_of_confirmation, confirmation_status, current_experience, on_notice]
      compensation: [fixed_ctc, total_ctc, salary_structure]   # never exposed to visitor/client
  output_block_hints: [employee_card, fact, hierarchy_table]
  cache_ttl_seconds: 300
```

### Why YAML, not Python decorators

* Engineers + ops can review the manifest without reading orchestrator code.
* Diffable. A pull-request adding a new endpoint is a single YAML stanza.
* The same manifest powers (a) the live registry, (b) the admin debug panel listing every tool with its latency / call count, (c) the auto-generated documentation page at `/api/admin/tools/registry`.

### Registry API (sketch)

```python
class ToolSpec(BaseModel):
    name: str
    description: str
    endpoint: str                              # "GET /api/v1/...{ucc}"
    required_scope: str | None
    allowed_roles: list[Literal["visitor","client","employee","admin"]]
    latency_tier: Literal["fast","medium","slow"]
    parameters: dict[str, ParamSpec]
    returns: ReturnSpec
    output_block_hints: list[str]
    cache_ttl_seconds: int = 0

class ToolRegistry:
    def all(self) -> list[ToolSpec]: ...
    def visible_to(self, role: str, scope: set[str]) -> list[ToolSpec]: ...
    def select(self, *, role: str, intent: IntentClassification,
                 max_tools: int = 5) -> list[ToolSpec]: ...
    def adapter_for(self, name: str) -> Callable[..., Awaitable[ToolResult]]: ...
```

* `select(...)` is where the budget rule lives. Visitors get **≤ 5 tools** (departments, locations, designations, public stats — nothing PII-shaped). Clients get **≤ 5 tools** with `ucc=` parameters auto-bound to their verified UCC. Employees get **≤ 8 tools** based on intent.
* `adapter_for(...)` returns a thin async function that: (1) checks role + scope, (2) substitutes `ucc` / `employee_id` from session when the caller is a verified client/employee, (3) reads cache, (4) calls OrgLens, (5) masks PII per role, (6) records to `tool_calls`.

### Boot-time validation

* Every `endpoint` in the manifest must resolve to a real operation in the live `/api/openapi.json`. If a manifest entry has stale path/params, the backend refuses to start and surfaces a single line in `/api/admin/email_relay/...` style — "tool X mismatches OrgLens spec field Y". This catches OrgLens API drift on day one.
* Every `required_scope` must be present in the `permissions` response. If our key lacks `employees:compensation`, the manifest entry is loaded as `disabled=True` and the tool never appears in `visible_to(...)`.

---

## B. Composition

### Turn lifecycle

```
1. user message → orchestrator
2. orchestrator → Question Analyzer (1 call, gpt-4o-mini class)
        ↓ returns { entity, operation, output_format, language }
3. orchestrator → registry.select(role, classification) → 3-5 candidate tools
4. orchestrator → Hub AI chat/completions with `tools=[...]`
        ↓ Hub AI returns tool_calls[]  (function-calling spec)
5. orchestrator → asyncio.gather(*[adapter(call) for call in tool_calls])
        ↓ shared in-process LRU + Mongo cache (TTL per tool spec)
6. orchestrator → Hub AI second pass with tool results (forced output format)
7. orchestrator → renderer (picks block type via classification + tool_hints)
```

### Composition rules

* **Hard cap: 5 sequential tool calls per turn.** If the LLM tries to chain more, the orchestrator stops, returns the partial answer, and shows a *"Continue"* CTA the user can click (sends an implicit follow-up).
* **Parallel where possible.** When the LLM emits multiple `tool_calls` in one response (which Hub AI's function-calling supports), we resolve them in parallel via `asyncio.gather`. Two parallel 800 ms calls = 800 ms wall.
* **Result-bus cache.** A `(tool_name, params_hash)` key. In-process LRU (max 256 entries) + Mongo `tool_call_cache` collection with TTL per tool's `cache_ttl_seconds`. Mongo cache survives backend restarts and is shared across processes once we scale horizontally.
* **Inter-turn cache.** Same key namespace, scoped by `session_id`. A user asking "show Aaditya's profile" then "what's his department" the second turn reuses the first turn's result (no second OrgLens call).
* **Streaming partial answers.** If a single tool call takes > 2.5 s AND we already have at least one tool result, stream what we have to the chat (`"fetching more…"` shimmer block) while the slow one finishes. Implementable as a single SSE event from the existing chat endpoint.

### Two example traces

> **Q:** *"Show me Aaditya's top 3 clients by AUM with their last transaction."*
>
> 1. Analyzer → `{entity: employee+client, operation: aggregate+lookup, format: table}`
> 2. Registry → tools = [`employee_by_code`, `mf_clients_by_rm`, `mf_client_transactions`]
> 3. LLM call 1 → `[employee_by_code(SMWM-25031054)]`
> 4. LLM call 2 → `[mf_clients_by_rm(rm_name="Aaditya R. Jaiswal", sort="aum", limit=3)]`
> 5. LLM call 3 → `mf_client_transactions(uid=...)` × 3 in **parallel**
> 6. Renderer → `TableBlock` (cols: client, aum, last_txn_scheme, last_txn_amount, last_txn_date)

Total tool calls: 5 (1 + 1 + 3 parallel). Wall: ~1.8 s of network + ~3 s of LLM = under budget.

> **Q:** *"How does our client base break down by category?"*
>
> 1. Analyzer → `{entity: client-aggregate, operation: aggregate, format: chart}`
> 2. Registry → tools = [`clients_stats`]
> 3. LLM call 1 → `clients_stats()` → 22 categories, counts
> 4. Renderer → `ChartBlock` (pie, top 7 + "others")

---

## C. Output Rendering — four new block types

The chat already renders `EmployeeCardBlock`, `ClientCardBlock`, `text`, `citation_chips`. We add four more, each a JSON envelope the backend emits and `Chat.jsx` switches on.

### 1. `TableBlock`

```json
{
  "type": "table",
  "title": "Top 3 clients by AUM",
  "columns": [
    {"key": "client_name", "label": "Client", "type": "text",  "frozen": true},
    {"key": "aum",         "label": "AUM",    "type": "inr",   "sortable": true, "default_sort": "desc"},
    {"key": "last_txn",    "label": "Last txn", "type": "date_relative"}
  ],
  "rows":     [ /* up to 50 — page after that */ ],
  "row_total": 3,
  "csv_url":  "/api/exports/abc-123.csv",
  "footnote": "Showing 3 of 47. Cached for 90 s."
}
```

* Mobile (< 640 px) → collapses to a card-per-row layout (existing pattern, see `SalesPipelineTab`).
* Pagination beyond 50 rows → server-side, the LLM never sees a 200-row blob.
* Columns are *typed* so the renderer can right-align INRs, render relative dates, mask PAN, etc.

### 2. `ChartBlock`

```json
{
  "type": "chart",
  "kind": "pie",            // bar | line | pie | sparkline
  "title": "Client mix by category",
  "x_key": "name", "y_key": "count",
  "data": [ {"name": "Individual", "count": 34722}, ... ],
  "max_slices": 7,          // top 7 + "Others"
  "theme": "smifs-dark"
}
```

* **Recommendation — Recharts (SVG, client-side).** Justification:
  * Themable via CSS variables; we already have a token system.
  * Client-side renders at < 50 ms; no PNG round-trip cost.
  * Zero new server dependency (no kaleido/matplotlib install).
  * Accessibility: SVG charts work with the screen-readers we already wire in the admin tab.
  * Mobile-friendly out of the box (responsive container).
* **When to use server-rendered PNG instead:** only if we ever need to embed a chart inside an email (no JS to render). Defer until that use case actually exists.

### 3. `ImageBlock`

```json
{
  "type": "image",
  "src": "/api/exports/chart-org-tree-deadbeef.png",
  "alt": "SMIFS reporting tree, 6 root managers, 318 leaves",
  "width": 1200, "height": 800,
  "expires_at": "<ISO>",     // signed URL, 10-min TTL
  "download_filename": "smifs-org-tree.png"
}
```

* **Used only for things HTML/SVG can't do well**: an org-tree visual export, a portfolio-allocation infographic for a screenshot/share. Generated server-side with **matplotlib + kaleido** (already battle-tested for one-off renders).
* Saved to `/app/uploads/charts/<hash>.png`. Served via the existing `/api/exports/{id}` signed-URL pattern. 10-minute TTL — long enough to ship the chat-render lifecycle, short enough that it never leaks.

### 4. `DownloadBlock`

```json
{
  "type": "download",
  "title": "Trade book — FY25-26",
  "format": "csv",
  "url": "/api/exports/trades-deadbeef.csv",
  "row_count": 187,
  "size_bytes": 24533,
  "expires_at": "<ISO>"
}
```

* Triggered when a tool returns > 50 rows OR > 8 columns.
* The renderer shows a compact card with a download icon and the row count; the LLM still narrates a 3-line summary on top.
* CSV generation happens in the adapter; we never stream a 200-row blob through the LLM.

### Existing blocks (reused, no change)

* `EmployeeCardBlock` — single employee lookup (Aaditya's profile).
* `ClientCardBlock` — single client lookup (UCC M700778).
* `CitationChipsBlock` — knowledge / deck citations.
* `text` — fallback narrative.

---

## D. Intelligence Layer — the "Question Analyzer"

A single small-model call (gpt-4o-mini class, ≤ 200 input tokens, ≤ 100 output) **before** the orchestrator picks tools. It returns a JSON envelope:

```json
{
  "entity": "employee | client | vehicle | transaction | aggregate | mixed",
  "operation": "lookup | aggregate | compare | trend | explain | refuse",
  "output_format": "single_fact | card | table | chart | image | download | narrative",
  "language": "en | hi | ta | bn",
  "confidence": 0.93
}
```

* Cost: ~200 input + 50 output ≈ $0.0001/turn at gpt-4o-mini pricing — negligible.
* This classification feeds **two** downstream decisions:
  1. `ToolRegistry.select(...)` narrows the function-calling tool list (3-5 tools, not 35).
  2. The system prompt for the second-pass call tells the LLM *"answer using a `chart` block"* — so the LLM doesn't dump prose when the user asked for a comparison.
* `operation = compare | aggregate | trend` → defaults to TableBlock or ChartBlock, never text.
* `operation = refuse` → orchestrator short-circuits the LLM, returns the existing guardrail apology.
* On low confidence (< 0.6), we fall through to the existing rigid router as a safety net.

### How this improves on the current router

The existing `agents/router.py` already classifies intent, but only into ~6 buckets and only uses it to pick *which agent* runs. We extend the classification with `output_format` and `operation` so it also picks the *block type*. Keep `router.py` as the fast-path for visitor questions and short-circuit refusals; the Analyzer kicks in only when intent is "ambiguous or data-shaped".

---

## E. Security & Role Gating (non-negotiable)

This is enforced in the **adapter layer** — the function that calls OrgLens — not in the prompt.

| Role | Allowed tools | UCC binding | PII handling |
|---|---|---|---|
| visitor | `stats`, `departments`, `locations`, `designations`, `clients/stats` (aggregate only) | none — any `ucc=` parameter is rejected | n/a (no PII surfaced) |
| client | + `client/by-ucc` (locked to verified UCC), `bo/client/{ucc}/*`, `mf/client/by-pan/{pan}` (locked to verified PAN), folios, sips, transactions | adapter **overwrites** any `ucc` in the LLM tool call with the session's verified UCC; mismatch → security event | PAN/Aadhaar/account-no masked in chat output, full value only in admin drawers |
| employee | + `employees`, `employee/by-code`, `employees/{identifier}`, `org-tree`, `bo/clients`, `mf/clients`, `bo/client/{ucc}/*` (any UCC, but with RM-relationship check at adapter), `mf/folios`, `mf/funds`, `bo/stats`, `mf/stats` | when calling `bo/client/{ucc}/*`, adapter first calls `bo/clients?rm={emp.code}` and verifies the UCC is in that list; otherwise refuse | PAN/Aadhaar/account masked in chat; visible in admin drawers; `*_ctc` fields never echoed to chat for any role |
| admin | full registry | none | full payloads in admin surfaces only |

### Adapter-layer checks (sketch)

```python
async def adapter_bo_client_360(session, ucc: str) -> ToolResult:
    role = session["role_state"]
    if role == "client" and ucc != session["verified_ucc"]:
        await log_security_event(kind="cross_ucc_attempt", ...)
        raise ToolForbidden("client_can_only_see_own_ucc")
    if role == "employee":
        their = await orglens.bo_clients(rm=session["employee_code"])
        if ucc not in {c["client_code"] for c in their["clients"]}:
            raise ToolForbidden("rm_does_not_own_this_ucc")
    raw = await orglens.bo_client_360(ucc)
    return mask_pii_for_role(raw, role=role)
```

### What we won't expose to chat (ever)

* `employees:compensation` fields (`fixed_ctc`, `total_ctc`, `salary_structure`). Admin-only.
* Bank account number (full) — only last-4 in chat.
* PAN — only first 5 + last 1 in chat; admin drawer shows full.
* Aadhaar — already returned masked by OrgLens; we never unmask.
* Any `bo-crm` data (we can't fetch it anyway — see §6 of delta).

---

## F. Performance & Cost

### Caching layers (defence-in-depth)

| Layer | Scope | Lifetime | Purpose |
|---|---|---|---|
| In-process LRU | (tool_name, params_hash) | per `tool_spec.cache_ttl_seconds` (default 90 s, max 1 h) | Avoids redundant OrgLens hits within one turn / one session |
| Mongo `tool_call_cache` | (tool_name, params_hash) | 1 h hard TTL | Survives restarts; shared across pods once we scale |
| Question Analyzer cache | exact prompt | 5 min | Same user asking the same question twice doesn't pay the classifier twice |
| OrgLens chain cache (existing, Phase 19) | `(employee_id → manager chain)` | 1 h | Already in place, keep |

### LLM context budget

* With 35 tools, the function-calling schema alone is ~4 kB. Hub AI charges by token; we don't want that on every turn.
* Hence the **two-stage** design: Analyzer narrows to 3-5 tools, only those are serialised into the function-calling schema for the main pass.
* Net token cost per turn vs today: **+150 tokens** (analyzer in/out) **- 600 tokens** (no longer dumping 6 hand-written tool descriptions into every prompt). **Net win.**

### Latency budget

* Soft cap: **6 s** wall-clock to first token of the user-facing reply.
* Hard cap: **15 s** then we fall back to the existing "I'm working on this — try a simpler question" copy.
* Anticipated turn budget:
  * Analyzer call: ~600 ms (small model, prompt-only).
  * Tool calls (parallel): ~900 ms p95.
  * Main LLM pass: ~2 s for chart/table answers, ~3 s for narrative.
  * Render + transport: ~200 ms.
  * **Total p95 ≈ 3.7 s — comfortable.**

---

## G. Telemetry & Admin Observability

### Collection — `tool_calls`

```json
{
  "_id": ObjectId,
  "turn_id": "<uuid>",                  // groups parallel calls within a turn
  "session_id": "<chat session>",
  "tool_name": "bo_client_360",
  "params_redacted": {"ucc": "***0778"}, // PAN/UCC redacted in this column
  "latency_ms": 832,
  "hit_cache": false,
  "ok": true,
  "error_kind": null,                   // forbidden|orglens_5xx|timeout|param_invalid
  "role_state": "client",
  "created_at": "<ISO>"
}
```

### Admin panel (new `Tools` tab — siblings of Phase 19.2 Email Relay)

* **Top 10 tools by 7-day call volume** — bar chart, link to the manifest stanza
* **Per-tool p50 / p95 latency, last 7 days** — line chart
* **Cache hit rate per tool** — table
* **Failure rate per tool + breakdown by `error_kind`** — table
* **Live tail of the last 20 tool calls** — turn-grouped, with params (redacted) and latency
* **OrgLens spec drift detector** — green/red badge per tool; red when our manifest's field projections no longer match the live spec (computed nightly)
* **"Replay this turn"** button on each row in the live tail → re-runs the same tool with the same params, useful when an OrgLens deploy breaks something

---

## H. Open questions / decisions to confirm before build

1. **Recharts vs VegaLite-via-React.** Recommendation = Recharts (lighter, fits our theme). Confirm?
2. **PNG export library.** Recommendation = matplotlib + kaleido. Confirm or prefer plotly-only?
3. **Question Analyzer model.** Recommendation = gpt-4o-mini via Hub AI for cost + latency. Confirm.
4. **Sequential tool-call cap = 5.** Confirm or prefer 3?
5. **bo-crm endpoints (branch economics, per-client PnL).** Request OrgLens to expose these to our API-key, or skip and document the limit? **Recommend: file the OrgLens ticket NOW, in parallel with the build, so it's not blocking.**
6. **Where the new "Tools" admin tab sits.** Sibling of the Phase 19.2 Email Relay tab, between Knowledge Base and SMTP/Email Relay. Confirm.

---

## I. Build pass order (when greenlit)

1. **Foundation** — write `backend/orglens_tools/manifest.yaml` (one stanza per accessible endpoint, ~35 entries) + `backend/orglens_tools/registry.py` + `adapter.py`. Add boot-time spec validator.
2. **Plumb the orchestrator** — add the Analyzer step; route tool calls through the new registry. Keep the existing 6 hard-coded paths running in parallel behind a feature flag (`PHASE_20_TOOLS_ENABLED=true`) so we can A/B.
3. **Block renderers** — `TableBlock` + `ChartBlock` + `DownloadBlock` first (cover ~95 % of the use cases). `ImageBlock` after, only if the question matrix shows real demand.
4. **Admin "Tools" tab** — registry browser, telemetry panels, spec-drift checker.
5. **Test pass** — work through `question_matrix.md` end-to-end; each question must hit the expected block type.
6. **Cut over** — flip the flag, retire the old 6 hand-written adapters, delete dead code.
