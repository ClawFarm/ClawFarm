"use client";

import Link from "next/link";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { BotActions } from "./bot-actions";
import { statusColor, formatBytes, botUiUrl } from "@/lib/format";
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
                  : bot.status === "exited"
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
            <a
              href={botUiUrl(bot, config?.portal_url)}
              target="_blank"
              rel="noopener noreferrer"
              className="text-[10px] font-medium text-muted-foreground hover:text-foreground transition-colors uppercase tracking-wide"
              onClick={() => {
                api.approveDevices(bot.name).catch(() => {});
              }}
            >
              Open UI
            </a>
          </div>
        </div>
      </CardHeader>
      <CardContent className="px-4 pb-4 space-y-3">
        <div className="flex items-center gap-4 flex-wrap">
          <MetaItem label={bot.ui_path ? "Path" : "Port"} value={bot.ui_path || `:${bot.port}`} />
          <MetaItem label="Storage" value={formatBytes(bot.storage_bytes)} />
          {bot.backup_count > 0 && (
            <MetaItem
              label="Backups"
              value={String(bot.backup_count)}
            />
          )}
          {bot.cron_jobs.length > 0 && (
            <MetaItem
              label="Cron"
              value={String(bot.cron_jobs.length)}
            />
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
