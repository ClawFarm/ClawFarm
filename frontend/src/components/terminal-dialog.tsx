"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import "@xterm/xterm/css/xterm.css";

type Status = "idle" | "connecting" | "connected" | "disconnected" | "error";

function toBase64(str: string): string {
  return btoa(
    Array.from(new TextEncoder().encode(str), (b) => String.fromCharCode(b)).join("")
  );
}

export function TerminalDialog({ botName, trigger }: { botName: string; trigger?: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<Status>("idle");
  const termRef = useRef<HTMLDivElement>(null);
  const terminalRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const cleanupRef = useRef<(() => void) | null>(null);

  const connect = useCallback(() => {
    const container = termRef.current;
    if (!container) return;

    // Tear down previous connection (e.g. on reconnect)
    cleanupRef.current?.();
    cleanupRef.current = null;
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }

    // Create terminal if not already created
    if (!terminalRef.current) {
      const fit = new FitAddon();
      fitRef.current = fit;

      const term = new Terminal({
        cursorBlink: true,
        fontSize: 13,
        fontFamily: "var(--font-geist-mono), monospace",
        theme: {
          background: "#0a0a0a",
          foreground: "#ededed",
          cursor: "#ededed",
          selectionBackground: "#3a3a3a",
          black: "#0a0a0a",
          red: "#e5484d",
          green: "#2b8a3e",
          yellow: "#f5a623",
          blue: "#3b82f6",
          magenta: "#a855f7",
          cyan: "#06b6d4",
          white: "#ededed",
          brightBlack: "#888888",
          brightRed: "#e5484d",
          brightGreen: "#2b8a3e",
          brightYellow: "#f5a623",
          brightBlue: "#3b82f6",
          brightMagenta: "#a855f7",
          brightCyan: "#06b6d4",
          brightWhite: "#ffffff",
        },
        allowProposedApi: true,
      });

      term.loadAddon(fit);
      term.loadAddon(new WebLinksAddon());
      term.open(container);

      // Small delay to let the DOM settle before fitting
      requestAnimationFrame(() => {
        fit.fit();
      });

      terminalRef.current = term;
    } else {
      terminalRef.current.clear();
    }

    const term = terminalRef.current;

    // WebSocket URL — always use same origin. Caddy routes /api/* to dashboard.
    // Next.js rewrites don't proxy WebSocket upgrades, so this must go through Caddy.
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${proto}//${window.location.host}/api/bots/${encodeURIComponent(botName)}/terminal`;

    setStatus("connecting");
    term.writeln("\x1b[90mConnecting...\x1b[0m");

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      setStatus("connected");
      // Send initial terminal dimensions
      ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "data") {
          const bytes = Uint8Array.from(atob(msg.data), (c) => c.charCodeAt(0));
          term.write(bytes);
        } else if (msg.type === "error") {
          term.writeln(`\x1b[31m${msg.message}\x1b[0m`);
          setStatus("error");
        }
      } catch {
        // Non-JSON data — write raw
        term.write(event.data);
      }
    };

    ws.onclose = () => {
      setStatus((prev) => (prev === "error" ? "error" : "disconnected"));
      term.writeln("\r\n\x1b[90mDisconnected.\x1b[0m");
    };

    ws.onerror = () => {
      setStatus("error");
    };

    // Terminal input → WebSocket
    const dataDisposable = term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "data", data: toBase64(data) }));
      }
    });

    // Binary input (for special keys)
    const binaryDisposable = term.onBinary((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "data", data: toBase64(data) }));
      }
    });

    // Resize handling
    const resizeDisposable = term.onResize(({ cols, rows }) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "resize", cols, rows }));
      }
    });

    // Store cleanup so reconnect can tear down properly
    cleanupRef.current = () => {
      dataDisposable.dispose();
      binaryDisposable.dispose();
      resizeDisposable.dispose();
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
        ws.close();
      }
    };
  }, [botName]);

  function handleOpenChange(next: boolean) {
    setOpen(next);
    if (!next) setStatus("idle");
  }

  // Connect when dialog opens, disconnect when it closes
  useEffect(() => {
    if (!open) {
      // Tear down listeners + WebSocket
      cleanupRef.current?.();
      cleanupRef.current = null;
      wsRef.current = null;
      // Dispose terminal on close
      if (terminalRef.current) {
        terminalRef.current.dispose();
        terminalRef.current = null;
        fitRef.current = null;
      }
      return;
    }

    // Small delay to let dialog DOM render before mounting xterm
    const timer = setTimeout(() => {
      connect();
    }, 50);

    return () => clearTimeout(timer);
  }, [open, connect]);

  // Handle window resize → refit terminal
  useEffect(() => {
    if (!open) return;

    const handleResize = () => {
      fitRef.current?.fit();
    };

    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [open]);

  const statusLabel =
    status === "connecting" ? "Connecting..." :
    status === "connected" ? "Connected" :
    status === "disconnected" ? "Disconnected" :
    status === "error" ? "Error" : "";

  const statusDot =
    status === "connected" ? "bg-green-500" :
    status === "disconnected" ? "bg-neutral-500" :
    status === "error" ? "bg-red-500" :
    status === "connecting" ? "bg-yellow-500 animate-pulse" : "";

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        {trigger ?? (
          <Button size="sm" variant="secondary">
            Terminal
          </Button>
        )}
      </DialogTrigger>
      <DialogContent
        className="max-w-[calc(100vw-2rem)] w-full sm:max-w-5xl h-[85vh] flex flex-col p-0 gap-0 overflow-hidden"
        showCloseButton={false}
      >
        <DialogHeader className="flex flex-row items-center justify-between px-4 py-2 border-b border-border shrink-0">
          <div className="flex items-center gap-3">
            <DialogTitle className="text-sm font-medium">
              Terminal — {botName}
            </DialogTitle>
            <DialogDescription className="sr-only">
              Interactive terminal session for {botName}
            </DialogDescription>
            {statusLabel && (
              <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <span className={`inline-block w-1.5 h-1.5 rounded-full ${statusDot}`} />
                {statusLabel}
              </div>
            )}
          </div>
          <div className="flex items-center gap-1.5">
            {(status === "disconnected" || status === "error") && (
              <Button
                size="sm"
                variant="secondary"
                className="h-6 text-xs px-2"
                onClick={() => connect()}
              >
                Reconnect
              </Button>
            )}
            <Button
              size="sm"
              variant="ghost"
              className="h-6 w-6 p-0 text-muted-foreground hover:text-foreground"
              onClick={() => handleOpenChange(false)}
              aria-label="Close terminal"
            >
              &times;
            </Button>
          </div>
        </DialogHeader>
        <div
          ref={termRef}
          className="flex-1 min-h-0"
          style={{ backgroundColor: "#0a0a0a" }}
        />
      </DialogContent>
    </Dialog>
  );
}
