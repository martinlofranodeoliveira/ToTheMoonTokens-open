import * as Sentry from "@sentry/react";

export function initFrontendObservability() {
  const dsn = import.meta.env.VITE_SENTRY_DSN;
  if (!dsn) return;
  Sentry.init({
    dsn,
    environment: import.meta.env.VITE_APP_ENV || import.meta.env.MODE,
    tracesSampleRate: Number(import.meta.env.VITE_SENTRY_TRACES_SAMPLE_RATE || 0),
  });
}

export const SentryErrorBoundary = Sentry.ErrorBoundary;
