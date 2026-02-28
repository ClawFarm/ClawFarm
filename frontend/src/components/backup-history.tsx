"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type { Backup } from "@/lib/types";

interface Props {
  botName: string;
  backups: Backup[];
  onAction: () => void;
}

export function BackupHistory({ botName, backups, onAction }: Props) {
  const [loading, setLoading] = useState("");

  async function handleBackup() {
    setLoading("backup");
    try {
      await api.createBackup(botName);
      onAction();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Backup failed");
    } finally {
      setLoading("");
    }
  }

  async function handleRollback(timestamp: string) {
    if (!confirm(`Rollback "${botName}" to ${timestamp}? Current state will be auto-backed up first.`)) return;
    setLoading(timestamp);
    try {
      await api.rollback(botName, timestamp);
      onAction();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Rollback failed");
    } finally {
      setLoading("");
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">Backups</h3>
        <Button size="sm" onClick={handleBackup} disabled={!!loading}>
          {loading === "backup" ? "Creating..." : "Create Backup"}
        </Button>
      </div>
      {backups.length === 0 ? (
        <p className="text-sm text-muted-foreground">No backups yet.</p>
      ) : (
        <div className="space-y-1">
          {[...backups].reverse().map((b) => (
            <div
              key={b.timestamp}
              className="flex items-center justify-between rounded-sm bg-secondary px-3 py-1.5 text-xs border border-border"
            >
              <div>
                <span className="font-medium">{b.timestamp}</span>
                <span className="ml-2 text-muted-foreground">{b.label}</span>
              </div>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => handleRollback(b.timestamp)}
                disabled={!!loading}
              >
                {loading === b.timestamp ? "..." : "Rollback"}
              </Button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
