"use client";

import Link from "next/link";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { BotActions } from "./bot-actions";
import { statusColor, formatBytes, formatTokens, formatUptime, botUiUrl } from "@/lib/format";
import { useConfig } from "@/hooks/use-config";
import { api } from "@/lib/api";
import type { Bot } from "@/lib/types";

function MetaItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</span>
      <span className="text-xs font-medium tabular-nums">{value}</span>
    </div>
  );
}

export function BotCard({ bot, onAction }: { bot: Bot; onAction: () => void }) {
  const config = useConfig();

  return (
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
          <div className="flex items-center gap-2 shrink-0">
            <Badge className={`text-[10px] px-1.5 py-0 ${statusColor(bot.status)}`}>
              {bot.status}
            </Badge>
            {bot.status === "running" && (
              <a
                href={botUiUrl(bot, config?.portal_url)}
                target="_blank"
                rel="noopener noreferrer"
                onClick={() => { api.approveDevices(bot.name).catch(() => {}); }}
              >
                <Button size="xs" variant="secondary" className="text-[10px] h-6">
                  Open UI
                </Button>
              </a>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent className="px-4 pb-4 space-y-3">
        <div className="flex items-center gap-4 flex-wrap">
          {bot.status === "running" && bot.uptime_seconds > 0 && (
            <MetaItem label="Uptime" value={formatUptime(bot.uptime_seconds)} />
          )}
          {bot.token_usage.total_tokens > 0 && (
            <MetaItem label="Tokens" value={formatTokens(bot.token_usage.total_tokens)} />
          )}
          <MetaItem label="Storage" value={formatBytes(bot.storage_bytes)} />
          {bot.backup_count > 0 && (
            <MetaItem label="Backups" value={String(bot.backup_count)} />
          )}
          {bot.cron_jobs.length > 0 && (
            <MetaItem label="Cron" value={String(bot.cron_jobs.length)} />
          )}
          {bot.template && bot.template !== "default" && (
            <MetaItem label="Template" value={bot.template} />
          )}
          {bot.created_by && (
            <MetaItem label="Creator" value={bot.created_by} />
          )}
          {bot.forked_from && (
            <MetaItem label="Fork of" value={bot.forked_from} />
          )}
        </div>
        <BotActions bot={bot} onAction={onAction} />
      </CardContent>
    </Card>
  );
}
