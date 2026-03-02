"use client";

export default function GlobalError({
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en" className="dark">
      <body
        style={{
          fontFamily: "system-ui, -apple-system, sans-serif",
          backgroundColor: "#0a0a0a",
          color: "#ededed",
          margin: 0,
        }}
      >
        <div
          style={{
            minHeight: "100vh",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: "1rem",
          }}
        >
          <div style={{ textAlign: "center" }}>
            <p
              style={{
                fontSize: "3.75rem",
                fontWeight: 700,
                color: "rgba(136,136,136,0.5)",
                margin: "0 0 1rem",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              500
            </p>
            <h1 style={{ fontSize: "1.125rem", fontWeight: 600, margin: "0 0 0.5rem" }}>
              Something went wrong
            </h1>
            <p style={{ fontSize: "0.875rem", color: "#888888", margin: "0 0 1.5rem" }}>
              A critical error occurred. Please try again.
            </p>
            <button
              onClick={reset}
              style={{
                padding: "0.375rem 0.75rem",
                fontSize: "0.875rem",
                fontWeight: 500,
                backgroundColor: "#1a1a1a",
                color: "#ededed",
                border: "1px solid #2a2a2a",
                borderRadius: "0.375rem",
                cursor: "pointer",
              }}
            >
              Try again
            </button>
          </div>
        </div>
      </body>
    </html>
  );
}
