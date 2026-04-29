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

## ❌ Still not supported

### Routing hints — silently ignored
Tested 15 candidate fields against `model:"auto"` with `"hi"`:
- `routing_hint`, `task`, `task_type`, `quality`, `priority`, `tier`, `cost_preference`, `route`, `meta.routing_hint`
- All produced identical resolved model (`gemma-4-e4b`).

The new response field `routing` (e.g. `"auto:local/gemma-4-e4b"`) is **prompt-content driven**, not hint driven. Probing 5 different prompt complexities only reroutes between local models (`gemma-4-e4b` vs `deepseek-coder-v2-lite-16b`); never to paid OpenAI/Anthropic. The "intelligent prompt routing engine" is real but **it routes by analysing the prompt, not by client hints.**

**Workaround for differentiation between router-task and chat-task models:** name the model directly per task (router → `llama-3.3-70b-versatile` for native tool calling; chat → `auto` so Hub picks the free local model for cost). This produces the requested `by_model` differentiation in the cost ledger without any client-side hint.

### `GET /openapi.json`, `/docs`, `/` | 404 (unchanged)
### `POST /route`, `/classify`, `/intent` | 404 (unchanged)
