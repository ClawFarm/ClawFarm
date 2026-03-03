"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import type { Template } from "@/lib/types";

export function CreateBotForm({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState("");
  const [soul, setSoul] = useState("");
  const [template, setTemplate] = useState("default");
  const [templates, setTemplates] = useState<Template[]>([]);
  const [soulCustomized, setSoulCustomized] = useState(false);
  const [networkIsolation, setNetworkIsolation] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const [showConfig, setShowConfig] = useState(false);

  // Pre-fetch templates on mount so they're ready when the form opens
  useEffect(() => {
    api.listTemplates().then(setTemplates).catch(() => {});
  }, []);

  function handleTemplateChange(name: string) {
    setTemplate(name);
    if (!soulCustomized) {
      const t = templates.find((t) => t.name === name);
      if (t?.soul_preview) setSoul(t.soul_preview);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setLoading(true);
    setError("");
    try {
      await api.createBot({ name, soul: soul || undefined, template, network_isolation: networkIsolation });
      const sanitized = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/-{2,}/g, "-").replace(/^-|-$/g, "").slice(0, 48);
      if (sanitized !== name) {
        toast.info(`Created as "${sanitized}"`);
      }
      setName("");
      setSoul("");
      setTemplate("default");
      setSoulCustomized(false);
      setNetworkIsolation(true);
      setShowConfig(false);
      setOpen(false);
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create bot");
    } finally {
      setLoading(false);
    }
  }

  const selectedTemplate = templates.find((t) => t.name === template);

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
          className="rounded-lg border border-border bg-card p-4 space-y-3 max-w-2xl"
        >
          <div className="text-xs font-medium uppercase tracking-wider text-muted-foreground mb-1">
            New Agent
          </div>
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">Template</label>
            {templates.length === 0 ? (
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                {[0, 1, 2].map((i) => (
                  <div key={i} className="h-20 rounded-lg bg-secondary animate-pulse" />
                ))}
              </div>
            ) : (
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                {templates.map((t) => (
                  <button
                    key={t.name}
                    type="button"
                    onClick={() => handleTemplateChange(t.name)}
                    className={cn(
                      "rounded-lg border p-3 text-left transition-colors",
                      template === t.name
                        ? "border-primary bg-primary/5 ring-1 ring-primary/20"
                        : "border-border bg-card/50 hover:border-muted-foreground/30"
                    )}
                  >
                    <div className="text-sm font-medium truncate">{t.name}</div>
                    {t.description && (
                      <div className="text-xs text-muted-foreground mt-0.5 line-clamp-2">
                        {t.description}
                      </div>
                    )}
                    {t.env_hint && (
                      <div className="text-[10px] text-muted-foreground/60 mt-1 truncate">
                        {t.env_hint}
                      </div>
                    )}
                  </button>
                ))}
              </div>
            )}
            {selectedTemplate?.missing_vars && selectedTemplate.missing_vars.length > 0 && (
              <p className="text-xs text-amber-500 mt-1.5">
                Missing env: {selectedTemplate.missing_vars.join(", ")}
              </p>
            )}
            {selectedTemplate?.config_preview && (
              <div>
                <button
                  type="button"
                  onClick={() => setShowConfig(!showConfig)}
                  className="text-xs text-muted-foreground hover:text-foreground transition-colors flex items-center gap-1 mt-1"
                >
                  <span className="text-[10px]">{showConfig ? "▼" : "▶"}</span>
                  Config preview
                </button>
                {showConfig && (
                  <pre className="mt-1.5 rounded-md bg-secondary/50 border border-border p-3 text-xs text-muted-foreground overflow-x-auto max-h-64 overflow-y-auto">
                    {selectedTemplate.config_preview}
                  </pre>
                )}
              </div>
            )}
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
            onChange={(e) => {
              setSoul(e.target.value);
              setSoulCustomized(true);
            }}
            rows={3}
          />
          <label className="flex items-center gap-3 cursor-pointer group">
            <button
              type="button"
              role="switch"
              aria-checked={networkIsolation}
              onClick={() => setNetworkIsolation(!networkIsolation)}
              className={cn(
                "relative inline-flex h-5 w-9 shrink-0 rounded-full border-2 border-transparent transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
                networkIsolation ? "bg-emerald-500" : "bg-secondary",
              )}
            >
              <span
                className={cn(
                  "pointer-events-none block h-4 w-4 rounded-full bg-white shadow-sm transition-transform",
                  networkIsolation ? "translate-x-4" : "translate-x-0",
                )}
              />
            </button>
            <div className="flex flex-col">
              <span className="text-sm text-foreground leading-tight">Network isolation</span>
              <span className="text-xs text-muted-foreground/60 leading-tight">Block LAN access from this agent</span>
            </div>
          </label>
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
                setShowConfig(false);
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
