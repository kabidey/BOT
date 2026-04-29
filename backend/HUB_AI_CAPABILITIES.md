# Hub AI Capabilities (re-probed Apr 2026)

Base URL: `https://ai.superclue.io/api/v1`
Auth: `Authorization: Bearer <LLMHUB_API_KEY>`

## ✅ Confirmed working (current re-probe)

### `POST /embeddings` ✅ NEW
Live as of this re-probe. Returns OpenAI-compatible response shape.

| Model | Dim | Pricing (USD / 1M input tokens) |
|---|---|---|
| `text-embedding-3-small` | **1536** | **$0.02** ← best price/quality |
| `text-embedding-3-large` | 3072 | $0.13 |
| `text-embedding-ada-002` | 1536 | $0.10 (legacy) |
| `auto` | 1536 (resolves to text-embedding-3-small) | varies |

Verified curl:
```bash
curl -X POST -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"input":["text 1","text 2"],"model":"text-embedding-3-small"}' \
  "$BASE/embeddings"
# returns: {object,data:[{embedding:[1536 floats]}, ...], model, usage:{prompt_tokens,total_tokens}, cost, balance_inr, latency_ms}
```

### Streaming `stream: true` ✅ (unchanged)
SSE `text/event-stream`, no `[DONE]` sentinel — TCP closes after final chunk.

### `context_chunks` (native RAG injection) ✅ (unchanged)
Hub natively merges chunks into context with metadata preserved.

### `response_format: {"type":"json_object"}` ✅ (unchanged)
Honored across models including local gemma.

### Native function-calling / `tools` ✅ NEW (with caveat)
**Works perfectly when a tool-capable model is named explicitly.** Verified return shape:
```json
{
  "model": "llama-3.3-70b-versatile",
  "choices": [{
    "finish_reason": "tool_calls",
    "message": {
      "content": "",
      "tool_calls": [{"type":"function","function":{"name":"fetch_market_data","arguments":"{\"symbol\":\"RELIANCE\"}"}}]
    }
  }]
}
```
Confirmed-working models for tool-calling:
- `llama-3.3-70b-versatile` (groq) ← chosen as router primary (cheap, fast, native tools)
- `gpt-4.1-mini`, `gpt-4.1-nano`, `gpt-4o-mini` (openai)
- `claude-haiku-4-5-20251001` (anthropic — `finish_reason: "tool_use"`, but Hub normalises into the same `tool_calls` shape)

**Does NOT work:**
- `model:"auto"` → silently routes to `gemma-4-e4b`, which emits raw chat-template tokens like `<|tool_call>call:fetch_market_data{symbol:RELIANCE}<tool_call|>` instead of a parsed `tool_calls` array.
- `claude-3-5-sonnet-20241022` was rerouted to gemma in our probe (provider quota?), producing the same broken tokens.

**Implication:** tool-calling REQUIRES naming a tool-capable model directly. Don't use `auto` when sending `tools`.

## ✅ Routing hints (re-probed Apr 2026 — NOW LIVE)
**Confirmed working field:** `routing_hint` (top-level, **must be a dict**; sending a string returns `HTTP 422 dict_type` Pydantic error). Synonyms: `route` (also expects dict). Top-level `prefer:"fast"` also works.

**Vocabulary tested — Hub recognises exactly ONE keyword today:**

| Sent | Resolved model | Provider | Honored? |
|---|---|---|---|
| `routing_hint:{"prefer":"fast"}` | `llama-3.3-70b-versatile` | groq | ✅ |
| `routing_hint:{"prefer":"quality"\|"cheap"\|"premium"\|"speed"\|"reasoning"\|"smart"\|"groq"\|"openai"\|"claude"\|"llama"\|"gpt-4"\|...}` | `gemma-4-e4b` | local | ❌ ignored (defaults) |
| `routing_hint:{"task":...}`, `{"quality":...}`, `{"latency":...}`, `{"intent":...}`, `{"cost":...}`, `{"tier":...}`, `{"provider":...}` | `gemma-4-e4b` | local | ❌ all ignored |

So Hub today honours **only** `prefer:"fast"` → groq llama-3.3-70b-versatile. Everything else stays on the local default. Hub also echoes `routing_resolved` in the response confirming the hint received (useful for telemetry).

**Verified curl:**
```bash
curl -X POST -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"Compare AIF and PMS"}],"routing_hint":{"prefer":"fast"}}' \
  "$BASE/chat/completions"
# {"model":"llama-3.3-70b-versatile","provider":"groq","routing":"auto:groq/llama-3.3-70b-versatile",
#  "routing_resolved":{"prefer":"fast"}, ...}
```

**Embeddings:** `routing_hint` is accepted but produces no observable change — `model:"auto"` always resolves to `text-embedding-3-small`.

## ❌ Still not supported

### `GET /openapi.json`, `/docs`, `/` | 404 (unchanged)
### `POST /route`, `/classify`, `/intent` | 404 (unchanged)
