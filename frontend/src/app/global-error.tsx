"use client";

/**
 * Last-resort error page for errors in the root layout itself. It renders its own document,
 * so it uses plain inline styles in the app's colours instead of the global stylesheet.
 */

export default function GlobalError({ error, retry }: { error: unknown; retry: () => void }) {
  const message = error instanceof Error && error.message ? error.message : "Unknown error";
  return (
    <html lang="en">
      <body style={{ margin: 0, minHeight: "100vh", background: "#05070d", color: "#e2e8f0", fontFamily: "system-ui, sans-serif" }}>
        <title>CausalTwin - error</title>
        <main style={{ maxWidth: 560, margin: "15vh auto 0", padding: "0 16px" }}>
          <h1 style={{ fontSize: 24, marginBottom: 12 }}>CausalTwin could not start this page</h1>
          <p style={{ color: "#fda4af", lineHeight: 1.5 }}>{message}</p>
          <p style={{ color: "#94a3b8", lineHeight: 1.5 }}>
            Reload the page. If the problem stays, check that the backend is running.
          </p>
          <button
            type="button"
            onClick={() => retry()}
            style={{ marginTop: 12, padding: "8px 16px", borderRadius: 8, border: "1px solid #22d3ee", background: "transparent", color: "#a5f3fc", cursor: "pointer" }}
          >
            Try again
          </button>
        </main>
      </body>
    </html>
  );
}
