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
    <div className="border-b border-border px-4 py-3">
      <button
        onClick={() => setOpen(!open)}
        className="text-sm font-medium text-muted-foreground hover:text-foreground mb-2"
      >
        {open ? "− Hide form" : "+ New Bot"}
      </button>
      {open && (
        <form onSubmit={handleSubmit} className="space-y-3 max-w-xl">
          <Input
            placeholder="Bot name (e.g. research-bot)"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <Textarea
            placeholder="SOUL.md — custom personality (optional)"
            value={soul}
            onChange={(e) => setSoul(e.target.value)}
            rows={3}
          />
          {error && <p className="text-sm text-destructive">{error}</p>}
          <Button type="submit" disabled={loading}>
            {loading ? "Creating..." : "Create Bot"}
          </Button>
        </form>
      )}
    </div>
  );
}
