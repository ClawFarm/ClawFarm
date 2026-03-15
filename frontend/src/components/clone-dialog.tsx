"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

interface CloneDialogProps {
  sourceName: string;
  onClone: (newName: string, trackFork: boolean) => Promise<void>;
  trigger?: React.ReactNode;
}

export function CloneDialog({ sourceName, onClone, trigger }: CloneDialogProps) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [trackFork, setTrackFork] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setLoading(true);
    setError("");
    try {
      await onClone(name.trim(), trackFork);
      setOpen(false);
      setName("");
      setTrackFork(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Clone failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => { setOpen(v); if (!v) { setName(""); setError(""); setTrackFork(false); } }}>
      <DialogTrigger asChild>
        {trigger ?? <Button size="sm" variant="secondary">Clone</Button>}
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>Clone &ldquo;{sourceName}&rdquo;</DialogTitle>
            <DialogDescription>
              Create a copy of this agent with its personality and memories.
            </DialogDescription>
          </DialogHeader>
          <div className="py-4 space-y-3">
            <Input
              placeholder="New agent name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoFocus
            />
            <label className="flex items-center gap-2 text-sm text-muted-foreground cursor-pointer">
              <input
                type="checkbox"
                checked={trackFork}
                onChange={(e) => setTrackFork(e.target.checked)}
                className="rounded border-border"
              />
              Track as fork (records lineage)
            </label>
            {error && <p className="text-sm text-red-400">{error}</p>}
          </div>
          <DialogFooter>
            <Button type="button" variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
            <Button type="submit" disabled={loading || !name.trim()}>
              {loading ? "Cloning..." : "Clone"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
