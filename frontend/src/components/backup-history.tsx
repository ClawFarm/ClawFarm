"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { api } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import type { Backup } from "@/lib/types";

interface Props {
  botName: string;
  backups: Backup[];
  onAction: () => void;
}

export function BackupHistory({ botName, backups, onAction }: Props) {
  const [loading, setLoading] = useState("");
  const [rollbackTarget, setRollbackTarget] = useState<string | null>(null);

  async function handleBackup() {
    setLoading("backup");
    try {
      await api.createBackup(botName);
      onAction();
      toast.success("Backup created");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Backup failed");
    } finally {
      setLoading("");
    }
  }

  async function handleRollback() {
    if (!rollbackTarget) return;
    const ts = rollbackTarget;
    setRollbackTarget(null);
    setLoading(ts);
    try {
      await api.rollback(botName, ts);
      onAction();
      toast.success(`Rolled back to ${ts}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Rollback failed");
    } finally {
      setLoading("");
    }
  }

  return (
    <>
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
                  {b.size_bytes != null && (
                    <span className="ml-2 text-muted-foreground">{formatBytes(b.size_bytes)}</span>
                  )}
                </div>
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => setRollbackTarget(b.timestamp)}
                  disabled={!!loading}
                >
                  {loading === b.timestamp ? "..." : "Rollback"}
                </Button>
              </div>
            ))}
          </div>
        )}
      </div>

      <Dialog open={!!rollbackTarget} onOpenChange={(open) => { if (!open) setRollbackTarget(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Rollback to {rollbackTarget}?</DialogTitle>
            <DialogDescription>
              Current state will be auto-backed up first. The bot will be restored to this snapshot.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="secondary" onClick={() => setRollbackTarget(null)}>Cancel</Button>
            <Button onClick={handleRollback}>Rollback</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
