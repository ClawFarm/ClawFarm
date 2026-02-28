"use client";

import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { BotActions } from "./bot-actions";
import { statusColor } from "@/lib/format";
import { api } from "@/lib/api";
import type { Bot } from "@/lib/types";

export function BotCard({ bot, onAction }: { bot: Bot; onAction: () => void }) {
  return (
    <Card className="bg-card border-border">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <Link href={`/bots/${encodeURIComponent(bot.name)}`}>
            <CardTitle className="text-sm hover:text-primary hover:underline cursor-pointer">
              {bot.name}
            </CardTitle>
          </Link>
          <Badge className={`text-xs ${statusColor(bot.status)}`}>{bot.status}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          <span>:{bot.port}</span>
          <a
            href={`http://${typeof window !== "undefined" ? window.location.hostname : "localhost"}:${bot.port}`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary hover:underline"
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
        </div>
        <BotActions bot={bot} onAction={onAction} />
      </CardContent>
    </Card>
  );
}
