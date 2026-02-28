"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { api } from "@/lib/api";

export function LogsDialog({ botName }: { botName: string }) {
  const [logs, setLogs] = useState("");
  const [loading, setLoading] = useState(false);

  async function fetchLogs() {
    setLoading(true);
    setLogs("Loading...");
    try {
      const data = await api.getLogs(botName);
      setLogs(data.logs || "(no logs)");
    } catch {
      setLogs("Failed to fetch logs.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button size="sm" variant="secondary" onClick={fetchLogs}>
          Logs
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-3xl max-h-[80vh] flex flex-col">
        <DialogHeader>
          <DialogTitle>Logs — {botName}</DialogTitle>
        </DialogHeader>
        <div className="flex justify-end">
          <Button size="sm" variant="secondary" onClick={fetchLogs} disabled={loading}>
            {loading ? "Loading..." : "Refresh"}
          </Button>
        </div>
        <div className="flex-1 overflow-auto rounded bg-background p-3 border border-border">
          <pre className="text-xs whitespace-pre-wrap break-words text-muted-foreground">
            {logs}
          </pre>
        </div>
      </DialogContent>
    </Dialog>
  );
}
