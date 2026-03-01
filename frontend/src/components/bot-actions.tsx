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
import { LogsDialog } from "./logs-dialog";
import { NamePromptDialog } from "./name-prompt-dialog";
import { api } from "@/lib/api";
import type { Bot } from "@/lib/types";

export function BotActions({ bot, onAction }: { bot: Bot; onAction: () => void }) {
  const [loading, setLoading] = useState("");
  const [deleteOpen, setDeleteOpen] = useState(false);

  async function action(key: string, label: string, fn: () => Promise<unknown>) {
    setLoading(key);
    try {
      await fn();
      onAction();
      toast.success(`${label} completed`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : `${label} failed`);
    } finally {
      setLoading("");
    }
  }

  async function handleDelete() {
    setDeleteOpen(false);
    await action("delete", "Delete", () => api.deleteBot(bot.name));
  }

  return (
    <>
      <div className="flex flex-wrap gap-1.5">
        {bot.status !== "running" && bot.status !== "starting" && bot.status !== "unhealthy" ? (
          <Button
            size="sm"
            variant="secondary"
            disabled={!!loading}
            onClick={() => action("start", "Start", () => api.startBot(bot.name))}
          >
            {loading === "start" ? "..." : "Start"}
          </Button>
        ) : (
          <>
            <Button
              size="sm"
              variant="secondary"
              disabled={!!loading}
              onClick={() => action("stop", "Stop", () => api.stopBot(bot.name))}
            >
              {loading === "stop" ? "..." : "Stop"}
            </Button>
            <Button
              size="sm"
              variant="secondary"
              disabled={!!loading}
              onClick={() => action("restart", "Restart", () => api.restartBot(bot.name))}
            >
              {loading === "restart" ? "..." : "Restart"}
            </Button>
          </>
        )}
        <LogsDialog botName={bot.name} />
        <NamePromptDialog
          label="Duplicate"
          title={`Duplicate "${bot.name}"`}
          description="Enter a name for the duplicate bot."
          onSubmit={(newName) => action("duplicate", "Duplicate", () => api.duplicateBot(bot.name, newName))}
        />
        <NamePromptDialog
          label="Fork"
          title={`Fork "${bot.name}"`}
          description="Enter a name for the forked bot. Lineage will be tracked."
          onSubmit={(newName) => action("fork", "Fork", () => api.forkBot(bot.name, newName))}
        />
        <Button
          size="sm"
          variant="ghost"
          className="text-red-400 hover:text-red-300 hover:bg-red-500/10"
          disabled={!!loading}
          onClick={() => setDeleteOpen(true)}
        >
          {loading === "delete" ? "..." : "Delete"}
        </Button>
      </div>

      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete &ldquo;{bot.name}&rdquo;?</DialogTitle>
            <DialogDescription>
              This removes the container, network, and all configuration files. This action cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="secondary" onClick={() => setDeleteOpen(false)}>Cancel</Button>
            <Button variant="destructive" onClick={handleDelete}>Delete</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
