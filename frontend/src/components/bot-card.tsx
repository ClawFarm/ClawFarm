"use client";

import { useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { LogsDialog } from "./logs-dialog";
import { TerminalDialog } from "./terminal-dialog";
import { CloneDialog } from "./clone-dialog";
import { Sparkline } from "./sparkline";
import { statusColor, formatTokens, formatUptime, botUiUrl } from "@/lib/format";
import { useConfig } from "@/hooks/use-config";
import { api } from "@/lib/api";
import type { Bot, SparklinePoint } from "@/lib/types";

interface BotCardProps {
  bot: Bot;
  sparkline: SparklinePoint[];
  sparklineLoading?: boolean;
  onAction: () => void;
}

export function BotCard({ bot, sparkline, sparklineLoading, onAction }: BotCardProps) {
  const config = useConfig();
  const [loading, setLoading] = useState("");
  const [deleteOpen, setDeleteOpen] = useState(false);
  const isRunning = bot.status === "running" || bot.status === "starting" || bot.status === "unhealthy";

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
      <Card className="bg-card border-border hover:border-muted-foreground/25 transition-colors">
        <CardHeader className="pb-2 pt-4 px-4">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2 min-w-0">
              <div
                className={`h-2 w-2 rounded-full shrink-0 ${
                  bot.status === "running"
                    ? "bg-emerald-400"
                    : bot.status === "starting"
                      ? "bg-amber-400 animate-pulse"
                      : bot.status === "exited" || bot.status === "unhealthy"
                        ? "bg-red-400"
                        : "bg-amber-400"
                }`}
              />
              <Link
                href={`/bots/${encodeURIComponent(bot.name)}`}
                className="text-sm font-semibold truncate hover:text-muted-foreground transition-colors"
              >
                {bot.name}
              </Link>
            </div>
            <Badge className={`text-[10px] px-1.5 py-0 ${statusColor(bot.status)}`}>
              {bot.status}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="px-4 pb-4 space-y-3">
          {/* Sparkline + tokens */}
          <div className="flex items-center gap-3 h-8">
            {sparklineLoading ? (
              <div className="w-[120px] h-[32px] rounded bg-secondary/50 animate-pulse shrink-0" />
            ) : (
              <Sparkline data={sparkline} width={120} height={32} className="shrink-0 opacity-70" />
            )}
            {bot.token_usage.total_tokens > 0 && (
              <span className="text-xs text-muted-foreground tabular-nums">
                {formatTokens(bot.token_usage.total_tokens)} tokens
              </span>
            )}
          </div>

          {/* Primary actions */}
          <div className="flex gap-1.5">
            {isRunning ? (
              <a
                href={botUiUrl(bot, config?.portal_url)}
                target="_blank"
                rel="noopener noreferrer"
                className="flex-1"
                onClick={() => { api.approveDevices(bot.name).catch(() => {}); }}
              >
                <Button size="sm" variant="secondary" className="w-full text-xs">
                  Open UI
                </Button>
              </a>
            ) : (
              <Button size="sm" variant="secondary" className="flex-1 text-xs" disabled>
                Open UI
              </Button>
            )}
            <TerminalDialog
              botName={bot.name}
              trigger={
                <Button size="sm" variant="secondary" className="flex-1 text-xs" disabled={!isRunning}>
                  Terminal
                </Button>
              }
            />
            <LogsDialog botName={bot.name} />
          </div>

          {/* Footer: meta + overflow */}
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span className="truncate">
              {bot.status === "running" && bot.uptime_seconds > 0
                ? `${formatUptime(bot.uptime_seconds)} up`
                : bot.status}
              {bot.template && ` · ${bot.template}`}
            </span>

            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button size="sm" variant="ghost" className="h-6 w-6 p-0 text-muted-foreground">
                  &#8943;
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {!isRunning ? (
                  <DropdownMenuItem
                    disabled={!!loading}
                    onClick={() => action("start", "Start", () => api.startBot(bot.name))}
                  >
                    {loading === "start" ? "Starting..." : "Start"}
                  </DropdownMenuItem>
                ) : (
                  <>
                    <DropdownMenuItem
                      disabled={!!loading}
                      onClick={() => action("stop", "Stop", () => api.stopBot(bot.name))}
                    >
                      {loading === "stop" ? "Stopping..." : "Stop"}
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      disabled={!!loading}
                      onClick={() => action("restart", "Restart", () => api.restartBot(bot.name))}
                    >
                      {loading === "restart" ? "Restarting..." : "Restart"}
                    </DropdownMenuItem>
                  </>
                )}
                <DropdownMenuSeparator />
                <CloneDialog
                  sourceName={bot.name}
                  onClone={async (newName, trackFork) => {
                    await api.cloneBot(bot.name, newName, trackFork);
                    onAction();
                    toast.success("Clone completed");
                  }}
                  trigger={
                    <DropdownMenuItem onSelect={(e) => e.preventDefault()}>
                      Clone
                    </DropdownMenuItem>
                  }
                />
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  className="text-red-400 focus:text-red-400"
                  disabled={!!loading}
                  onClick={() => setDeleteOpen(true)}
                >
                  {loading === "delete" ? "Deleting..." : "Delete"}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </CardContent>
      </Card>

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
