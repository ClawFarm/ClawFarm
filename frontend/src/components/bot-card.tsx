"use client";

import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { BotActions } from "./bot-actions";
import { statusColor, formatBytes, botUiUrl } from "@/lib/format";
import { useConfig } from "@/hooks/use-config";
import { api } from "@/lib/api";
import type { Bot } from "@/lib/types";

export function BotCard({ bot, onAction }: { bot: Bot; onAction: () => void }) {
  const config = useConfig();

  return (
    <Card className="bg-card border-border">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <Link href={`/bots/${encodeURIComponent(bot.name)}`}>
            <CardTitle className="text-sm hover:opacity-70 transition-opacity cursor-pointer">
              {bot.name}
            </CardTitle>
          </Link>
          <Badge className={`text-xs ${statusColor(bot.status)}`}>{bot.status}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-center gap-3 text-xs text-muted-foreground flex-wrap">
          <span>:{bot.port}</span>
          <a
            href={botUiUrl(bot.port, config?.portal_url)}
            target="_blank"
            rel="noopener noreferrer"
            className="text-foreground hover:opacity-70 transition-opacity"
            onClick={() => { api.approveDevices(bot.name).catch(() => {}); }}
          >
            Open UI
          </a>
          {bot.forked_from && (
            <span className="text-muted-foreground">
              forked from <span className="text-foreground">{bot.forked_from}</span>
            </span>
          )}
          {bot.backup_count > 0 && (
            <span>{bot.backup_count} backup{bot.backup_count > 1 ? "s" : ""}</span>
          )}
          <span>{formatBytes(bot.storage_bytes)}</span>
          {bot.cron_jobs.length > 0 && (
            <span>{bot.cron_jobs.length} cron</span>
          )}
        </div>
        <BotActions bot={bot} onAction={onAction} />
      </CardContent>
    </Card>
  );
}
