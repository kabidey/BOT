import { useEffect, useRef, useState, useCallback } from "react";
import axios from "axios";
import { Send, ShieldCheck, AlertCircle, Sparkles } from "lucide-react";

import TextBlock from "@/components/blocks/TextBlock";
import FormBlock from "@/components/blocks/FormBlock";
import MarketCardBlock from "@/components/blocks/MarketCardBlock";
import ClientCardBlock from "@/components/blocks/ClientCardBlock";
import EscalationBlock from "@/components/blocks/EscalationBlock";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;
const STORAGE_KEY = "smifs_session_id";

const SUGGESTIONS = [
  "What is the minimum ticket size for an AIF?",
  "What's the price of RELIANCE?",
  "I'm interested in investing in NCDs",
];

export default function Chat() {
  const [sessionId, setSessionId] = useState(() => localStorage.getItem(STORAGE_KEY) || null);
  // messages: [{role, blocks?, content?, citations?, error?, intent?, model?}]
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [statusLabel, setStatusLabel] = useState("");
  const [errorMsg, setErrorMsg] = useState("");
  const [health, setHealth] = useState(null);
  const [activeCitation, setActiveCitation] = useState(null); // { msgIdx, citIdx }
  const listRef = useRef(null);
  const abortRef = useRef(null);

  // Health ping on mount
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { data } = await axios.get(`${API}/health`);
        if (!cancelled) setHealth(data);
      } catch (e) {
        if (!cancelled) setHealth({ status: "down", llm_reachable: false, detail: e.message });
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // Auto-scroll
  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [messages, streaming, statusLabel]);

  // Escape closes popover
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") setActiveCitation(null); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  /** Manual SSE parser over fetch+ReadableStream — EventSource doesn't support POST. */
  const sendStreaming = useCallback(async (text) => {
    setErrorMsg("");
    setActiveCitation(null);
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setStreaming(true);
    setStatusLabel("Routing your question…");

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const resp = await fetch(`${API}/agent/turn/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, message: text }),
        signal: controller.signal,
      });
      if (!resp.ok) {
        const errText = await resp.text();
        throw new Error(`HTTP ${resp.status}: ${errText.slice(0, 200)}`);
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let finalResult = null;
      let hadError = null;

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        // Split on double-newline (SSE event delimiter)
        let idx;
        while ((idx = buffer.indexOf("\n\n")) !== -1) {
          const raw = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          if (!raw.trim() || raw.startsWith(":")) continue; // comment / heartbeat
          let eventName = "message";
          let dataLines = [];
          for (const line of raw.split("\n")) {
            if (line.startsWith("event:")) eventName = line.slice(6).trim();
            else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
          }
          let data = null;
          if (dataLines.length) {
            try { data = JSON.parse(dataLines.join("\n")); } catch (_) { data = dataLines.join("\n"); }
          }
          if (eventName === "status") {
            if (data?.label) setStatusLabel(`${data.label}…`);
          } else if (eventName === "result") {
            finalResult = data;
          } else if (eventName === "error") {
            hadError = data?.detail || "Stream error";
          }
        }
      }

      if (hadError) throw new Error(hadError);
      if (!finalResult) throw new Error("Stream ended without a result");

      if (finalResult.session_id && finalResult.session_id !== sessionId) {
        setSessionId(finalResult.session_id);
        localStorage.setItem(STORAGE_KEY, finalResult.session_id);
      }
      setMessages((prev) => [...prev, {
        role: "assistant",
        blocks: finalResult.blocks || [],
        citations: finalResult.citations || [],
        intent: finalResult.intent,
        model: finalResult.model,
        trace: finalResult.trace,
      }]);
    } catch (e) {
      if (e.name === "AbortError") return;
      const detail = e.message || "Unknown error";
      setErrorMsg(detail);
      setMessages((prev) => [...prev, {
        role: "assistant",
        error: true,
        blocks: [{ type: "text", text: "I'm momentarily unable to reach the advisory engine. Please try again shortly." }],
      }]);
    } finally {
      setStreaming(false);
      setStatusLabel("");
      abortRef.current = null;
    }
  }, [sessionId]);

  const send = (textOverride) => {
    const text = (textOverride ?? input).trim();
    if (!text || streaming) return;
    setInput("");
    sendStreaming(text);
  };

  const onKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  const resetThread = () => {
    if (abortRef.current) abortRef.current.abort();
    localStorage.removeItem(STORAGE_KEY);
    setSessionId(null);
    setMessages([]);
    setErrorMsg("");
    setActiveCitation(null);
  };

  const onCitationClick = (msgIdx, citIdx) => {
    setActiveCitation((cur) =>
      cur && cur.msgIdx === msgIdx && cur.citIdx === citIdx ? null : { msgIdx, citIdx }
    );
  };

  const requestCallback = () => {
    if (streaming) return;
    sendStreaming("Please call me back at your earliest convenience.");
  };

  const renderBlock = (block, bi, msgIdx, msg) => {
    const key = `${msgIdx}-${bi}`;
    switch (block.type) {
      case "text":
        return (
          <TextBlock
            key={key}
            block={block}
            citations={msg.citations}
            onCitationClick={onCitationClick}
            msgIdx={msgIdx}
            activeCitationKey={activeCitation ? `${activeCitation.msgIdx}-${activeCitation.citIdx}` : null}
          />
        );
      case "form":
        return <FormBlock key={key} block={block} sessionId={sessionId} msgIdx={msgIdx} />;
      case "market_card":
        return <MarketCardBlock key={key} block={block} msgIdx={msgIdx} />;
      case "client_card":
        return <ClientCardBlock key={key} block={block} msgIdx={msgIdx} />;
      case "escalation_card":
        return <EscalationBlock key={key} block={block} msgIdx={msgIdx} onRequestCallback={requestCallback} />;
      default:
        return null;
    }
  };

  return (
    <div className="smifs-shell" data-testid="smifs-chat-shell">
      <div className="smifs-bg-blob smifs-bg-blob--gold" aria-hidden />
      <div className="smifs-bg-blob smifs-bg-blob--teal" aria-hidden />
      <div className="smifs-grain" aria-hidden />

      <header className="smifs-header">
        <div className="smifs-brand">
          <div className="smifs-mono" aria-hidden>S</div>
          <div>
            <h1 className="smifs-title" data-testid="smifs-title">SMIFS Wealth Advisor</h1>
            <p className="smifs-subtitle">Lead Wealth-Engagement Agent · Phase 2 · Multi-agent</p>
          </div>
        </div>
        <div className="smifs-status" data-testid="health-pill">
          {health?.llm_reachable ? (
            <>
              <ShieldCheck size={14} strokeWidth={2.25} />
              <span>
                Engine online{health?.last_chat_model ? ` · ${health.last_chat_model}` : ""}
                {health?.rag_chunks ? ` · ${health.rag_chunks} chunks` : ""}
              </span>
            </>
          ) : (
            <>
              <AlertCircle size={14} strokeWidth={2.25} />
              <span>{health ? "Engine unreachable" : "Checking engine…"}</span>
            </>
          )}
        </div>
      </header>

      <main className="smifs-main">
        <div className="smifs-thread" ref={listRef} data-testid="message-list">
          {messages.length === 0 && (
            <div className="smifs-welcome" data-testid="welcome-card">
              <p className="smifs-eyebrow">Private advisory · Confidential</p>
              <h2 className="smifs-welcome-title">A considered conversation about your wealth.</h2>
              <p className="smifs-welcome-body">
                Our multi-agent advisor routes your question to the right specialist —
                research, market data, your account, or our human team — and grounds every
                product fact in our internal SMIFS knowledge base.
              </p>
              <div className="smifs-suggestions">
                {SUGGESTIONS.map((s, i) => (
                  <button
                    key={i}
                    type="button"
                    className="smifs-suggestion"
                    onClick={() => send(s)}
                    data-testid={`suggestion-${i}`}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m, i) => {
            if (m.role === "user") {
              return (
                <div key={i} className="smifs-msg smifs-msg--user" data-testid={`msg-user-${i}`}>
                  <div className="smifs-msg-meta">You</div>
                  <div className="smifs-msg-bubble">{m.content}</div>
                </div>
              );
            }
            // assistant
            return (
              <div
                key={i}
                className={`smifs-msg smifs-msg--bot ${m.error ? "smifs-msg--error" : ""}`}
                data-testid={`msg-assistant-${i}`}
                data-intent={m.intent || ""}
              >
                <div className="smifs-msg-meta">
                  Advisor
                  {m.intent ? <span className="smifs-msg-intent" data-testid={`msg-intent-${i}`}> · {m.intent.replace(/_/g, " ").toLowerCase()}</span> : null}
                  {m.model ? <span className="smifs-msg-model"> · {m.model}</span> : null}
                </div>
                <div className="smifs-blocks">
                  {(m.blocks || []).map((b, bi) => renderBlock(b, bi, i, m))}
                </div>
              </div>
            );
          })}

          {streaming && (
            <div className="smifs-msg smifs-msg--bot" data-testid="streaming-status">
              <div className="smifs-msg-meta">Advisor</div>
              <div className="smifs-msg-bubble smifs-streaming">
                <Sparkles size={13} strokeWidth={2.25} />
                <span className="smifs-streaming-label" data-testid="streaming-label">{statusLabel || "Thinking…"}</span>
                <span className="smifs-streaming-dots"><span /><span /><span /></span>
              </div>
            </div>
          )}
        </div>

        {errorMsg && (
          <div className="smifs-error" data-testid="error-banner">
            <AlertCircle size={14} /> {errorMsg}
          </div>
        )}

        <div className="smifs-composer">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKey}
            placeholder="Ask your wealth advisor…"
            rows={1}
            className="smifs-input"
            data-testid="chat-input"
          />
          <button
            className="smifs-send"
            onClick={() => send()}
            disabled={!input.trim() || streaming}
            data-testid="send-button"
            aria-label="Send message"
          >
            <Send size={16} strokeWidth={2.25} />
          </button>
        </div>

        <div className="smifs-footer">
          <button
            type="button"
            onClick={resetThread}
            className="smifs-link"
            data-testid="reset-thread"
          >
            Start a new conversation
          </button>
          <span className="smifs-session" data-testid="session-id">
            {sessionId ? `session · ${sessionId.slice(0, 8)}` : "no session yet"}
          </span>
        </div>
      </main>

      {/* Citation popover */}
      {activeCitation && (() => {
        const m = messages[activeCitation.msgIdx];
        const c = m?.citations?.[activeCitation.citIdx];
        if (!c) return null;
        return (
          <>
            <div
              className="smifs-popover-scrim"
              onClick={() => setActiveCitation(null)}
              data-testid="citation-popover-scrim"
            />
            <aside className="smifs-popover" data-testid="citation-popover" role="dialog">
              <div className="smifs-popover-head">
                <div>
                  <p className="smifs-popover-eyebrow">Knowledge base passage</p>
                  <h3 className="smifs-popover-title">{c.doc_title}</h3>
                  <p className="smifs-popover-section">§{c.section} · relevance {c.score.toFixed(2)}</p>
                </div>
                <button
                  type="button"
                  className="smifs-popover-close"
                  onClick={() => setActiveCitation(null)}
                  data-testid="citation-popover-close"
                  aria-label="Close passage"
                >×</button>
              </div>
              <div className="smifs-popover-body">{c.text}</div>
            </aside>
          </>
        );
      })()}
    </div>
  );
}
