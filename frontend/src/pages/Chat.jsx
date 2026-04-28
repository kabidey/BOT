import { useEffect, useRef, useState } from "react";
import axios from "axios";
import { Send, ShieldCheck, AlertCircle } from "lucide-react";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;
const STORAGE_KEY = "smifs_session_id";

const SUGGESTIONS = [
  "I'm exploring wealth management for the first time.",
  "How do you approach tax-efficient portfolio construction?",
  "I have ₹2 Cr to deploy — where would we begin?",
];

export default function Chat() {
  const [sessionId, setSessionId] = useState(() => localStorage.getItem(STORAGE_KEY) || null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");
  const [health, setHealth] = useState(null);
  const listRef = useRef(null);

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
    return () => {
      cancelled = true;
    };
  }, []);

  // Auto-scroll
  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [messages, sending]);

  const send = async (textOverride) => {
    const text = (textOverride ?? input).trim();
    if (!text || sending) return;
    setErrorMsg("");
    setInput("");
    const userMsg = { role: "user", content: text };
    setMessages((prev) => [...prev, userMsg]);
    setSending(true);
    try {
      const { data } = await axios.post(`${API}/chat`, {
        session_id: sessionId,
        message: text,
      });
      if (data.session_id && data.session_id !== sessionId) {
        setSessionId(data.session_id);
        localStorage.setItem(STORAGE_KEY, data.session_id);
      }
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: data.reply, model: data.model },
      ]);
    } catch (e) {
      const detail = e?.response?.data?.detail || e.message || "Unknown error";
      setErrorMsg(detail);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content:
            "I'm momentarily unable to reach the advisory engine. Please try again shortly.",
          error: true,
        },
      ]);
    } finally {
      setSending(false);
    }
  };

  const onKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  const resetThread = () => {
    localStorage.removeItem(STORAGE_KEY);
    setSessionId(null);
    setMessages([]);
    setErrorMsg("");
  };

  return (
    <div className="smifs-shell" data-testid="smifs-chat-shell">
      {/* Ambient gradient blobs */}
      <div className="smifs-bg-blob smifs-bg-blob--gold" aria-hidden />
      <div className="smifs-bg-blob smifs-bg-blob--teal" aria-hidden />
      <div className="smifs-grain" aria-hidden />

      {/* Header */}
      <header className="smifs-header">
        <div className="smifs-brand">
          <div className="smifs-mono" aria-hidden>S</div>
          <div>
            <h1 className="smifs-title" data-testid="smifs-title">SMIFS Wealth Advisor</h1>
            <p className="smifs-subtitle">Lead Wealth-Engagement Agent · Phase 0</p>
          </div>
        </div>

        <div className="smifs-status" data-testid="health-pill">
          {health?.llm_reachable ? (
            <>
              <ShieldCheck size={14} strokeWidth={2.25} />
              <span>Engine online{health?.model ? ` · ${health.model}` : ""}</span>
            </>
          ) : (
            <>
              <AlertCircle size={14} strokeWidth={2.25} />
              <span>{health ? "Engine unreachable" : "Checking engine…"}</span>
            </>
          )}
        </div>
      </header>

      {/* Conversation */}
      <main className="smifs-main">
        <div className="smifs-thread" ref={listRef} data-testid="message-list">
          {messages.length === 0 && (
            <div className="smifs-welcome" data-testid="welcome-card">
              <p className="smifs-eyebrow">Private advisory · Confidential</p>
              <h2 className="smifs-welcome-title">
                A considered conversation about your wealth.
              </h2>
              <p className="smifs-welcome-body">
                Share your goals, time horizon, or a question — your dedicated agent will
                respond with the precision of a senior wealth manager.
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

          {messages.map((m, i) => (
            <div
              key={i}
              className={`smifs-msg ${m.role === "user" ? "smifs-msg--user" : "smifs-msg--bot"} ${
                m.error ? "smifs-msg--error" : ""
              }`}
              data-testid={`msg-${m.role}-${i}`}
            >
              <div className="smifs-msg-meta">
                {m.role === "user" ? "You" : "Advisor"}
                {m.model ? <span className="smifs-msg-model"> · {m.model}</span> : null}
              </div>
              <div className="smifs-msg-bubble">{m.content}</div>
            </div>
          ))}

          {sending && (
            <div className="smifs-msg smifs-msg--bot" data-testid="typing-indicator">
              <div className="smifs-msg-meta">Advisor</div>
              <div className="smifs-msg-bubble smifs-typing">
                <span /><span /><span />
              </div>
            </div>
          )}
        </div>

        {errorMsg && (
          <div className="smifs-error" data-testid="error-banner">
            <AlertCircle size={14} /> {errorMsg}
          </div>
        )}

        {/* Composer */}
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
            disabled={!input.trim() || sending}
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
    </div>
  );
}
