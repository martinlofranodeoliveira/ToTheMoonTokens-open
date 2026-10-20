import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { SentryErrorBoundary, initFrontendObservability } from "./observability";
import "./styles.css";

initFrontendObservability();

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <SentryErrorBoundary fallback={<div className="empty-state">Something went wrong</div>}>
      <App />
    </SentryErrorBoundary>
  </React.StrictMode>,
);
