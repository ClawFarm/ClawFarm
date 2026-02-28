"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { LogsDialog } from "./logs-dialog";
import { NamePromptDialog } from "./name-prompt-dialog";
import type { Bot } from "@/lib/types";

export function BotActions({ bot, onAction }: { bot: Bot; onAction: () => void }) {
  const [loading, setLoading] = useState("");

  async function action(key: string, fn: () => Promise<unknown>) {
    setLoading(key);
    try {
      await fn();
      onAction();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Action failed");
    } finally {
      setLoading("");
    }
  }

  async function handleDelete() {
    if (!confirm(`Delete bot "${bot.name}"? This removes the container, network, and config.`)) return;
    await action("delete", () => api.deleteBot(bot.name));
  }

  return (
    <div className="flex flex-wrap gap-1.5">
      <Button
        size="sm"
        variant="secondary"
        disabled={!!loading}
        onClick={() => action("start", () => api.startBot(bot.name))}
      >
        {loading === "start" ? "..." : "Start"}
      </Button>
      <Button
        size="sm"
        variant="secondary"
        disabled={!!loading}
        onClick={() => action("stop", () => api.stopBot(bot.name))}
      >
        {loading === "stop" ? "..." : "Stop"}
      </Button>
      <Button
        size="sm"
        variant="secondary"
        disabled={!!loading}
        onClick={() => action("restart", () => api.restartBot(bot.name))}
      >
        {loading === "restart" ? "..." : "Restart"}
      </Button>
      <LogsDialog botName={bot.name} />
      <NamePromptDialog
        label="Duplicate"
        title={`Duplicate "${bot.name}"`}
        description="Enter a name for the duplicate bot."
        onSubmit={(newName) => action("duplicate", () => api.duplicateBot(bot.name, newName))}
      />
      <NamePromptDialog
        label="Fork"
        title={`Fork "${bot.name}"`}
        description="Enter a name for the forked bot. Lineage will be tracked."
        onSubmit={(newName) => action("fork", () => api.forkBot(bot.name, newName))}
      />
      <Button
        size="sm"
        variant="destructive"
        disabled={!!loading}
        onClick={handleDelete}
      >
        {loading === "delete" ? "..." : "Delete"}
      </Button>
    </div>
  );
}
