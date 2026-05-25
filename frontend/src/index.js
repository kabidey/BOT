import React from "react";
import ReactDOM from "react-dom/client";
import "@/branding-cleanup.css";
import "@/index.css";
import App from "@/App";
import { bootstrapFingerprint } from "@/lib/fingerprint";

// Phase 22 — silent device-fingerprint bootstrap. Fires-and-forgets: the
// global axios default headers are installed as soon as the visitorId
// resolves (typically within ~50ms of cache hit, ~250ms cold). Failures
// are silent by design.
bootstrapFingerprint();

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
