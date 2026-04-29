import { useEffect, useRef, useState, useCallback } from "react";
import axios from "axios";
import { Send, ShieldCheck, AlertCircle, Sparkles, LogOut, User, Lock, Briefcase, Clock, PlayCircle } from "lucide-react";

import TextBlock from "@/components/blocks/TextBlock";
import FormBlock from "@/components/blocks/FormBlock";
import MarketCardBlock from "@/components/blocks/MarketCardBlock";
import ClientCardBlock from "@/components/blocks/ClientCardBlock";
import EmployeeCardBlock from "@/components/blocks/EmployeeCardBlock";
import EscalationBlock from "@/components/blocks/EscalationBlock";
import ResumeOfferBlock from "@/components/blocks/ResumeOfferBlock";
import DirectoryCardBlock from "@/components/blocks/DirectoryCardBlock";
import DirectoryListBlock from "@/components/blocks/DirectoryListBlock";
import OrgStatsCardBlock from "@/components/blocks/OrgStatsCardBlock";
import ReportingChainCardBlock from "@/components/blocks/ReportingChainCardBlock";

const PAN_RE = /\b([A-Za-z]{5}[0-9]{4}[A-Za-z])\b/g;
const maskPanInText = (s) => (s || "").replace(PAN_RE, (m) => `XXXXX${m.slice(5, 9)}X`);

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;
const STORAGE_KEY_DEFAULT = "smifs_session_id";
const STORAGE_KEY_EMBED = "mackertich_embed_session_id";

const DEFAULT_SUGGESTIONS = [
  "Tell me about Mackertich ONE",
  "What is the minimum ticket size for an AIF?",
  "I'm interested in investing in NCDs",
];

export default function Chat({ embedded = false }) {
  const STORAGE_KEY = embedded ? STORAGE_KEY_EMBED : STORAGE_KEY_DEFAULT;
  const [sessionId, setSessionId] = useState(() => localStorage.getItem(STORAGE_KEY) || null);
  // messages: [{role, blocks?, content?, citations?, error?, intent?, model?}]
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [statusLabel, setStatusLabel] = useState("");
  const [errorMsg, setErrorMsg] = useState("");
  const [health, setHealth] = useState(null);
  const [activeCitation, setActiveCitation] = useState(null); // { msgIdx, citIdx }
  const [client, setClient] = useState(null); // {name, code, type} when verified
  const [identity, setIdentity] = useState(null); // full identity blob (employee|client)
  const [hydrating, setHydrating] = useState(false);
  const [widgetCfg, setWidgetCfg] = useState(null); // /api/widget/config response (embed mode)
  const listRef = useRef(null);
  const abortRef = useRef(null);
  const SUGGESTIONS = (widgetCfg && widgetCfg.suggestion_chips && widgetCfg.suggestion_chips.length)
    ? widgetCfg.suggestion_chips
    : DEFAULT_SUGGESTIONS;

  // Embed mode: fetch widget config to apply theme + branding
  useEffect(() => {
    if (!embedded) return;
    let cancelled = false;
    (async () => {
      try {
        const { data } = await axios.get(`${API}/widget/config`);
        if (!cancelled) setWidgetCfg(data);
      } catch (e) { /* fall back to defaults */ }
    })();
    return () => { cancelled = true; };
  }, [embedded]);

  // Apply theme via CSS vars on the document root when embedded
  useEffect(() => {
    if (!embedded || !widgetCfg) return;
    const root = document.documentElement;
    const t = widgetCfg.theme || {};
    const setVar = (k, v) => v && root.style.setProperty(k, v);
    setVar("--smifs-bg-primary", t.primary);
    setVar("--smifs-accent", t.accent);
    setVar("--smifs-bg-paper", t.background);
    setVar("--smifs-user-bubble", t.user_bubble);
    setVar("--smifs-asst-bubble", t.assistant_bubble);
    setVar("--smifs-text", t.text);
    setVar("--smifs-header-bg", t.header_bg);
    setVar("--smifs-header-text", t.header_text);
    document.body.classList.add("smifs-embed-body");
    return () => { document.body.classList.remove("smifs-embed-body"); };
  }, [embedded, widgetCfg]);

  // Notify parent (widget.js) when a new assistant message arrives, for unread badge
  useEffect(() => {
    if (!embedded) return;
    if (!messages.length) return;
    const last = messages[messages.length - 1];
    if (last && last.role === "assistant" && !last.streaming) {
      try { window.parent.postMessage({ type: "mackertich:assistant_message" }, "*"); } catch (_) {}
    }
  }, [embedded, messages]);

  // ---- Phase 7 — 2-min idle watcher ----
  const [idleState, setIdleState] = useState("fresh"); // "fresh" | "warning" | "expired"
  const idleTimersRef = useRef({ warn: null, expire: null });
  const resetIdleTimers = useCallback(() => {
    if (idleTimersRef.current.warn) clearTimeout(idleTimersRef.current.warn);
    if (idleTimersRef.current.expire) clearTimeout(idleTimersRef.current.expire);
    setIdleState("fresh");
    idleTimersRef.current.warn = setTimeout(() => setIdleState("warning"), 110_000);
    idleTimersRef.current.expire = setTimeout(() => setIdleState("expired"), 120_000);
  }, []);
  // Reset on mount and whenever messages change (any turn counts as activity).
  useEffect(() => {
    resetIdleTimers();
    return () => {
      if (idleTimersRef.current.warn) clearTimeout(idleTimersRef.current.warn);
      if (idleTimersRef.current.expire) clearTimeout(idleTimersRef.current.expire);
    };
  }, [resetIdleTimers, messages.length]);

  // Fetch rehydration candidates when the user clicks "Resume" after expiry
  // (session was frozen client-side; see if any prior sessions are recoverable).
  const offerResumeAfterExpiry = useCallback(async () => {
    resetIdleTimers();
    if (!sessionId) return;
    try {
      const { data } = await axios.get(`${API}/sessions/${sessionId}/rehydration_candidates`);
      const candidates = data?.candidates || [];
      if (candidates.length > 0) {
        setMessages((prev) => ([...prev, {
          role: "assistant",
          intent: "RESUME_OFFER",
          blocks: [{ type: "resume_offer", data: { candidates } }],
          citations: [],
        }]));
      } else {
        setErrorMsg("No prior conversation to restore — please continue below.");
        setTimeout(() => setErrorMsg(""), 4000);
      }
    } catch (_) { /* non-fatal */ }
  }, [sessionId, resetIdleTimers]);

  // Resume / Decline handlers
  const handleResume = useCallback(async (priorSessionId) => {
    if (!sessionId || !priorSessionId) return;
    try {
      const { data } = await axios.post(`${API}/sessions/${sessionId}/resume`,
        { prior_session_id: priorSessionId });
      // Merged session — rebuild messages from the returned history
      const restored = (data.history || []).map((h) => {
        if (h.role === "user") return { role: "user", content: h.text || "" };
        return {
          role: "assistant",
          blocks: h.blocks || [{ type: "text", text: "" }],
          citations: h.citations || [],
          intent: h.intent,
          model: h.model,
        };
      });
      setMessages(restored);
      if (data.identity) setIdentity(data.identity);
      if (data.client) setClient(data.client);
      resetIdleTimers();
    } catch (e) {
      const d = e?.response?.data?.detail || e.message;
      setErrorMsg(`Could not resume: ${d}`);
    }
  }, [sessionId, resetIdleTimers]);

  const handleDecline = useCallback(async () => {
    if (!sessionId) return;
    try {
      await axios.post(`${API}/sessions/${sessionId}/decline_resume`);
      // Strip any resume_offer blocks from the current view
      setMessages((prev) => prev.map((m) => (
        m.role === "assistant" && Array.isArray(m.blocks)
          ? { ...m, blocks: m.blocks.filter((b) => b.type !== "resume_offer") }
          : m
      )));
    } catch (_) { /* non-fatal */ }
  }, [sessionId]);

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

  // Rehydrate chat thread on mount if a session_id is present in localStorage
  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    (async () => {
      setHydrating(true);
      try {
        const { data } = await axios.get(`${API}/sessions/${sessionId}`);
        if (cancelled) return;
        if (data.client) setClient(data.client);
        else setClient(null);
        if (data.identity) setIdentity(data.identity);
        else setIdentity(null);
        const restored = (data.history || []).map((h) => {
          if (h.role === "user") return { role: "user", content: h.text };
          return {
            role: "assistant",
            blocks: h.blocks || [],
            citations: h.citations || [],
            intent: h.intent,
            model: h.model,
          };
        });
        setMessages(restored);
      } catch (e) {
        // 404 → stale localStorage, clear it
        if (e?.response?.status === 404) {
          localStorage.removeItem(STORAGE_KEY);
          setSessionId(null);
        }
      } finally {
        if (!cancelled) setHydrating(false);
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
    // If the previous assistant message asked for PAN, auto-mask the user's submitted text in local history
    const lastBotMsg = [...messages].reverse().find((m) => m.role === "assistant" && !m.streaming);
    const isPanReply = lastBotMsg?.intent === "AUTH_PAN_REQUEST" || lastBotMsg?.intent === "AUTH_PAN_RETRY";
    const displayText = isPanReply ? maskPanInText(text) : text;
    // Push the user message AND a placeholder assistant turn that will receive streamed tokens.
    const turnId = `t-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    setMessages((prev) => [...prev, { role: "user", content: displayText }, {
      role: "assistant",
      blocks: [{ type: "text", text: "", grounded: false }],
      citations: [],
      streaming: true,
      turnId,
    }]);
    setStreaming(true);
    setStatusLabel("Routing your question…");

    const controller = new AbortController();
    abortRef.current = controller;

    const updateStreamingTurn = (mutator) => {
      setMessages((prev) => {
        const idx = prev.findIndex((m) => m.turnId === turnId);
        if (idx === -1) return prev;
        const copy = prev.slice();
        copy[idx] = mutator(copy[idx]);
        return copy;
      });
    };

    const appendStreamingToken = (token) => {
      updateStreamingTurn((target) => {
        if (!target.streaming) return target;
        const blocks = target.blocks.slice();
        const firstText = blocks.findIndex((b) => b.type === "text");
        const i = firstText === -1 ? 0 : firstText;
        const existing = blocks[i]?.text || "";
        blocks[i] = { ...(blocks[i] || { type: "text" }), text: existing + token };
        return { ...target, blocks };
      });
    };

    const setStreamingCitations = (cits) => {
      updateStreamingTurn((target) => target.streaming ? { ...target, citations: cits } : target);
    };

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
          } else if (eventName === "token") {
            if (data?.text) appendStreamingToken(data.text);
          } else if (eventName === "citations") {
            if (Array.isArray(data)) setStreamingCitations(data);
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
      // Refresh verified identity if intent indicates an auth state change
      if (finalResult.intent && finalResult.intent.startsWith("AUTH_")) {
        try {
          const sid = finalResult.session_id || sessionId;
          if (sid) {
            const { data: sess } = await axios.get(`${API}/sessions/${sid}`);
            setClient(sess.client || null);
            setIdentity(sess.identity || null);
          }
        } catch (_) { /* non-fatal */ }
      }
      // Replace the streaming placeholder with the authoritative final payload.
      setMessages((prev) => {
        const idx = prev.findIndex((m) => m.turnId === turnId);
        if (idx === -1) return prev;
        const copy = prev.slice();
        copy[idx] = {
          role: "assistant",
          blocks: finalResult.blocks || [],
          citations: finalResult.citations || [],
          intent: finalResult.intent,
          model: finalResult.model,
          trace: finalResult.trace,
        };
        return copy;
      });
    } catch (e) {
      if (e.name === "AbortError") return;
      const detail = e.message || "Unknown error";
      setErrorMsg(detail);
      setMessages((prev) => {
        const idx = prev.findIndex((m) => m.turnId === turnId);
        if (idx === -1) return [...prev, {
          role: "assistant", error: true,
          blocks: [{ type: "text", text: "I'm momentarily unable to reach the advisory engine. Please try again shortly." }],
        }];
        const copy = prev.slice();
        copy[idx] = {
          role: "assistant", error: true,
          blocks: [{ type: "text", text: "I'm momentarily unable to reach the advisory engine. Please try again shortly." }],
        };
        return copy;
      });
    } finally {
      setStreaming(false);
      setStatusLabel("");
      abortRef.current = null;
    }
  }, [sessionId, messages]);

  const send = (textOverride) => {
    const text = (textOverride ?? input).trim();
    if (!text || streaming) return;
    if (idleState === "expired") return; // composer locked until user clicks Resume
    resetIdleTimers();
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
    setClient(null);
  };

  const signOut = async () => {
    if (!sessionId) return;
    try {
      await axios.post(`${API}/sessions/${sessionId}/signout`);
    } catch (_) { /* still proceed with local clear */ }
    setClient(null);
    // Also clear thread for a clean slate
    localStorage.removeItem(STORAGE_KEY);
    setSessionId(null);
    setMessages([]);
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
      case "employee_card":
        return <EmployeeCardBlock key={key} block={block} msgIdx={msgIdx} />;
      case "resume_offer":
        return (
          <ResumeOfferBlock
            key={key}
            block={block}
            onResume={(priorId) => handleResume(priorId)}
            onDecline={() => handleDecline()}
          />
        );
      case "directory_card":
        return <DirectoryCardBlock key={key} block={block} />;
      case "directory_list":
        return <DirectoryListBlock key={key} block={block} />;
      case "org_stats_card":
        return <OrgStatsCardBlock key={key} block={block} />;
      case "reporting_chain_card":
        return <ReportingChainCardBlock key={key} block={block} />;
      case "escalation_card":
        return <EscalationBlock key={key} block={block} msgIdx={msgIdx} onRequestCallback={requestCallback} />;
      default:
        return null;
    }
  };

  return (
    <div className={`smifs-shell ${embedded ? "smifs-shell--embed" : ""}`} data-testid="smifs-chat-shell">
      {!embedded && <div className="smifs-bg-blob smifs-bg-blob--gold" aria-hidden />}
      {!embedded && <div className="smifs-bg-blob smifs-bg-blob--teal" aria-hidden />}
      {!embedded && <div className="smifs-grain" aria-hidden />}

      <header className={`smifs-header ${embedded ? "smifs-header--embed" : ""}`}>
        <div className="smifs-brand">
          <div className="smifs-mono" aria-hidden>{embedded && widgetCfg?.bubble_icon ? widgetCfg.bubble_icon.slice(0, 2) : "M1"}</div>
          <div>
            <h1 className="smifs-title" data-testid="smifs-title">{embedded ? (widgetCfg?.brand_name || "Mackertich ONE Advisor") : "Mackertich ONE Advisor"}</h1>
            <p className="smifs-subtitle">{embedded ? (widgetCfg?.subtitle || "Wealth Management · SMIFS Ltd") : "Wealth Management · SMIFS Ltd"}</p>
          </div>
        </div>
        <div className="smifs-header-right">
          {client && (
            <div
              className={`smifs-client-chip smifs-client-chip--${(identity?.type) || (client.type) || "client"}`}
              data-testid="verified-chip"
              data-role={(identity?.type) || (client.type) || "client"}
            >
              <div className="smifs-client-chip-avatar" aria-hidden>
                {((identity?.type) || (client.type)) === "employee"
                  ? <Briefcase size={12} strokeWidth={2.5} />
                  : <User size={12} strokeWidth={2.5} />}
              </div>
              <div className="smifs-client-chip-text">
                <span className="smifs-client-chip-name">{(identity?.first_name) || (client.name?.split(" ")[0]) || "Client"}</span>
                <span className="smifs-client-chip-state">
                  <ShieldCheck size={10} strokeWidth={2.5} />
                  {((identity?.type) || (client.type)) === "employee" ? "EMP · Verified" : "Verified"}
                </span>
              </div>
              <button
                type="button"
                className="smifs-client-chip-out"
                onClick={signOut}
                data-testid="sign-out-button"
                aria-label="Sign out"
                title="Sign out"
              >
                <LogOut size={12} strokeWidth={2.25} />
              </button>
            </div>
          )}
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
          {embedded && (
            <button
              className="smifs-embed-close"
              data-testid="embed-close-button"
              onClick={() => { try { window.parent.postMessage({ type: "mackertich:close" }, "*"); } catch (_) {} }}
              aria-label="Close chat"
            >×</button>
          )}
        </div>
      </header>

      <main className="smifs-main">
        <div className="smifs-thread" ref={listRef} data-testid="message-list">
          {hydrating && messages.length === 0 && (
            <div className="smifs-hydrating" data-testid="hydrating">
              <Sparkles size={14} strokeWidth={2.25} />
              <span>Restoring your conversation…</span>
            </div>
          )}
          {messages.length === 0 && !hydrating && (
            <div className="smifs-welcome" data-testid="welcome-card">
              <p className="smifs-eyebrow">Private advisory · Confidential</p>
              <h2 className="smifs-welcome-title">{embedded && widgetCfg?.welcome_message ? widgetCfg.welcome_message : "A considered conversation about your wealth."}</h2>
              {!embedded && (
                <p className="smifs-welcome-body">
                  Our multi-agent advisor routes your question to the right specialist —
                  research, market data, your account, or our human team — and grounds every
                  product fact in the Mackertich ONE knowledge base.
                </p>
              )}
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
            const isStreamingTurn = !!m.streaming;
            const firstTextBlock = (m.blocks || []).find((b) => b.type === "text");
            const hasStreamedText = isStreamingTurn && !!(firstTextBlock?.text);
            return (
              <div
                key={i}
                className={`smifs-msg smifs-msg--bot ${m.error ? "smifs-msg--error" : ""} ${isStreamingTurn ? "smifs-msg--streaming" : ""}`}
                data-testid={`msg-assistant-${i}`}
                data-intent={m.intent || ""}
              >
                <div className="smifs-msg-meta">
                  Advisor
                  {m.intent ? <span className="smifs-msg-intent" data-testid={`msg-intent-${i}`}> · {m.intent.replace(/_/g, " ").toLowerCase()}</span> : null}
                  {m.model ? <span className="smifs-msg-model"> · {m.model}</span> : null}
                  {isStreamingTurn ? <span className="smifs-msg-model" data-testid="streaming-tag"> · streaming</span> : null}
                </div>
                {isStreamingTurn && !hasStreamedText ? (
                  <div className="smifs-msg-bubble smifs-streaming" data-testid={`streaming-spinner-${i}`}>
                    <Sparkles size={13} strokeWidth={2.25} />
                    <span className="smifs-streaming-label" data-testid="streaming-label">{statusLabel || "Thinking…"}</span>
                    <span className="smifs-streaming-dots"><span /><span /><span /></span>
                  </div>
                ) : (
                  <div className="smifs-blocks">
                    {(m.blocks || []).map((b, bi) => renderBlock(b, bi, i, m))}
                    {hasStreamedText && (
                      <span className="smifs-caret" aria-hidden data-testid="streaming-caret" />
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {errorMsg && (
          <div className="smifs-error" data-testid="error-banner">
            <AlertCircle size={14} /> {errorMsg}
          </div>
        )}

        {(() => {
          const lastBot = [...messages].reverse().find((m) => m.role === "assistant" && !m.streaming);
          const sensitive = lastBot?.intent === "AUTH_PAN_REQUEST" || lastBot?.intent === "AUTH_PAN_RETRY";
          const locked = idleState === "expired";
          const warning = idleState === "warning";
          return (
            <div className={`smifs-composer-wrap ${sensitive ? "smifs-composer-wrap--secure" : ""}`}>
              {warning && !locked && (
                <div className="smifs-idle-warning" data-testid="idle-warning">
                  <Clock size={12} strokeWidth={2.5} />
                  <span>Still there? This session will freeze in ~10 seconds due to inactivity.</span>
                </div>
              )}
              {locked && (
                <div className="smifs-idle-lockout" data-testid="idle-lockout" role="status">
                  <div className="smifs-idle-lockout-text">
                    <Lock size={12} strokeWidth={2.5} />
                    <span>Session paused after 2 minutes of inactivity. Continue to resume.</span>
                  </div>
                  <button
                    type="button"
                    className="smifs-idle-resume-btn"
                    onClick={offerResumeAfterExpiry}
                    data-testid="idle-resume-btn"
                  >
                    <PlayCircle size={12} strokeWidth={2.5} /> Resume
                  </button>
                </div>
              )}
              {sensitive && !locked && (
                <div className="smifs-secure-hint" data-testid="secure-input-hint">
                  <Lock size={11} strokeWidth={2.5} /> Secure entry · we'll mask this immediately
                </div>
              )}
              <div
                className={`smifs-composer ${sensitive ? "smifs-composer--secure" : ""} ${locked ? "smifs-composer--locked" : ""}`}
                data-secure={sensitive ? "true" : "false"}
                data-locked={locked ? "true" : "false"}
              >
                <textarea
                  value={input}
                  onChange={(e) => { setInput(e.target.value); if (idleState !== "expired") resetIdleTimers(); }}
                  onKeyDown={onKey}
                  onFocus={() => { if (idleState !== "expired") resetIdleTimers(); }}
                  placeholder={locked ? "Session paused — click Resume to continue" : (sensitive ? "Enter your PAN (e.g. ABCDE1234F)" : "Ask your wealth advisor…")}
                  rows={1}
                  className="smifs-input"
                  data-testid="chat-input"
                  inputMode="text"
                  autoComplete="off"
                  autoCapitalize={sensitive ? "characters" : "sentences"}
                  spellCheck={sensitive ? false : true}
                  disabled={locked}
                />
                <button
                  className="smifs-send"
                  onClick={() => send()}
                  disabled={!input.trim() || streaming || locked}
                  data-testid="send-button"
                  aria-label="Send message"
                >
                  <Send size={16} strokeWidth={2.25} />
                </button>
              </div>
            </div>
          );
        })()}

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
