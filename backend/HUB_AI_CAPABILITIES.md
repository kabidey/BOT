# Hub AI Capabilities (probed Apr 2026)

Base URL: `https://ai.superclue.io/api/v1`
Auth: `Authorization: Bearer <LLMHUB_API_KEY>`

## ✅ Confirmed working

### `GET /models` — model catalog
39 models across 9 providers. Notable additions vs the older catalog:

| provider | models |
|---|---|
| openai | gpt-4o, gpt-4o-mini, gpt-4-turbo, **gpt-4.1, gpt-4.1-mini, gpt-4.1-nano**, **o1, o1-mini, o1-pro, o3, o3-mini, o4-mini**, gpt-3.5-turbo, dall-e-3, dall-e-2, gpt-image-1, sora-2, sora-2-pro |
| anthropic | claude-sonnet-4-20250514, **claude-sonnet-4-6-20260205**, claude-haiku-4-5-20251001, claude-opus-4-20250514, **claude-opus-4-6-20260205**, claude-3-5-sonnet-20241022 |
| groq | **llama-3.3-70b-versatile**, llama-3.1-8b-instant, llama-3.1-70b-versatile, mixtral-8x7b-32768, gemma2-9b-it |
| local | gemma-4-e4b, qwen2.5-coder-14b, deepseek-coder-v2-lite-16b, deepseek-r1-distill-qwen-14b |
| deepseek | deepseek-chat, deepseek-reasoner |

Each model includes `pricing` (per-1M-token USD).

### Streaming via `stream: true` ✅
Returns `text/event-stream` SSE frames. Verified curl:
```bash
curl -N -X POST -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"auto","stream":true,"max_tokens":40,"messages":[{"role":"user","content":"Say hi"}]}' \
  "$BASE/chat/completions"
```
Response shape per frame:
```
data: {"id":"chatcmpl-…","object":"chat.completion.chunk","model":"gemma-4-e4b","choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}
```
**No** terminating `data: [DONE]` sentinel — stream just closes when finish_reason flips to non-null.

### `context_chunks` (native RAG injection) ✅
Hub accepts a `context_chunks` array alongside `messages`. The chunks are silently merged into the model's context with associated metadata (`source`, `title`, `id`, `section` are all preserved — the model can name the source on demand). Compatible with `stream: true`.
```bash
curl -X POST … -d '{
  "model":"auto",
  "messages":[{"role":"user","content":"What is the AIF minimum?"}],
  "context_chunks":[
    {"text":"SEBI mandates Rs 1 crore per investor across all AIF categories.",
     "source":"aif_overview","section":"Minimum Ticket Size","id":"chunk-42"}
  ]
}'
```
Verified: prompt_tokens jump from 13 → 79+ when chunks attached; the model answers using ONLY chunk content. **Field aliases tested:** `chunks` and `documents` are silently ignored (no token bump). Only `context_chunks` is honored.

### `response_format: {"type":"json_object"}` ✅
Honored by all `auto`-routed models including local gemma. Used by the router for strict JSON output.

## ❌ Not supported (probed, returned 404 or silently ignored)

| Probe | Result |
|---|---|
| `GET /openapi.json`, `GET /docs`, `GET /` | 404 — no public spec |
| `GET/POST /embeddings` | 404 — embeddings endpoint not live. Stay on local `sentence-transformers/all-MiniLM-L6-v2`. |
| `routing_hint: "fast"\|"quality"\|"cheap"` | Silently ignored — `auto` always landed on `gemma-4-e4b` regardless of hint. The `"routing"` field in responses comes back as empty string. |
| `task_type: "classification"\|"chat"\|"rag"` | Silently ignored. |
| `quality: "high"` | Silently ignored. |
| `tools: [{type:"function",…}]` | 200 OK, but the model never emits a `tool_calls` array — it just narrates. Function-calling is not actually wired through `auto`. Could revisit per-model (e.g. force `gpt-4o-mini`) if we ever need real tools. |
| `POST /route`, `/router`, `/classify`, `/intent` | 404 — no separate intent-classification endpoint. Continue using LLM-as-classifier via `/chat/completions` with `response_format: json_object`. |

## Decisions for SMIFS Wealth Agent

- **Integrate `stream: true`** end-to-end so the chat UI types out tokens progressively.
- **Integrate `context_chunks`** for the RAG agent — replaces our system-prompt KB block. Quality regression-tested below.
- **Skip routing hints, embeddings, tools** — not wired through the router yet; revisit when Hub publishes a docs/spec.
- **No `gpt-4o-mini`** in chains; keep `auto` as primary, `llama-3.3-70b-versatile` and `gemma-4-E4B` as cost-controlled fallbacks.
