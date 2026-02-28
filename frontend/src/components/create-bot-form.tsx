"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";

export function CreateBotForm({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState("");
  const [soul, setSoul] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setLoading(true);
    setError("");
    try {
      await api.createBot({ name, soul: soul || undefined });
      setName("");
      setSoul("");
      setOpen(false);
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create bot");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="px-6 pt-5">
      {!open ? (
        <button
          onClick={() => setOpen(true)}
          className="rounded-lg border border-dashed border-border hover:border-muted-foreground/50 bg-card/50 px-4 py-3 w-full text-left text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          + Create new agent
        </button>
      ) : (
        <form
          onSubmit={handleSubmit}
          className="rounded-lg border border-border bg-card p-4 space-y-3 max-w-xl"
        >
          <div className="text-xs font-medium uppercase tracking-wider text-muted-foreground mb-1">
            New Agent
          </div>
          <Input
            placeholder="Agent name (e.g. research-bot)"
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoFocus
          />
          <Textarea
            placeholder="SOUL.md — custom personality (optional)"
            value={soul}
            onChange={(e) => setSoul(e.target.value)}
            rows={3}
          />
          {error && <p className="text-sm text-destructive">{error}</p>}
          <div className="flex gap-2">
            <Button type="submit" disabled={loading} size="sm">
              {loading ? "Creating..." : "Create"}
            </Button>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => {
                setOpen(false);
                setError("");
              }}
            >
              Cancel
            </Button>
          </div>
        </form>
      )}
    </div>
  );
}
